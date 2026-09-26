"""One interface for every LLM provider.

Swapping models = changing LLM_PROVIDER / LLM_MODEL in .env. The composer only
ever calls `complete_json(system, user)`, so no provider detail leaks into the
business logic. Calls use plain HTTPS via httpx (no vendor SDKs): fewer
dependencies and identical timeout/error handling for every provider.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any, Optional

from app.config import Settings, settings as default_settings

SEED = 20260426  # fixed seed where the provider supports one


class LLMError(RuntimeError):
    pass


class LLMProvider(ABC):
    name: str = "base"

    def __init__(self, model: str, timeout: float) -> None:
        self.model = model
        self.timeout = timeout

    @abstractmethod
    def complete(self, system: str, user: str) -> str:
        """Return the raw text of one completion at temperature 0."""

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        text = self.complete(system, user)
        return parse_json_object(text)


def parse_json_object(text: str) -> dict[str, Any]:
    """Accept bare JSON or JSON wrapped in ```json fences / extra prose."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise LLMError(f"no JSON object in response: {text[:120]!r}")
        try:
            data = json.loads(match.group())
        except json.JSONDecodeError as exc:
            raise LLMError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise LLMError("JSON is not an object")
    return data


def build_provider(cfg: Settings = default_settings) -> Optional[LLMProvider]:
    """Return the configured provider, or None for template-only mode."""
    provider = cfg.llm_provider
    if provider == "openai" and cfg.openai_api_key:
        from app.llm.openai_provider import OpenAIProvider
        return OpenAIProvider(cfg.llm_model or "gpt-4o-mini", cfg.llm_timeout_seconds, cfg.openai_api_key)
    if provider == "anthropic" and cfg.anthropic_api_key:
        from app.llm.anthropic_provider import AnthropicProvider
        return AnthropicProvider(cfg.llm_model or "claude-haiku-4-5-20251001", cfg.llm_timeout_seconds,
                                 cfg.anthropic_api_key)
    return None
