"""Webhook-worker entrypoint that starts the Slack Socket Mode listener
alongside the standard RQ worker."""

from __future__ import annotations

import subprocess
import sys

from app.core.logging import get_logger
from app.services.slack.socket_listener import start_socket_listener

logger = get_logger(__name__)


def main() -> None:
    # Start the Slack Socket Mode listener in a daemon thread
    logger.info("worker_entrypoint: starting Slack Socket Mode listener")
    start_socket_listener()

    # Start the RQ worker as the main process
    logger.info("worker_entrypoint: starting RQ worker")
    sys.exit(
        subprocess.call(
            ["rq", "worker", "-u", sys.argv[1] if len(sys.argv) > 1 else "redis://redis:6379/0"],
        )
    )


if __name__ == "__main__":
    main()
