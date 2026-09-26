"""OpenAI Chat Completions provider (temperature 0 + fixed seed + JSON mode)."""

from __future__ import annotations

import httpx

from app.llm.base import SEED, LLMError, LLMProvider

OPENAI_URL = "https://api.openai.com/v1/chat/completions"


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, model: str, timeout: float, api_key: str) -> None:
        super().__init__(model, timeout)
        self._api_key = api_key

    def complete(self, system: str, user: str) -> str:
        body = {
            "model": self.model,
            "temperature": 0,
            "seed": SEED,
            "max_tokens": 600,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            resp = httpx.post(
                OPENAI_URL,
                json=body,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"openai request failed: {exc}") from exc
        if resp.status_code != 200:
            raise LLMError(f"openai HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"unexpected openai response: {exc}") from exc
