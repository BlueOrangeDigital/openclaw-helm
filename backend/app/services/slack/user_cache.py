"""Simple in-memory cache for Slack user display names."""

from __future__ import annotations

import threading
from typing import Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from app.core.logging import get_logger

logger = get_logger(__name__)

_cache: dict[str, str] = {}
_lock = threading.Lock()


def resolve_user_name(
    *,
    user_id: str,
    team_id: str,
    app_token: str | None = None,
    bot_tokens: dict[str, str] | None = None,
) -> str:
    """Resolve a Slack user ID to a display name, with caching.

    Uses team-scoped bot tokens when available, falls back to user_id.
    """
    cache_key = f"{team_id}:{user_id}"
    with _lock:
        if cache_key in _cache:
            return _cache[cache_key]

    # We need a bot token for the workspace to call users.info
    # For now, fall back to user_id if no token is readily available
    display_name = user_id
    try:
        # Import here to avoid circular imports during socket mode startup
        from app.models.board_slack_connection import decrypt_token

        # Try to look up a token from the DB (sync context, so we use a direct approach)
        _try_resolve_from_db(user_id, team_id, cache_key)
        with _lock:
            if cache_key in _cache:
                return _cache[cache_key]
    except Exception:
        pass

    with _lock:
        _cache[cache_key] = display_name
    return display_name


def _try_resolve_from_db(user_id: str, team_id: str, cache_key: str) -> None:
    """Try to resolve user name using a DB-stored bot token (sync context)."""
    try:
        import redis as redis_lib

        from app.core.config import settings

        # Check Redis cache first
        r = redis_lib.Redis.from_url(settings.rq_redis_url)
        cached = r.get(f"slack_user:{cache_key}")
        if cached:
            name = cached.decode() if isinstance(cached, bytes) else str(cached)
            with _lock:
                _cache[cache_key] = name
            return

        # Look up a bot token for this team from Redis cache
        token_data = r.get(f"slack_bot_token:{team_id}")
        if not token_data:
            return

        from app.models.board_slack_connection import decrypt_token

        token = decrypt_token(token_data.decode() if isinstance(token_data, bytes) else str(token_data))
        client = WebClient(token=token)
        response = client.users_info(user=user_id)
        user_info: dict[str, Any] = response.data.get("user", {}) if response.data else {}
        profile = user_info.get("profile", {})
        name = (
            profile.get("display_name")
            or profile.get("real_name")
            or user_info.get("real_name")
            or user_id
        )
        with _lock:
            _cache[cache_key] = name
        # Cache in Redis for 1 hour
        r.setex(f"slack_user:{cache_key}", 3600, name)
    except Exception as exc:
        logger.debug("slack.user_cache.resolve_failed", extra={"error": str(exc)})
