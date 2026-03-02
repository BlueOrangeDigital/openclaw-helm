"""Slack outbound message hook — posts agent responses back to Slack.

Called after an agent response is dispatched to the board. Checks if the board
has an active Slack connection and posts the response to Slack.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.core.logging import get_logger
from app.services.slack_service import get_active_connection, post_message

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession

logger = get_logger(__name__)


async def post_agent_response_to_slack(
    session: AsyncSession,
    *,
    board_id: UUID,
    agent_name: str,
    response_text: str,
    source: str | None = None,
    slack_thread_ts: str | None = None,
) -> bool:
    """Post an agent response to Slack if the board has an active connection.

    Deduplication rules:
    - If source is "slack", reply as a thread to the original Slack message.
    - If source is "board", post as a new message with attribution.
    - Never re-post a message that originated from Slack back to Slack as a
      top-level message (dedup: the agent response is the thread reply).

    Returns True if a message was posted to Slack.
    """
    connection = await get_active_connection(session, board_id)
    if connection is None:
        return False

    # Format the message with agent attribution
    formatted = f"*{agent_name}*:\n{response_text}"

    # If the message originated from Slack, reply in thread
    thread_ts = slack_thread_ts if source == "slack" else None

    result = post_message(connection, formatted, thread_ts=thread_ts)
    if result is not None:
        logger.info(
            "slack.outbound.posted",
            extra={
                "board_id": str(board_id),
                "source": source,
                "thread_ts": thread_ts,
                "channel_id": connection.slack_channel_id,
            },
        )
        return True

    logger.warning(
        "slack.outbound.failed",
        extra={"board_id": str(board_id), "source": source},
    )
    return False


def should_post_to_slack(*, source: str | None, message_source: str | None = None) -> bool:
    """Determine if a message should be posted to Slack.

    Dedup logic:
    - Board UI messages -> post to Slack (new message)
    - Slack inbound messages -> agent response posts as thread reply
    - Agent-initiated messages -> post to Slack
    - Messages already from Slack bot -> skip (prevent loops)
    """
    if message_source == "slack_bot":
        return False
    return True
