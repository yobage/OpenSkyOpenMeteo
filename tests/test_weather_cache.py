"""Tests for the Open-Meteo grid-cell weather cache."""

from __future__ import annotations

import httpx
import pytest
from consumer.weather import WeatherClient


def _make_client(monkeypatch, current: dict, clock: list[float]) -> WeatherClient:
    """Build a WeatherClient whose HTTP calls are counted, not real."""
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json={"current": current})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = WeatherClient(
        base_url="https://api.open-meteo.com/v1/forecast",
        http_client=http_client,
        grid_size_deg=0.25,
        cache_ttl_seconds=600.0,
        clock=lambda: clock[0],
    )
    client._call_count = call_count  # type: ignore[attr-defined]
    return client


@pytest.fixture
def sample_current() -> dict:
    return {
        "time": "2026-08-28T12:00",
        "temperature_2m": 31.5,
        "wind_speed_10m": 8.2,
        "wind_direction_10m": 270,
        "weather_code": 0,
    }


def test_nearby_positions_share_one_cached_lookup(monkeypatch, sample_current) -> None:
    clock = [0.0]
    client = _make_client(monkeypatch, sample_current, clock)

    reading_a = client.get_weather(32.0, 34.8)
    reading_b = client.get_weather(32.05, 34.85)  # same grid cell (0.25deg)

    assert client._call_count["n"] == 1  # type: ignore[attr-defined]
    assert reading_a == reading_b
    assert reading_a.temperature_c == pytest.approx(31.5)


def test_distant_positions_trigger_separate_lookups(monkeypatch, sample_current) -> None:
    clock = [0.0]
    client = _make_client(monkeypatch, sample_current, clock)

    client.get_weather(29.5, 34.3)   # south
    client.get_weather(33.2, 35.5)   # north, different grid cell

    assert client._call_count["n"] == 2  # type: ignore[attr-defined]


def test_cache_expires_after_ttl(monkeypatch, sample_current) -> None:
    clock = [0.0]
    client = _make_client(monkeypatch, sample_current, clock)

    client.get_weather(32.0, 34.8)
    clock[0] = 700.0  # past the 600s TTL
    client.get_weather(32.0, 34.8)

    assert client._call_count["n"] == 2  # type: ignore[attr-defined]


def test_missing_coordinates_return_none_without_http_call(monkeypatch, sample_current) -> None:
    clock = [0.0]
    client = _make_client(monkeypatch, sample_current, clock)

    assert client.get_weather(None, 34.8) is None
    assert client.get_weather(32.0, None) is None
    assert client._call_count["n"] == 0  # type: ignore[attr-defined]
