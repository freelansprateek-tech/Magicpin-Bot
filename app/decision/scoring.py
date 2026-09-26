"""Score one candidate (merchant x trigger x optional customer).

Every component is a small, explainable integer so the rationale string can
say exactly why a candidate won. Weights (all additive):

  urgency           trigger.urgency (1-5) x 10                      10..50
  signal strength   trigger payload carries real data              +15
                    magnitude of a metric move (|delta| x 20, cap)  +0..10
  action ready      merchant already said yes (planning intent)    +20
  category fit      kind fits this category                        +10
                    kind clearly wrong for this category           -20
  offer match       merchant has an active service+price offer     +5
                    category catalog has a service+price offer     +3
  engagement        merchant replied recently                      +5
                    our last message is still unanswered (<24h)     -10
  expiry            trigger expires within 48h                     +5
  consent           customer gate failed -> merchant-facing note   -15
  contradiction     data contradicts the trigger (renewal far off)  -30
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from app.core.timeutil import parse_iso
from app.decision.kinds import category_fit, rule_for

RENEWAL_WINDOW_DAYS = 45

PRICE_RE = re.compile(r"₹\s?\d")


@dataclass
class Score:
    total: int = 0
    parts: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def add(self, name: str, points: int, note: Optional[str] = None) -> None:
        if points:
            self.parts[name] = self.parts.get(name, 0) + points
            self.total += points
        if note:
            self.notes.append(note)


def payload_has_data(payload: dict[str, Any]) -> bool:
    if not payload or payload.get("placeholder"):
        return False
    return any(v not in (None, "", [], {}) for k, v in payload.items() if k != "category")


def active_priced_offers(merchant: dict[str, Any]) -> list[str]:
    return [o.get("title", "") for o in merchant.get("offers") or []
            if o.get("status") == "active" and PRICE_RE.search(o.get("title", ""))]


def catalog_priced_offers(category: dict[str, Any]) -> list[str]:
    return [o.get("title", "") for o in category.get("offer_catalog") or []
            if o.get("type") == "service_at_price" and PRICE_RE.search(o.get("title", ""))]


def _last_history(merchant: dict[str, Any]) -> Optional[dict[str, Any]]:
    history = merchant.get("conversation_history") or []
    dated = [h for h in history if parse_iso(h.get("ts"))]
    return max(dated, key=lambda h: parse_iso(h["ts"])) if dated else (history[-1] if history else None)


def score_candidate(
    *,
    category: dict[str, Any],
    merchant: dict[str, Any],
    trigger: dict[str, Any],
    now: datetime,
    consent_blocked: bool,
) -> Score:
    s = Score()
    kind = trigger.get("kind", "unknown")
    scope = trigger.get("scope", "merchant")
    payload = trigger.get("payload") or {}

    # urgency
    try:
        urgency = max(1, min(5, int(trigger.get("urgency", 1))))
    except (TypeError, ValueError):
        urgency = 1
    s.add("urgency", urgency * 10, f"urgency {urgency}/5")

    # signal strength
    if payload_has_data(payload):
        s.add("signal", 15, "trigger carries concrete data")
        delta = payload.get("delta_pct")
        if isinstance(delta, (int, float)):
            s.add("magnitude", min(10, int(abs(delta) * 20)), f"metric moved {delta:+.0%}")
    else:
        s.notes.append("trigger payload has no specifics; anchoring on merchant/category facts")

    # action-ready intent
    if rule_for(kind, scope).action_ready:
        s.add("action_ready", 20, "merchant already asked for this")

    # category fit
    fit = category_fit(kind, scope, category.get("slug", ""), payload)
    if fit is True:
        s.add("category_fit", 10, f"{kind} fits {category.get('slug')}")
    elif fit is False:
        s.add("category_fit", -20, f"{kind} is unusual for {category.get('slug')}")

    # offer match
    if active_priced_offers(merchant):
        s.add("offer", 5, "merchant has an active service+price offer")
    elif catalog_priced_offers(category):
        s.add("offer", 3, "category catalog has a service+price offer")

    # engagement recency
    last = _last_history(merchant)
    signals = " ".join(merchant.get("signals") or [])
    if "engaged_in_last" in signals or (last and last.get("from") == "merchant"):
        s.add("engagement", 5, "merchant engaged recently")
    elif last and last.get("from") == "vera" and last.get("engagement") == "merchant_no_reply":
        ts = parse_iso(last.get("ts"))
        if ts and timedelta(0) <= now - ts < timedelta(hours=24):
            s.add("engagement", -10, "our last message is still unanswered")

    # data contradicts the trigger (e.g. "renewal due" but 200+ days left)
    if kind == "renewal_due":
        days = payload.get("days_remaining", (merchant.get("subscription") or {}).get("days_remaining"))
        if isinstance(days, (int, float)) and days > RENEWAL_WINDOW_DAYS:
            s.add("contradiction", -30, f"renewal not due yet ({int(days)} days left)")

    # expiry
    exp = parse_iso(trigger.get("expires_at"))
    if exp and timedelta(0) <= exp - now <= timedelta(hours=48):
        s.add("expiry", 5, "window closes within 48h")

    # consent
    if consent_blocked:
        s.add("consent", -15, "customer consent does not cover this; routing to merchant")

    return s
