"""Consent + status gates for customer-facing outreach.

A customer message is allowed only if ALL of these hold:
  1. the customer context exists (we never message someone we know nothing about)
  2. preferences.reminder_opt_in is not False
  3. consent.opted_in_at is set and consent.scope is non-empty
  4. consent.scope covers the purpose of this trigger kind (see kinds.py)
  5. state is not "churned" — unless they explicitly consented to win-back offers

If a gate fails, the planner does NOT drop the signal silently: it turns it into
a merchant-facing note (send_as="vera") so the merchant can decide.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.decision.kinds import rule_for


@dataclass(frozen=True)
class ConsentDecision:
    allowed: bool
    reason: str


def check_customer_consent(trigger: dict[str, Any], customer: Optional[dict[str, Any]]) -> ConsentDecision:
    if not customer:
        return ConsentDecision(False, "customer_context_missing")

    prefs = customer.get("preferences") or {}
    if prefs.get("reminder_opt_in") is False:
        return ConsentDecision(False, "customer_opted_out")

    consent = customer.get("consent") or {}
    scopes = set(consent.get("scope") or [])
    if not consent.get("opted_in_at") or not scopes:
        return ConsentDecision(False, "no_recorded_consent")

    required = rule_for(trigger.get("kind", ""), "customer").consent_scopes
    if required and not (scopes & required):
        return ConsentDecision(False, f"consent_scope_mismatch(has={sorted(scopes)})")

    if customer.get("state") == "churned" and "winback_offers" not in scopes:
        return ConsentDecision(False, "customer_churned")

    return ConsentDecision(True, "consent_ok")
