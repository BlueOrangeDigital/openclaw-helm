"""Board Slack connection model with encrypted token storage."""

from __future__ import annotations

import os
from datetime import datetime
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
from sqlmodel import Field

from app.core.time import utcnow
from app.models.base import QueryModel

RUNTIME_ANNOTATION_TYPES = (datetime,)

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    """Return a cached Fernet instance using the ENCRYPTION_KEY env var."""
    global _fernet  # noqa: PLW0603
    if _fernet is None:
        key = os.environ.get("ENCRYPTION_KEY", "")
        if not key:
            msg = "ENCRYPTION_KEY environment variable is required for Slack token encryption."
            raise RuntimeError(msg)
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


def encrypt_token(plaintext: str) -> str:
    """Encrypt a Slack bot token and return the ciphertext as a UTF-8 string."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    """Decrypt a stored Slack bot token ciphertext back to plaintext."""
    return _get_fernet().decrypt(ciphertext.encode()).decode()


class BoardSlackConnection(QueryModel, table=True):
    """Per-board Slack workspace and channel connection with encrypted bot token."""

    __tablename__ = "board_slack_connections"  # pyright: ignore[reportAssignmentType]

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    board_id: UUID = Field(foreign_key="boards.id", index=True, unique=True)
    slack_team_id: str = Field(max_length=32, index=True)
    slack_team_name: str | None = Field(default=None, max_length=255)
    slack_channel_id: str = Field(max_length=32, index=True)
    slack_channel_name: str | None = Field(default=None, max_length=255)
    bot_token_encrypted: str
    bot_user_id: str | None = Field(default=None, max_length=32)
    installer_user_id: str | None = Field(default=None, max_length=32)
    scopes: str | None = None
    is_active: bool = Field(default=True, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
