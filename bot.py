"""Submission entry point required by challenge-brief.md §7.1.

    from bot import compose
    msg = compose(category, merchant, trigger, customer)   # dicts from the dataset

Returns: body, cta, send_as, suppression_key, rationale
         (+ template_name, template_params for the first-touch template).

Same pipeline as the HTTP bot: consent gate -> fact sheet -> LLM (if configured)
-> grounding validator -> deterministic template fallback.
Deterministic: time-relative facts use `now` (default: the brief's scenario
date), never the machine clock.
"""

from __future__ import annotations

from typing import Any, Optional

from app.compose.composer import Composer
from app.core.timeutil import DEFAULT_NOW, now_from
from app.decision.consent import check_customer_consent
from app.decision.planner import Candidate
from app.decision.scoring import score_candidate

_composer: Optional[Composer] = None


def _get_composer() -> Composer:
    global _composer
    if _composer is None:
        _composer = Composer()
    return _composer


def compose(category: dict, merchant: dict, trigger: dict, customer: dict | None = None,
            now: str | None = None) -> dict[str, Any]:
    now_dt = now_from(now or DEFAULT_NOW)

    customer_facing = False
    consent_reason = None
    if customer is not None or trigger.get("scope") == "customer":
        decision = check_customer_consent(trigger, customer)
        customer_facing, consent_reason = decision.allowed, decision.reason

    score = score_candidate(category=category, merchant=merchant, trigger=trigger, now=now_dt,
                            consent_blocked=customer is not None and not customer_facing)
    candidate = Candidate(str(trigger.get("id", "")), trigger, merchant, category, customer,
                          customer_facing, consent_reason, score)

    message = _get_composer().compose(
        category=category, merchant=merchant, trigger=trigger, customer=customer,
        customer_facing=customer_facing, consent_reason=consent_reason, now=now_dt,
        decision_rationale=candidate.rationale(),
    )
    return message.to_dict()
