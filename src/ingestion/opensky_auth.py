"""OAuth2 client-credentials auth for the OpenSky Network API.

Basic-auth was retired in March 2026; OpenSky now requires an OAuth2 bearer
token obtained via the client-credentials grant. Tokens are short-lived
(~30 min), so we cache the token and refresh it shortly before it expires.
If no client credentials are configured, `get_token` returns None and the
caller falls back to anonymous (unauthenticated, lower rate limit) access.
"""

from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger(__name__)

# Refresh this many seconds before the token's actual expiry, to avoid racing
# a request against an about-to-expire token.
_EXPIRY_BUFFER_SECONDS = 60


class OpenSkyAuth:
    """Fetches and caches OAuth2 bearer tokens for the OpenSky API."""

    def __init__(
        self,
        client_id: str | None,
        client_secret: str | None,
        token_url: str,
        http_client: httpx.Client,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_url = token_url
        self._http = http_client
        self._token: str | None = None
        self._expires_at: float = 0.0

    @property
    def is_configured(self) -> bool:
        """Whether client credentials are present (vs. anonymous access)."""
        return bool(self._client_id and self._client_secret)

    def get_token(self) -> str | None:
        """Return a valid bearer token, refreshing it if needed.

        Returns None if no credentials are configured (anonymous access).
        """
        if not self.is_configured:
            return None

        if self._token and time.monotonic() < self._expires_at - _EXPIRY_BUFFER_SECONDS:
            return self._token

        self._token = self._fetch_token()
        return self._token

    def _fetch_token(self) -> str:
        logger.info("Fetching new OpenSky OAuth2 token")
        response = self._http.post(
            self._token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        payload = response.json()
        expires_in = payload.get("expires_in", 1800)
        self._expires_at = time.monotonic() + expires_in
        logger.info("Obtained OpenSky token, expires in %ss", expires_in)
        return payload["access_token"]
