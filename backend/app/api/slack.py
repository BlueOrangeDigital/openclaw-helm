"""Slack OAuth flow and board connection management endpoints."""

from __future__ import annotations

import urllib.parse
from typing import TYPE_CHECKING
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse

import re

from sqlmodel import col

from app.api.deps import get_board_for_user_read, get_board_for_user_write
from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.session import get_session
from app.models.agents import Agent
from app.schemas.common import OkResponse
from app.schemas.slack import (
    SlackChannelInfo,
    SlackChannelListResponse,
    SlackChannelSetRequest,
    SlackConnectionStatus,
    SlackDisconnectResponse,
)
from app.services import slack_service

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession

    from app.models.boards import Board

router = APIRouter(prefix="/slack", tags=["slack"])
SESSION_DEP = Depends(get_session)
BOARD_USER_READ_DEP = Depends(get_board_for_user_read)
BOARD_USER_WRITE_DEP = Depends(get_board_for_user_write)
logger = get_logger(__name__)

_STATE_JWT_ALGORITHM = "HS256"
_STATE_JWT_EXPIRY_SECONDS = 600  # 10 minutes


def _slack_configured() -> None:
    """Raise 503 if Slack credentials are not configured."""
    if not settings.slack_client_id or not settings.slack_client_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Slack integration is not configured.",
        )


def _build_state_token(board_id: UUID) -> str:
    """Build a signed JWT state parameter encoding the board_id."""
    payload = {
        "board_id": str(board_id),
        "iat": utcnow().timestamp(),
        "exp": utcnow().timestamp() + _STATE_JWT_EXPIRY_SECONDS,
    }
    return jwt.encode(payload, settings.slack_client_secret, algorithm=_STATE_JWT_ALGORITHM)


def _decode_state_token(state: str) -> UUID:
    """Decode and validate the JWT state parameter, returning the board_id."""
    try:
        payload = jwt.decode(
            state,
            settings.slack_client_secret,
            algorithms=[_STATE_JWT_ALGORITHM],
        )
        return UUID(payload["board_id"])
    except (jwt.InvalidTokenError, KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired state parameter.",
        ) from exc


@router.get("/oauth/authorize")
async def slack_oauth_authorize(
    board_id: UUID,
    session: AsyncSession = SESSION_DEP,
) -> RedirectResponse:
    """Generate Slack OAuth URL and redirect user to Slack authorization page."""
    _slack_configured()

    state = _build_state_token(board_id)
    scopes = "channels:history,channels:join,channels:manage,channels:read,chat:write,groups:history,groups:read,users:read"
    redirect_uri = settings.slack_oauth_redirect_uri
    if not redirect_uri:
        base = settings.base_url.rstrip("/")
        redirect_uri = f"{base}/api/v1/slack/oauth/callback"

    params = urllib.parse.urlencode(
        {
            "client_id": settings.slack_client_id,
            "scope": scopes,
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    authorize_url = f"https://slack.com/oauth/v2/authorize?{params}"
    logger.info(
        "slack.oauth.authorize",
        extra={"board_id": str(board_id)},
    )
    return RedirectResponse(url=authorize_url, status_code=status.HTTP_302_FOUND)


@router.get("/oauth/callback")
async def slack_oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    session: AsyncSession = SESSION_DEP,
) -> RedirectResponse:
    """Handle Slack OAuth callback — exchange code for tokens and store connection."""
    _slack_configured()

    if error:
        logger.warning("slack.oauth.callback.error", extra={"error": error})
        frontend_base = settings.cors_origins.split(",")[0].strip() if settings.cors_origins else ""
        return RedirectResponse(
            url=f"{frontend_base}/boards?slack_error={error}",
            status_code=status.HTTP_302_FOUND,
        )

    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing code or state parameter.",
        )

    board_id = _decode_state_token(state)
    oauth_response = await slack_service.exchange_code_for_tokens(code)

    if not oauth_response.get("ok"):
        logger.error(
            "slack.oauth.callback.exchange_failed",
            extra={"board_id": str(board_id), "error": oauth_response.get("error")},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Slack OAuth failed: {oauth_response.get('error', 'unknown')}",
        )

    # Check for existing connection and remove it before storing new one
    existing = await slack_service.get_connection(session, board_id)
    if existing is not None:
        await session.delete(existing)
        await session.flush()

    # Auto-create a Slack channel named after the board lead (e.g. "ava-helm")
    bot_token = oauth_response.get("access_token", "")
    channel_id = ""
    channel_name = ""

    lead = (
        await Agent.objects.filter_by(board_id=board_id)
        .filter(col(Agent.is_board_lead).is_(True))
        .first(session)
    )
    if lead and bot_token:
        # Slack channel names: lowercase, no spaces, max 80 chars
        slug = re.sub(r"[^a-z0-9-]", "", lead.name.lower().replace(" ", "-"))
        desired_name = f"{slug}-helm"[:80]
        created = slack_service.create_channel(bot_token, desired_name)
        if created:
            channel_id = created["id"]
            channel_name = created["name"]
            logger.info(
                "slack.oauth.auto_channel",
                extra={"board_id": str(board_id), "channel": channel_name},
            )

    # Fall back to incoming webhook channel if auto-create didn't work
    if not channel_id:
        incoming_webhook = oauth_response.get("incoming_webhook", {})
        channel_id = incoming_webhook.get("channel_id", "")
        channel_name = incoming_webhook.get("channel", "")

    await slack_service.store_connection(
        session,
        board_id=board_id,
        oauth_response=oauth_response,
        channel_id=channel_id,
        channel_name=channel_name,
    )

    frontend_base = settings.cors_origins.split(",")[0].strip() if settings.cors_origins else ""
    redirect_url = f"{frontend_base}/boards/{board_id}/edit?slack=connected"
    logger.info(
        "slack.oauth.callback.success",
        extra={"board_id": str(board_id)},
    )
    return RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)


