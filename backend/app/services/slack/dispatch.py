"""Slack inbound message dispatch — routes queued Slack messages to board agents."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlmodel import col

from app.core.logging import get_logger
from app.db.session import async_session_maker
from app.models.agents import Agent
from app.models.board_memory import BoardMemory
from app.services.openclaw.gateway_dispatch import GatewayDispatchService
from app.services.slack.queue import QueuedSlackMessage, decode_slack_task
from app.services.slack_service import get_connection_by_channel

if TYPE_CHECKING:
    from app.services.queue import QueuedTask

logger = get_logger(__name__)


async def process_slack_queue_task(task: QueuedTask) -> None:
    """Process a queued Slack inbound message: look up board, route to agent."""
    msg = decode_slack_task(task)
    await _dispatch_slack_message(msg)


async def _dispatch_slack_message(msg: QueuedSlackMessage) -> None:
    """Route a Slack message to the board's agent via the gateway."""
    async with async_session_maker() as session:
        connection = await get_connection_by_channel(
            session,
            team_id=msg.team_id,
            channel_id=msg.channel_id,
        )
        if connection is None:
            logger.debug(
                "slack.dispatch.no_connection",
                extra={"team_id": msg.team_id, "channel_id": msg.channel_id},
            )
            return

        board_id = connection.board_id

        # Store as board memory
        memory_content = (
            f"SLACK MESSAGE from {msg.user_name} ({msg.user_id})\n"
            f"Channel: #{connection.slack_channel_name or msg.channel_id}\n"
            f"Timestamp: {msg.slack_ts}\n\n"
            f"{msg.text}"
        )
        memory = BoardMemory(
            board_id=board_id,
            content=memory_content,
            tags=["slack", f"slack_user:{msg.user_id}", f"slack_ts:{msg.slack_ts}"],
            source="slack",
            is_chat=True,
        )
        session.add(memory)
        await session.commit()

        # Find the board lead agent
        lead_agent = (
            await session.exec(
                Agent.objects.filter_by(board_id=board_id).filter(
                    col(Agent.is_board_lead).is_(True)
                ).statement
            )
        ).first()

        if lead_agent is None or not lead_agent.openclaw_session_id:
            logger.debug(
                "slack.dispatch.no_lead_agent",
                extra={"board_id": str(board_id)},
            )
            return

        dispatch = GatewayDispatchService(session)
        config = await dispatch.optional_gateway_config_for_board_by_id(board_id)
        if config is None:
            logger.debug(
                "slack.dispatch.no_gateway",
                extra={"board_id": str(board_id)},
            )
            return

        message = (
            f"SLACK MESSAGE from {msg.user_name}\n"
            f"Channel: #{connection.slack_channel_name or msg.channel_id}\n\n"
            f"{msg.text}\n\n"
            f"[source:slack] [slack_ts:{msg.slack_ts}] [slack_user:{msg.user_id}]"
        )
        await dispatch.try_send_agent_message(
            session_key=lead_agent.openclaw_session_id,
            config=config,
            agent_name=lead_agent.name,
            message=message,
            deliver=True,
        )
        logger.info(
            "slack.dispatch.sent_to_agent",
            extra={
                "board_id": str(board_id),
                "agent_name": lead_agent.name,
                "slack_ts": msg.slack_ts,
            },
        )
