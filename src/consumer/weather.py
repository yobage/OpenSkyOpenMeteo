"""Open-Meteo weather client with grid-cell caching.

Open-Meteo has no API key and no hard rate limit for this use case, but
polling weather once per flight per cycle would still be wasteful: many
aircraft over Israel share roughly the same weather. We bucket each flight's
position onto a coarse lat/lon grid and cache one reading per grid cell for
a short TTL, so a busy poll cycle triggers only a handful of HTTP calls.

The consumer calls `get_weather` from multiple worker threads (see
`consumer/main.py`), so cache reads/writes are protected by a lock. Only the
cache access is locked, not the HTTP call itself, so concurrent lookups for
different (uncached) grid cells still fetch in parallel.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

import httpx
from common.models import WeatherReading
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_GridKey = tuple[float, float]


class WeatherClient:
    """Fetches current weather from Open-Meteo, cached per grid cell."""

    def __init__(
        self,
        base_url: str,
        http_client: httpx.Client,
        grid_size_deg: float = 0.25,
        cache_ttl_seconds: float = 600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base_url = base_url
        self._http = http_client
        self._grid_size_deg = grid_size_deg
        self._cache_ttl_seconds = cache_ttl_seconds
        self._clock = clock
        self._cache: dict[_GridKey, tuple[WeatherReading, float]] = {}
        self._cache_lock = threading.Lock()

    def _grid_key(self, lat: float, lon: float) -> _GridKey:
        size = self._grid_size_deg
        return (
            round(round(lat / size) * size, 4),
            round(round(lon / size) * size, 4),
        )

    def get_weather(self, lat: float | None, lon: float | None) -> WeatherReading | None:
        """Return the (possibly cached) weather at a position, or None on failure."""
        if lat is None or lon is None:
            return None

        key = self._grid_key(lat, lon)
        now = self._clock()
        with self._cache_lock:
            cached = self._cache.get(key)
        if cached is not None and now - cached[1] < self._cache_ttl_seconds:
            return cached[0]

        reading = self._fetch(key[0], key[1])
        if reading is not None:
            with self._cache_lock:
                self._cache[key] = (reading, now)
        return reading

    def _fetch(self, lat: float, lon: float) -> WeatherReading | None:
        try:
            return self._fetch_with_retry(lat, lon)
        except httpx.HTTPError as exc:
            logger.warning("Failed to fetch weather for grid cell (%.2f, %.2f): %s", lat, lon, exc)
            return None

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _fetch_with_retry(self, lat: float, lon: float) -> WeatherReading:
        response = self._http.get(
            self._base_url,
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,wind_speed_10m,wind_direction_10m,weather_code",
            },
        )
        response.raise_for_status()
        current = response.json()["current"]
        return WeatherReading(
            temperature_c=current.get("temperature_2m"),
            wind_speed_kmh=current.get("wind_speed_10m"),
            wind_direction_deg=current.get("wind_direction_10m"),
            weather_code=current.get("weather_code"),
            observed_at=current.get("time"),
        )