@router.get(
    "/boards/{board_id}/status",
    response_model=SlackConnectionStatus,
)
async def get_slack_status(
    board: Board = BOARD_USER_READ_DEP,
    session: AsyncSession = SESSION_DEP,
) -> SlackConnectionStatus:
    """Return the Slack connection status for a board."""
    connection = await slack_service.get_connection(session, board.id)
    if connection is None:
        return SlackConnectionStatus(connected=False)
    return SlackConnectionStatus(
        connected=True,
        slack_team_id=connection.slack_team_id,
        slack_team_name=connection.slack_team_name,
        slack_channel_id=connection.slack_channel_id,
        slack_channel_name=connection.slack_channel_name,
        bot_user_id=connection.bot_user_id,
        is_active=connection.is_active,
        created_at=connection.created_at,
    )


@router.get(
    "/boards/{board_id}/channels",
    response_model=SlackChannelListResponse,
)
async def list_slack_channels(
    board: Board = BOARD_USER_READ_DEP,
    session: AsyncSession = SESSION_DEP,
) -> SlackChannelListResponse:
    """List available Slack channels for the connected workspace."""
    connection = await slack_service.get_connection(session, board.id)
    if connection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No Slack connection found for this board.",
        )
    raw_channels = slack_service.list_channels(connection)
    channels = [
        SlackChannelInfo(
            id=ch["id"],
            name=ch["name"],
            is_private=ch.get("is_private", False),
        )
        for ch in raw_channels
    ]
    return SlackChannelListResponse(channels=channels)


@router.post(
    "/boards/{board_id}/channel",
    response_model=SlackConnectionStatus,
)
async def set_slack_channel(
    payload: SlackChannelSetRequest,
    board: Board = BOARD_USER_WRITE_DEP,
    session: AsyncSession = SESSION_DEP,
) -> SlackConnectionStatus:
    """Set which Slack channel a board syncs with."""
    connection = await slack_service.update_channel(
        session,
        board_id=board.id,
        channel_id=payload.channel_id,
        channel_name=payload.channel_name,
    )
    if connection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No Slack connection found for this board.",
        )
    return SlackConnectionStatus(
        connected=True,
        slack_team_id=connection.slack_team_id,
        slack_team_name=connection.slack_team_name,
        slack_channel_id=connection.slack_channel_id,
        slack_channel_name=connection.slack_channel_name,
        bot_user_id=connection.bot_user_id,
        is_active=connection.is_active,
        created_at=connection.created_at,
    )


@router.delete(
    "/boards/{board_id}/disconnect",
    response_model=SlackDisconnectResponse,
)
async def disconnect_slack(
    board: Board = BOARD_USER_WRITE_DEP,
    session: AsyncSession = SESSION_DEP,
) -> SlackDisconnectResponse:
    """Revoke Slack token and delete the board connection."""
    revoked = await slack_service.revoke_connection(session, board.id)
    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No Slack connection found for this board.",
        )
    return SlackDisconnectResponse(board_id=board.id)
