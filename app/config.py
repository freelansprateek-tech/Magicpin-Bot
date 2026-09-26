"""Settings loaded from environment variables.

Secrets (API keys) are read ONLY from the environment / .env file.
Nothing secret is ever hard-coded in source.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

# Load .env from the project root if it exists. Real environment variables
# (e.g. set by Render/Railway/Fly) take priority over the file.
load_dotenv(override=False)


def _csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


@dataclass(frozen=True)
class Settings:
    # Server
    port: int = int(os.getenv("PORT", "8080"))
    log_level: str = os.getenv("LOG_LEVEL", "info")

    # Bot identity for GET /v1/metadata
    team_name: str = os.getenv("BOT_TEAM_NAME", "Your Team Name")
    team_members: list[str] = field(
        default_factory=lambda: _csv(os.getenv("BOT_TEAM_MEMBERS", "Your Full Name"))
    )
    contact_email: str = os.getenv("BOT_CONTACT_EMAIL", "you@example.com")
    version: str = os.getenv("BOT_VERSION", "0.1.0")
    submitted_at: str = os.getenv("BOT_SUBMITTED_AT", "2026-09-26T00:00:00Z")

    # LLM (used from Phase 4)
    llm_provider: str = os.getenv("LLM_PROVIDER", "none").lower()
    llm_model: str = os.getenv("LLM_MODEL", "")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    llm_timeout_seconds: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "8"))
    llm_max_parallel: int = int(os.getenv("LLM_MAX_PARALLEL", "8"))

    # Cache (used from Phase 4)
    cache_dir: str = os.getenv("CACHE_DIR", "cache")

    # Hard limits from challenge-testing-brief.md §5
    max_actions_per_tick: int = 20
    max_context_bytes: int = 500 * 1024


settings = Settings()
