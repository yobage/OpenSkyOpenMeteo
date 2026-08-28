"""Client for polling OpenSky's /states/all endpoint over a bounding box."""

from __future__ import annotations

import logging

import httpx
from common.models import StateVector
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ingestion.opensky_auth import OpenSkyAuth

logger = logging.getLogger(__name__)


class RateLimitedError(Exception):
    """Raised when OpenSky responds 429; retried with backoff."""


class OpenSkyClient:
    """Polls OpenSky /states/all for a fixed bounding box."""

    def __init__(
        self,
        base_url: str,
        bbox: tuple[float, float, float, float],
        http_client: httpx.Client,
        auth: OpenSkyAuth | None = None,
    ) -> None:
        """bbox is (lamin, lamax, lomin, lomax)."""
        self._base_url = base_url.rstrip("/")
        self._lamin, self._lamax, self._lomin, self._lomax = bbox
        self._http = http_client
        self._auth = auth

    @retry(
        retry=retry_if_exception_type(RateLimitedError),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def fetch_states(self) -> list[StateVector]:
        """Fetch current aircraft states within the bounding box.

        Retries with exponential backoff on HTTP 429. Malformed individual
        state entries are logged and skipped rather than failing the batch.
        """
        headers = {}
        token = self._auth.get_token() if self._auth else None
        if token:
            headers["Authorization"] = f"Bearer {token}"

        response = self._http.get(
            f"{self._base_url}/states/all",
            params={
                "lamin": self._lamin,
                "lamax": self._lamax,
                "lomin": self._lomin,
                "lomax": self._lomax,
            },
            headers=headers,
        )

        if response.status_code == 429:
            logger.warning("OpenSky rate limit hit (429), backing off")
            raise RateLimitedError(response.text)

        response.raise_for_status()
        payload = response.json()
        raw_states = payload.get("states") or []

        states: list[StateVector] = []
        for raw in raw_states:
            try:
                states.append(StateVector.from_array(raw))
            except (ValueError, IndexError, TypeError) as exc:
                logger.warning("Skipping malformed state vector: %s", exc)
        return states
