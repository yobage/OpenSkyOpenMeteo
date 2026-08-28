"""Liveness heartbeat for container healthchecks.

Ingestion and consumer are long-running workers with no HTTP endpoint of
their own, so they can't be healthchecked the way the dashboard is (an HTTP
GET). Instead they touch a file on every successful work cycle; the
service's Dockerfile HEALTHCHECK just checks that file's mtime is recent.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_HEARTBEAT_PATH = "/tmp/healthy"


def touch_heartbeat(path: str = DEFAULT_HEARTBEAT_PATH) -> None:
    """Update the heartbeat file's mtime to now, creating it if needed."""
    Path(path).touch()
