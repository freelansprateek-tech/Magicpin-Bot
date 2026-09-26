"""Registry of trigger kinds: which categories they fit and which consent they need.

Unknown kinds (the judge will inject new ones) fall back to DEFAULT_RULE, so
nothing here is required for the bot to work — it only sharpens decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class KindRule:
    family: str                              # knowledge | compliance | event | performance |
                                             # account | relationship | planning | customer
    categories: Optional[frozenset[str]] = None   # None = fits every category
    consent_scopes: frozenset[str] = field(default_factory=frozenset)  # customer kinds only
    action_ready: bool = False               # merchant already asked -> deliver, don't pitch


def _cats(*slugs: str) -> frozenset[str]:
    return frozenset(slugs)


KIND_RULES: dict[str, KindRule] = {
    # ---- merchant-facing, external
    "research_digest": KindRule("knowledge"),
    "research_digest_release": KindRule("knowledge"),
    "category_research_digest_release": KindRule("knowledge"),
    "cde_opportunity": KindRule("knowledge"),
    "category_trend_movement": KindRule("knowledge"),
    "regulation_change": KindRule("compliance"),
    "supply_alert": KindRule("compliance", _cats("pharmacies")),
    "festival_upcoming": KindRule("event"),
    "ipl_match_today": KindRule("event", _cats("restaurants")),
    "weather_heatwave": KindRule("event"),
    "local_news_event": KindRule("event"),
    "category_seasonal": KindRule("event"),
    "competitor_opened": KindRule("event"),
    # ---- merchant-facing, internal
    "perf_dip": KindRule("performance"),
    "perf_spike": KindRule("performance"),
    "seasonal_perf_dip": KindRule("performance"),
    "milestone_reached": KindRule("performance"),
    "review_theme_emerged": KindRule("performance"),
    "renewal_due": KindRule("account"),
    "winback_eligible": KindRule("account"),
    "gbp_unverified": KindRule("account"),
    "dormant_with_vera": KindRule("relationship"),
    "curious_ask_due": KindRule("relationship"),
    "scheduled_recurring": KindRule("relationship"),
    "active_planning_intent": KindRule("planning", action_ready=True),
    # ---- customer-facing (send_as = merchant_on_behalf)
    "recall_due": KindRule("customer", _cats("dentists"),
                           frozenset({"recall_reminders", "appointment_reminders"})),
    "appointment_tomorrow": KindRule("customer", None,
                                     frozenset({"appointment_reminders"})),
    "chronic_refill_due": KindRule("customer", _cats("pharmacies"),
                                   frozenset({"refill_reminders"})),
    "trial_followup": KindRule("customer", _cats("gyms", "salons"),
                               frozenset({"kids_program_updates", "program_updates",
                                          "appointment_reminders", "promotional_offers"})),
    "wedding_package_followup": KindRule("customer", _cats("salons"),
                                         frozenset({"bridal_package_followup",
                                                    "appointment_reminders"})),
    "bridal_followup": KindRule("customer", _cats("salons"),
                                frozenset({"bridal_package_followup", "appointment_reminders"})),
    "customer_lapsed_soft": KindRule("customer", None,
                                     frozenset({"winback_offers", "promotional_offers",
                                                "recall_reminders"})),
    "customer_lapsed_hard": KindRule("customer", None,
                                     frozenset({"winback_offers", "promotional_offers",
                                                "recall_reminders"})),
    "unplanned_slot_open": KindRule("customer", None,
                                    frozenset({"appointment_reminders", "promotional_offers"})),
}

DEFAULT_MERCHANT_RULE = KindRule("other")
DEFAULT_CUSTOMER_RULE = KindRule("customer", None, frozenset({"promotional_offers"}))


def rule_for(kind: str, scope: str = "merchant") -> KindRule:
    rule = KIND_RULES.get(kind)
    if rule is not None:
        return rule
    return DEFAULT_CUSTOMER_RULE if scope == "customer" else DEFAULT_MERCHANT_RULE


def category_fit(kind: str, scope: str, category_slug: str, payload: dict) -> Optional[bool]:
    """True = strong fit, False = mismatch, None = neutral (fits anything)."""
    relevance = payload.get("category_relevance")
    if isinstance(relevance, list) and relevance:
        return category_slug in relevance
    rule = rule_for(kind, scope)
    if rule.categories is None:
        return None
    return category_slug in rule.categories
