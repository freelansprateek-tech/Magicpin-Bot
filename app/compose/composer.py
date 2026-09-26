"""Composer: fact sheet -> LLM draft -> validator -> (retry once) -> template fallback.

Flow for one message:
  1. build the fact sheet (only grounded, pre-formatted facts)
  2. render the deterministic template (always; it's the fallback AND the
     baseline shown to the LLM)
  3. if an LLM is configured: cache lookup -> LLM call -> validate
       - invalid -> one retry with the validator's errors as feedback
       - still invalid / timeout / API error -> use the template
  4. validate the final text once more and attach rationale + metadata

Determinism: the cache key is a hash of the prompts, so the same inputs return
the same stored answer; the template path is pure Python.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from app.compose import prompts, templates, validator
from app.compose.facts import FactSheet, build_fact_sheet
from app.config import settings
from app.llm.base import LLMError, LLMProvider, build_provider
from app.llm.cache import DiskCache, cache_key

log = logging.getLogger("vera.composer")

VALID_CTAS = {"binary_yes_no", "binary_confirm_cancel", "open_ended", "multi_choice_slot", "none"}


@dataclass
class ComposedMessage:
    body: str
    cta: str
    send_as: str
    suppression_key: str
    rationale: str
    template_name: str
    template_params: list[str]
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "body": self.body,
            "cta": self.cta,
            "send_as": self.send_as,
            "suppression_key": self.suppression_key,
            "rationale": self.rationale,
            "template_name": self.template_name,
            "template_params": self.template_params,
        }


class Composer:
    def __init__(self, provider: Optional[LLMProvider] = None, cache: Optional[DiskCache] = None,
                 use_default_provider: bool = True) -> None:
        self.provider = provider if provider is not None else (build_provider() if use_default_provider else None)
        self.cache = cache or DiskCache(settings.cache_dir)

    # ------------------------------------------------------------- public
    def compose(
        self,
        *,
        category: dict[str, Any],
        merchant: dict[str, Any],
        trigger: dict[str, Any],
        customer: Optional[dict[str, Any]],
        customer_facing: bool,
        consent_reason: Optional[str],
        now: datetime,
        decision_rationale: str = "",
        allow_llm: bool = True,
    ) -> ComposedMessage:
        sheet = build_fact_sheet(category=category, merchant=merchant, trigger=trigger, customer=customer,
                                 customer_facing=customer_facing, consent_reason=consent_reason, now=now)
        baseline = templates.render(sheet)
        path = "template"
        body, cta, why = baseline.body, baseline.cta, baseline.rationale
        errors: list[str] = []

        if allow_llm and self.provider is not None:
            llm_draft, llm_errors, llm_path = self._try_llm(sheet, baseline)
            if llm_draft is not None:
                body, cta, why, path = llm_draft["body"], llm_draft["cta"], llm_draft["rationale"], llm_path
            errors = llm_errors

        final_check = validator.validate(body, cta, sheet, sheet.recent_messages)
        if not final_check.ok and path != "template":
            body, cta, why, path = baseline.body, baseline.cta, baseline.rationale, "template"
            final_check = validator.validate(body, cta, sheet, sheet.recent_messages)
        if not final_check.ok:
            log.warning("template for %s failed validation: %s", sheet.kind, final_check.errors)

        rationale = _join_rationale(decision_rationale, why, path)
        return ComposedMessage(
            body=body,
            cta=cta,
            send_as=sheet.send_as,
            suppression_key=sheet.suppression_key,
            rationale=rationale,
            template_name=_template_name(sheet),
            template_params=_template_params(sheet, body),
            meta={
                "path": path,
                "prompt_version": prompts.PROMPT_VERSION,
                "context_hash": _context_hash(sheet),
                "llm_errors": errors,
                "validator_errors": final_check.errors,
            },
        )

    def compose_template_only(self, **kwargs: Any) -> ComposedMessage:
        return self.compose(allow_llm=False, **kwargs)

    # ------------------------------------------------------------ internals
    def _try_llm(self, sheet: FactSheet, baseline: templates.Draft) -> tuple[Optional[dict[str, str]], list[str], str]:
        assert self.provider is not None
        errors: list[str] = []
        feedback: Optional[str] = None
        for attempt in (1, 2):
            system, user = prompts.build_prompts(sheet, baseline, feedback)
            key = cache_key(self.provider.name, self.provider.model, prompts.PROMPT_VERSION, system, user)
            cached = self.cache.get(key)
            if cached is not None:
                return cached, errors, "cache"
            try:
                raw = self.provider.complete_json(system, user)
            except LLMError as exc:
                errors.append(f"attempt {attempt}: {exc}")
                return None, errors, "template"
            draft = _clean_draft(raw, baseline)
            check = validator.validate(draft["body"], draft["cta"], sheet, sheet.recent_messages)
            if check.ok:
                self.cache.set(key, draft)
                return draft, errors, "llm"
            errors.append(f"attempt {attempt}: " + "; ".join(check.errors))
            feedback = "; ".join(check.errors)
        return None, errors, "template"


# ---------------------------------------------------------------- helpers
def _clean_draft(raw: dict[str, Any], baseline: templates.Draft) -> dict[str, str]:
    body = str(raw.get("body") or "").strip()
    cta = str(raw.get("cta") or baseline.cta).strip()
    if cta not in VALID_CTAS:
        cta = baseline.cta
    rationale = str(raw.get("rationale") or baseline.rationale).strip()
    return {"body": body, "cta": cta, "rationale": rationale}


def _join_rationale(decision: str, message_why: str, path: str) -> str:
    parts = [p.strip().rstrip(".") for p in (decision, message_why) if p and p.strip()]
    text = ". ".join(parts)
    return f"{text}. [written by: {path}]" if text else f"[written by: {path}]"


def _template_name(sheet: FactSheet) -> str:
    prefix = "merchant" if sheet.send_as == "merchant_on_behalf" else "vera"
    kind = re.sub(r"[^a-z0-9_]", "_", sheet.kind.lower())
    return f"{prefix}_{kind}_v1"


def _template_params(sheet: FactSheet, body: str) -> list[str]:
    """{{1}} = recipient, {{2}} = message core, {{3}} = the ask (last sentence)."""
    sentences = re.split(r"(?<=[.!?])\s+", body.strip())
    ask = sentences[-1] if len(sentences) > 1 else ""
    core = " ".join(sentences[:-1]) if len(sentences) > 1 else body
    return [sheet.salutation, core, ask]


def _context_hash(sheet: FactSheet) -> str:
    blob = json.dumps(sheet.to_prompt_dict(), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
