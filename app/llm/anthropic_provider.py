"""Anthropic Messages API provider (temperature 0).

Anthropic has no `seed` parameter, so determinism across calls relies on the
disk cache in app/llm/cache.py (same input hash -> same stored output).
"""

from __future__ import annotations

import httpx

from app.llm.base import LLMError, LLMProvider

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, model: str, timeout: float, api_key: str) -> None:
        super().__init__(model, timeout)
        self._api_key = api_key

    def complete(self, system: str, user: str) -> str:
        body = {
            "model": self.model,
            "max_tokens": 600,
            "temperature": 0,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        try:
            resp = httpx.post(
                ANTHROPIC_URL,
                json=body,
                headers={"x-api-key": self._api_key, "anthropic-version": "2023-06-01"},
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"anthropic request failed: {exc}") from exc
        if resp.status_code != 200:
            raise LLMError(f"anthropic HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            blocks = resp.json()["content"]
            return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        except (KeyError, ValueError) as exc:
            raise LLMError(f"unexpected anthropic response: {exc}") from exc
