"""LLM provider abstraction.

`get_llm_provider` picks a primary implementation based on `LLM_PROVIDER`
(`gemini` or `groq`), so the rest of the AI layer (summaries, text-to-SQL,
anomaly explanations) is written once against the `LLMProvider` interface
and never needs to know which vendor is configured. Both providers are
called over plain REST via httpx rather than a vendor SDK, keeping the
dependency footprint small and the two implementations symmetric.

If the *other* provider's API key is also configured, `get_llm_provider`
wraps the primary in `FallbackLLMProvider`, which retries a failed call
(e.g. a free-tier quota/rate-limit error) against the other provider before
giving up — so a single vendor's outage or quota exhaustion doesn't take
down the AI panels.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import httpx
from common.config import Settings

logger = logging.getLogger(__name__)


class LLMProvider(ABC):
    """A chat-style text completion provider."""

    @abstractmethod
    def complete(self, prompt: str, system: str | None = None) -> str:
        """Return the model's text response to `prompt`, given an optional system instruction."""


class GeminiProvider(LLMProvider):
    """Google Gemini via the generativelanguage REST API (free tier)."""

    def __init__(self, api_key: str, model: str, http_client: httpx.Client) -> None:
        self._api_key = api_key
        self._model = model
        self._http = http_client

    def complete(self, prompt: str, system: str | None = None) -> str:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self._model}:generateContent"
        )
        payload: dict = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2},
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        response = self._http.post(
            url, params={"key": self._api_key}, json=payload, timeout=60.0
        )
        response.raise_for_status()
        data = response.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError) as exc:
            raise RuntimeError(f"Unexpected Gemini response shape: {data}") from exc


class GroqProvider(LLMProvider):
    """Groq's OpenAI-compatible chat completions API (free tier)."""

    def __init__(self, api_key: str, model: str, http_client: httpx.Client) -> None:
        self._api_key = api_key
        self._model = model
        self._http = http_client

    def complete(self, prompt: str, system: str | None = None) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = self._http.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"model": self._model, "messages": messages, "temperature": 0.2},
            timeout=60.0,
        )
        response.raise_for_status()
        data = response.json()
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError) as exc:
            raise RuntimeError(f"Unexpected Groq response shape: {data}") from exc


class FallbackLLMProvider(LLMProvider):
    """Retries a failed `complete()` call against a secondary provider.

    Only network/API failures trigger the fallback (HTTP errors, timeouts,
    an unexpected response shape) — anything else propagates immediately.
    """

    def __init__(self, primary: LLMProvider, fallback: LLMProvider, fallback_name: str) -> None:
        self._primary = primary
        self._fallback = fallback
        self._fallback_name = fallback_name

    def complete(self, prompt: str, system: str | None = None) -> str:
        try:
            return self._primary.complete(prompt, system=system)
        except (httpx.HTTPError, RuntimeError):
            logger.warning(
                "Primary LLM provider call failed, retrying against %s",
                self._fallback_name,
                exc_info=True,
            )
            return self._fallback.complete(prompt, system=system)


def _build_provider(name: str, settings: Settings, http_client: httpx.Client) -> LLMProvider:
    if name == "gemini":
        if not settings.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is not set")
        return GeminiProvider(settings.gemini_api_key, settings.gemini_model, http_client)

    if name == "groq":
        if not settings.groq_api_key:
            raise ValueError("GROQ_API_KEY is not set")
        return GroqProvider(settings.groq_api_key, settings.groq_model, http_client)

    raise ValueError(f"Unknown LLM_PROVIDER: {name!r}")


def get_llm_provider(settings: Settings, http_client: httpx.Client) -> LLMProvider:
    """Build the LLM provider configured by `settings.llm_provider`.

    Wraps it with the other vendor as a fallback when that vendor's API key
    is also configured (see `FallbackLLMProvider`).
    """
    if settings.llm_provider not in ("gemini", "groq"):
        raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider!r}")

    primary = _build_provider(settings.llm_provider, settings, http_client)

    fallback_name = "groq" if settings.llm_provider == "gemini" else "gemini"
    try:
        fallback = _build_provider(fallback_name, settings, http_client)
    except ValueError:
        return primary

    return FallbackLLMProvider(primary, fallback, fallback_name)
