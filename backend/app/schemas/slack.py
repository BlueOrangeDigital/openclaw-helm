"""Schemas for Slack OAuth and connection status endpoints."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlmodel import SQLModel


class SlackConnectionStatus(SQLModel):
    """Current Slack connection state for a board."""

    connected: bool = False
    slack_team_id: str | None = None
    slack_team_name: str | None = None
    slack_channel_id: str | None = None
    slack_channel_name: str | None = None
    bot_user_id: str | None = None
    is_active: bool = False
    created_at: datetime | None = None


class SlackChannelSetRequest(SQLModel):
    """Request to set which Slack channel a board syncs with."""

    channel_id: str
    channel_name: str | None = None


class SlackChannelInfo(SQLModel):
    """Slack channel info for the channel picker."""

    id: str
    name: str
    is_private: bool = False


class SlackChannelListResponse(SQLModel):
    """Response for listing available Slack channels."""

    channels: list[SlackChannelInfo] = []


class SlackDisconnectResponse(SQLModel):
    """Response after disconnecting Slack."""

    ok: bool = True
    board_id: UUID
