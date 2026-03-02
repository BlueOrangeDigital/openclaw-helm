"""Webhook-worker entrypoint — runs the custom queue consumer loop
(which handles webhook, Slack inbound, and lifecycle tasks) and starts
the Slack Socket Mode listener as a daemon thread."""

from __future__ import annotations

from app.services.queue_worker import run_worker

if __name__ == "__main__":
    run_worker()
