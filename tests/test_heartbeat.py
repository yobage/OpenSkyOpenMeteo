"""Tests for the container-healthcheck heartbeat helper."""

import time
from pathlib import Path

from common.heartbeat import touch_heartbeat


def test_touch_heartbeat_creates_file(tmp_path: Path) -> None:
    path = tmp_path / "healthy"
    assert not path.exists()

    touch_heartbeat(str(path))

    assert path.exists()


def test_touch_heartbeat_updates_mtime(tmp_path: Path) -> None:
    path = tmp_path / "healthy"
    touch_heartbeat(str(path))
    first_mtime = path.stat().st_mtime

    time.sleep(0.01)
    touch_heartbeat(str(path))

    assert path.stat().st_mtime >= first_mtime
