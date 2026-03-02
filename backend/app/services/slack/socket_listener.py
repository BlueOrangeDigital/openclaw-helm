"""Slack Socket Mode listener for inbound messages.

Runs as a background thread within the webhook-worker process.
Receives message events from connected Slack workspaces and enqueues them
for async processing by the queue worker.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse
from slack_sdk.web import WebClient

from app.core.config import settings
from app.core.logging import get_logger
from app.services.slack.queue import QueuedSlackMessage, enqueue_slack_message
from app.services.slack.user_cache import resolve_user_name

logger = get_logger(__name__)

_listener_thread: threading.Thread | None = None
_socket_client: SocketModeClient | None = None


def _handle_socket_event(client: SocketModeClient, req: SocketModeRequest) -> None:
    """Process a Socket Mode event envelope."""
    # Acknowledge immediately to prevent retries
    client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))

    if req.type != "events_api":
        return

    event = req.payload.get("event", {})
    event_type = event.get("type", "")

    if event_type != "message":
        return

    # Skip bot messages, message_changed, message_deleted subtypes
    subtype = event.get("subtype")
    if subtype is not None:
        return

    # Skip messages from our own bot
    bot_id = event.get("bot_id")
    if bot_id:
        return

    team_id = req.payload.get("team_id", "")
    channel_id = event.get("channel", "")
    user_id = event.get("user", "")
    text = event.get("text", "")
    slack_ts = event.get("ts", "")
    thread_ts = event.get("thread_ts")

    if not text or not channel_id or not user_id:
        return

    user_name = resolve_user_name(
        user_id=user_id,
        team_id=team_id,
        app_token=settings.slack_app_token,
    )

    enqueue_slack_message(
        QueuedSlackMessage(
            team_id=team_id,
            channel_id=channel_id,
            user_id=user_id,
            user_name=user_name,
            text=text,
            slack_ts=slack_ts,
            thread_ts=thread_ts,
            received_at=datetime.now(UTC),
        )
    )
    logger.info(
        "slack.socket.message_enqueued",
        extra={
            "team_id": team_id,
            "channel_id": channel_id,
            "user_id": user_id,
            "slack_ts": slack_ts,
        },
    )


def _run_socket_mode() -> None:
    """Start the Socket Mode client (blocking)."""
    global _socket_client  # noqa: PLW0603
    app_token = settings.slack_app_token
    if not app_token:
        logger.warning("slack.socket.no_app_token — Socket Mode listener disabled")
        return

    logger.info("slack.socket.starting")
    _socket_client = SocketModeClient(
        app_token=app_token,
        web_client=WebClient(),
    )
    _socket_client.socket_mode_request_listeners.append(_handle_socket_event)

    try:
        _socket_client.connect()
        logger.info("slack.socket.connected")
        # Block forever — SocketModeClient manages reconnection internally
        import time

        while True:
            time.sleep(60)
    except Exception:
        logger.exception("slack.socket.fatal_error")
        raise


def start_socket_listener() -> None:
    """Start the Socket Mode listener in a daemon thread."""
    global _listener_thread  # noqa: PLW0603
    if _listener_thread is not None and _listener_thread.is_alive():
        logger.info("slack.socket.already_running")
        return
    if not settings.slack_app_token:
        logger.info("slack.socket.disabled — no SLACK_APP_TOKEN configured")
        return
    _listener_thread = threading.Thread(
        target=_run_socket_mode,
        name="slack-socket-listener",
        daemon=True,
    )
    _listener_thread.start()
    logger.info("slack.socket.thread_started")


def stop_socket_listener() -> None:
    """Disconnect the Socket Mode client."""
    global _socket_client  # noqa: PLW0603
    if _socket_client is not None:
        try:
            _socket_client.disconnect()
        except Exception:
            logger.exception("slack.socket.disconnect_error")
        _socket_client = None
    logger.info("slack.socket.stopped")
