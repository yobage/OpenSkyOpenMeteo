"""LLM provider abstraction.

`get_llm_provider` picks an implementation based on `LLM_PROVIDER`
(`gemini` or `groq`), so the rest of the AI layer (summaries, text-to-SQL,
anomaly explanations) is written once against the `LLMProvider` interface
and never needs to know which vendor is configured. Both providers are
called over plain REST via httpx rather than a vendor SDK, keeping the
dependency footprint small and the two implementations symmetric.
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
            url, params={"key": self._api_key}, json=payload, timeout=30.0
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
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError) as exc:
            raise RuntimeError(f"Unexpected Groq response shape: {data}") from exc


def get_llm_provider(settings: Settings, http_client: httpx.Client) -> LLMProvider:
    """Build the LLM provider configured by `settings.llm_provider`."""
    if settings.llm_provider == "gemini":
        if not settings.gemini_api_key:
            raise ValueError("LLM_PROVIDER=gemini requires GEMINI_API_KEY to be set")
        return GeminiProvider(settings.gemini_api_key, settings.gemini_model, http_client)

    if settings.llm_provider == "groq":
        if not settings.groq_api_key:
            raise ValueError("LLM_PROVIDER=groq requires GROQ_API_KEY to be set")
        return GroqProvider(settings.groq_api_key, settings.groq_model, http_client)

    raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider!r}")
