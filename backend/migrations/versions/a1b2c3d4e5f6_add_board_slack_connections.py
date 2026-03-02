"""Add board_slack_connections table for Slack OAuth integration.

Revision ID: a1b2c3d4e5f6
Revises: f1b2c3d4e5a6
Create Date: 2026-03-01 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "f1b2c3d4e5a6"
branch_labels = None
depends_on = None


def _index_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {item["name"] for item in inspector.get_indexes(table_name)}


def upgrade() -> None:
    """Create board_slack_connections table."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("board_slack_connections"):
        op.create_table(
            "board_slack_connections",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("board_id", sa.Uuid(), nullable=False),
            sa.Column("slack_team_id", sa.String(32), nullable=False),
            sa.Column("slack_team_name", sa.String(255), nullable=True),
            sa.Column("slack_channel_id", sa.String(32), nullable=False),
            sa.Column("slack_channel_name", sa.String(255), nullable=True),
            sa.Column("bot_token_encrypted", sa.Text(), nullable=False),
            sa.Column("bot_user_id", sa.String(32), nullable=True),
            sa.Column("installer_user_id", sa.String(32), nullable=True),
            sa.Column("scopes", sa.Text(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["board_id"], ["boards.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("board_id", name="uq_board_slack_connections_board_id"),
            sa.UniqueConstraint(
                "slack_team_id",
                "slack_channel_id",
                name="uq_board_slack_connections_team_channel",
            ),
        )

    inspector = sa.inspect(bind)
    indexes = _index_names(inspector, "board_slack_connections")
    if "ix_board_slack_connections_board_id" not in indexes:
        op.create_index(
            "ix_board_slack_connections_board_id",
            "board_slack_connections",
            ["board_id"],
        )
    if "ix_board_slack_connections_slack_team_id" not in indexes:
        op.create_index(
            "ix_board_slack_connections_slack_team_id",
            "board_slack_connections",
            ["slack_team_id"],
        )
    if "ix_board_slack_connections_slack_channel_id" not in indexes:
        op.create_index(
            "ix_board_slack_connections_slack_channel_id",
            "board_slack_connections",
            ["slack_channel_id"],
        )
    if "ix_board_slack_connections_is_active" not in indexes:
        op.create_index(
            "ix_board_slack_connections_is_active",
            "board_slack_connections",
            ["is_active"],
        )


def downgrade() -> None:
    """Drop board_slack_connections table."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("board_slack_connections"):
        indexes = _index_names(inspector, "board_slack_connections")
        for index_name in [
            "ix_board_slack_connections_is_active",
            "ix_board_slack_connections_slack_channel_id",
            "ix_board_slack_connections_slack_team_id",
            "ix_board_slack_connections_board_id",
        ]:
            if index_name in indexes:
                op.drop_index(index_name, table_name="board_slack_connections")
        op.drop_table("board_slack_connections")
