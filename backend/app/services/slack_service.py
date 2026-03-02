"""Slack OAuth token exchange, storage, and messaging service."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from sqlmodel import col, select

from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import utcnow
from app.db import crud
from app.models.board_slack_connection import (
    BoardSlackConnection,
    decrypt_token,
    encrypt_token,
)

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession

logger = get_logger(__name__)


async def exchange_code_for_tokens(code: str) -> dict[str, Any]:
    """Exchange an OAuth authorization code for Slack tokens."""
    client = WebClient()
    response = client.oauth_v2_access(
        client_id=settings.slack_client_id,
        client_secret=settings.slack_client_secret,
        code=code,
        redirect_uri=settings.slack_oauth_redirect_uri or None,
    )
    return dict(response.data) if response.data else {}


async def store_connection(
    session: AsyncSession,
    *,
    board_id: UUID,
    oauth_response: dict[str, Any],
    channel_id: str,
    channel_name: str | None = None,
) -> BoardSlackConnection:
    """Encrypt and store a Slack connection for a board."""
    bot_token = oauth_response.get("access_token", "")
    team_info = oauth_response.get("team", {})
    authed_user = oauth_response.get("authed_user", {})

    connection = BoardSlackConnection(
        board_id=board_id,
        slack_team_id=team_info.get("id", ""),
        slack_team_name=team_info.get("name"),
        slack_channel_id=channel_id,
        slack_channel_name=channel_name,
        bot_token_encrypted=encrypt_token(bot_token),
        bot_user_id=oauth_response.get("bot_user_id"),
        installer_user_id=authed_user.get("id"),
        scopes=oauth_response.get("scope", ""),
        is_active=True,
    )
    await crud.save(session, connection)
    logger.info(
        "slack.connection.stored",
        extra={
            "board_id": str(board_id),
            "team_id": connection.slack_team_id,
            "channel_id": channel_id,
        },
    )
    return connection


def create_channel(
    bot_token: str,
    channel_name: str,
) -> dict[str, Any] | None:
    """Create a new Slack channel and return its info, or return existing if name is taken."""
    try:
        client = WebClient(token=bot_token)
        response = client.conversations_create(name=channel_name)
        channel = response.data.get("channel", {}) if response.data else {}
        channel_id = channel.get("id", "")
        logger.info(
            "slack.channel.created",
            extra={"channel_id": channel_id, "channel_name": channel_name},
        )
        return {"id": channel_id, "name": channel.get("name", channel_name)}
    except SlackApiError as exc:
        if exc.response.get("error") == "name_taken":
            # Channel already exists — find it and join it
            logger.info(
                "slack.channel.already_exists",
                extra={"channel_name": channel_name},
            )
            try:
                existing = _find_channel_by_name(client, channel_name)
                if existing:
                    _join_channel(client, existing["id"])
                    return existing
            except SlackApiError:
                pass
        logger.error(
            "slack.channel.create_failed",
            extra={"channel_name": channel_name, "error": str(exc)},
        )
        return None


def _join_channel(client: WebClient, channel_id: str) -> None:
    """Join a channel so the bot can post messages."""
    try:
        client.conversations_join(channel=channel_id)
        logger.info("slack.channel.joined", extra={"channel_id": channel_id})
    except SlackApiError as exc:
        if exc.response.get("error") != "already_in_channel":
            logger.warning(
                "slack.channel.join_failed",
                extra={"channel_id": channel_id, "error": str(exc)},
            )


def _find_channel_by_name(client: WebClient, name: str) -> dict[str, Any] | None:
    """Find a channel by exact name."""
    cursor = None
    while True:
        response = client.conversations_list(
            types="public_channel,private_channel",
            limit=200,
            cursor=cursor,
        )
        data = response.data if response.data else {}
        for ch in data.get("channels", []):
            if ch.get("name") == name:
                return {"id": ch["id"], "name": ch["name"]}
        cursor = data.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return None


async def get_connection(
    session: AsyncSession,
    board_id: UUID,
) -> BoardSlackConnection | None:
    """Get the Slack connection for a board."""
    return (
        await session.exec(
            select(BoardSlackConnection).where(
                col(BoardSlackConnection.board_id) == board_id,
            )
        )
    ).first()


async def get_active_connection(
    session: AsyncSession,
    board_id: UUID,
) -> BoardSlackConnection | None:
    """Get the active Slack connection for a board."""
    return (
        await session.exec(
            select(BoardSlackConnection)
            .where(col(BoardSlackConnection.board_id) == board_id)
            .where(col(BoardSlackConnection.is_active).is_(True))
        )
    ).first()


async def get_connection_by_channel(
    session: AsyncSession,
    *,
    team_id: str,
    channel_id: str,
) -> BoardSlackConnection | None:
    """Look up a Slack connection by team and channel IDs."""
    return (
        await session.exec(
            select(BoardSlackConnection)
            .where(col(BoardSlackConnection.slack_team_id) == team_id)
            .where(col(BoardSlackConnection.slack_channel_id) == channel_id)
            .where(col(BoardSlackConnection.is_active).is_(True))
        )
    ).first()


async def update_channel(
    session: AsyncSession,
    *,
    board_id: UUID,
    channel_id: str,
    channel_name: str | None = None,
) -> BoardSlackConnection | None:
    """Update the Slack channel for an existing board connection."""
    connection = await get_connection(session, board_id)
    if connection is None:
        return None
    connection.slack_channel_id = channel_id
    connection.slack_channel_name = channel_name
    connection.updated_at = utcnow()
    await crud.save(session, connection)
    return connection


def post_message(
    connection: BoardSlackConnection,
    text: str,
    *,
    thread_ts: str | None = None,
) -> dict[str, Any] | None:
    """Post a message to the connected Slack channel."""
    try:
        token = decrypt_token(connection.bot_token_encrypted)
        client = WebClient(token=token)
        response = client.chat_postMessage(
            channel=connection.slack_channel_id,
            text=text,
            thread_ts=thread_ts,
        )
        return dict(response.data) if response.data else None
    except SlackApiError as exc:
        # Auto-join and retry if bot is not in the channel
        if exc.response.get("error") == "not_in_channel":
            try:
                token = decrypt_token(connection.bot_token_encrypted)
                client = WebClient(token=token)
                client.conversations_join(channel=connection.slack_channel_id)
                response = client.chat_postMessage(
                    channel=connection.slack_channel_id,
                    text=text,
                    thread_ts=thread_ts,
                )
                return dict(response.data) if response.data else None
            except SlackApiError as retry_exc:
                logger.error(
                    "slack.post_message.retry_failed",
                    extra={
                        "board_id": str(connection.board_id),
                        "channel_id": connection.slack_channel_id,
                        "error": str(retry_exc),
                    },
                )
                return None
        logger.error(
            "slack.post_message.failed",
            extra={
                "board_id": str(connection.board_id),
                "channel_id": connection.slack_channel_id,
                "error": str(exc),
            },
        )
        if exc.response.get("error") in ("token_revoked", "invalid_auth", "account_inactive"):
            logger.warning(
                "slack.post_message.token_invalid",
                extra={"board_id": str(connection.board_id)},
            )
        return None


def list_channels(connection: BoardSlackConnection) -> list[dict[str, Any]]:
    """List available Slack channels using the bot token."""
    try:
        token = decrypt_token(connection.bot_token_encrypted)
        client = WebClient(token=token)
        channels: list[dict[str, Any]] = []
        cursor = None
        while True:
            response = client.conversations_list(
                types="public_channel,private_channel",
                limit=200,
                cursor=cursor,
            )
            data = response.data if response.data else {}
            for ch in data.get("channels", []):
                channels.append(
                    {
                        "id": ch["id"],
                        "name": ch.get("name", ""),
                        "is_private": ch.get("is_private", False),
                    }
                )
            cursor = data.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
        return channels
    except SlackApiError as exc:
        logger.error(
            "slack.list_channels.failed",
            extra={"error": str(exc)},
        )
        return []


async def revoke_connection(
    session: AsyncSession,
    board_id: UUID,
) -> bool:
    """Revoke Slack token and delete the board connection."""
    connection = await get_connection(session, board_id)
    if connection is None:
        return False

    try:
        token = decrypt_token(connection.bot_token_encrypted)
        client = WebClient(token=token)
        client.auth_revoke()
    except SlackApiError as exc:
        logger.warning(
            "slack.revoke.api_error",
            extra={"board_id": str(board_id), "error": str(exc)},
        )

    await session.delete(connection)
    await session.commit()
    logger.info("slack.connection.revoked", extra={"board_id": str(board_id)})
    return True


async def verify_token(connection: BoardSlackConnection) -> bool:
    """Verify a Slack bot token is still valid via auth.test."""
    try:
        token = decrypt_token(connection.bot_token_encrypted)
        client = WebClient(token=token)
        client.auth_test()
        return True
    except SlackApiError:
        return False
