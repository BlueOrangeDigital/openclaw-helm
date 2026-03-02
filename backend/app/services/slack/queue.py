"""Slack inbound message queue persistence and delivery helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.config import settings
from app.core.logging import get_logger
from app.services.queue import QueuedTask, enqueue_task
from app.services.queue import requeue_if_failed as generic_requeue_if_failed

logger = get_logger(__name__)
TASK_TYPE = "slack_inbound"


@dataclass(frozen=True)
class QueuedSlackMessage:
    """Payload metadata for a deferred Slack inbound message."""

    team_id: str
    channel_id: str
    user_id: str
    user_name: str
    text: str
    slack_ts: str
    thread_ts: str | None = None
    received_at: datetime | None = None
    attempts: int = 0


def _task_from_payload(msg: QueuedSlackMessage) -> QueuedTask:
    return QueuedTask(
        task_type=TASK_TYPE,
        payload={
            "team_id": msg.team_id,
            "channel_id": msg.channel_id,
            "user_id": msg.user_id,
            "user_name": msg.user_name,
            "text": msg.text,
            "slack_ts": msg.slack_ts,
            "thread_ts": msg.thread_ts,
            "received_at": (msg.received_at or datetime.now(UTC)).isoformat(),
        },
        created_at=msg.received_at or datetime.now(UTC),
        attempts=msg.attempts,
    )


def decode_slack_task(task: QueuedTask) -> QueuedSlackMessage:
    """Decode a generic queued task into a Slack inbound message."""
    if task.task_type != TASK_TYPE:
        raise ValueError(f"Unexpected task_type={task.task_type!r}; expected {TASK_TYPE!r}")
    p: dict[str, Any] = task.payload
    received_raw = p.get("received_at")
    return QueuedSlackMessage(
        team_id=p["team_id"],
        channel_id=p["channel_id"],
        user_id=p["user_id"],
        user_name=p.get("user_name", ""),
        text=p["text"],
        slack_ts=p["slack_ts"],
        thread_ts=p.get("thread_ts"),
        received_at=datetime.fromisoformat(received_raw) if received_raw else datetime.now(UTC),
        attempts=task.attempts,
    )


def enqueue_slack_message(msg: QueuedSlackMessage) -> bool:
    """Enqueue a Slack inbound message for async processing."""
    try:
        queued = _task_from_payload(msg)
        enqueue_task(queued, settings.rq_queue_name, redis_url=settings.rq_redis_url)
        logger.info(
            "slack.queue.enqueued",
            extra={
                "team_id": msg.team_id,
                "channel_id": msg.channel_id,
                "user_id": msg.user_id,
                "slack_ts": msg.slack_ts,
            },
        )
        return True
    except Exception as exc:
        logger.warning(
            "slack.queue.enqueue_failed",
            extra={
                "team_id": msg.team_id,
                "channel_id": msg.channel_id,
                "error": str(exc),
            },
        )
        return False


def requeue_slack_queue_task(task: QueuedTask, *, delay_seconds: float = 0) -> bool:
    """Requeue a failed Slack task with capped retries."""
    try:
        return generic_requeue_if_failed(
            task,
            settings.rq_queue_name,
            max_retries=settings.rq_dispatch_max_retries,
            redis_url=settings.rq_redis_url,
            delay_seconds=delay_seconds,
        )
    except Exception as exc:
        logger.warning(
            "slack.queue.requeue_failed",
            extra={"error": str(exc)},
        )
        return False
