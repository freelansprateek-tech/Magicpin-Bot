"""Fact sheet: the ONLY facts a message is allowed to use.

The composer never hands raw contexts to the LLM. It builds this small,
pre-formatted sheet instead, because:
  * smaller prompt -> faster, cheaper, less room to wander
  * numbers are pre-formatted ("2.1%", "₹4,999", "2,100") so the LLM copies
    them instead of re-computing (a big source of hallucinated numbers)
  * derived values (days to deadline, months since last visit) are computed
    HERE from the judge's `now`, so they are grounded and deterministic
  * the grounding validator checks message numbers against this sheet
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from app.core.timeutil import days_between, human_date, human_time, parse_iso, to_ist
from app.compose import voice as V
from app.decision.scoring import payload_has_data

PRICE_RE = re.compile(r"₹\s?[\d,]+")


# ---------------------------------------------------------------- formatting
def fmt_num(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.1f}".rstrip("0").rstrip(".")
    return f"{int(value):,}"


def fmt_inr(value: Any) -> str:
    try:
        return f"₹{int(float(value)):,}"
    except (TypeError, ValueError):
        return str(value)


def fmt_pct(ratio: Any, signed: bool = False) -> Optional[str]:
    if not isinstance(ratio, (int, float)) or isinstance(ratio, bool):
        return None
    pct = ratio * 100
    text = f"{pct:.1f}".rstrip("0").rstrip(".")
    if signed and pct > 0:
        text = "+" + text
    return text + "%"


def humanize(token: Any) -> str:
    return str(token).replace("_", " ").strip()


# ------------------------------------------------------------------- sheet
@dataclass
class FactSheet:
    kind: str
    trigger_id: str
    suppression_key: str
    category_slug: str
    audience: str                      # "merchant" | "customer"
    send_as: str                       # "vera" | "merchant_on_behalf"
    language: str                      # "hi-en" | "en"
    tone: str
    tone_guide: str
    taboos: list[str]
    vocab: list[str]
    salutation: str                    # how to address the recipient
    merchant: dict[str, Any] = field(default_factory=dict)
    performance: dict[str, Any] = field(default_factory=dict)
    peer: dict[str, Any] = field(default_factory=dict)
    offers: dict[str, Any] = field(default_factory=dict)
    customer_aggregate: dict[str, Any] = field(default_factory=dict)
    review_themes: list[dict[str, Any]] = field(default_factory=list)
    trigger: dict[str, Any] = field(default_factory=dict)
    digest_item: Optional[dict[str, Any]] = None
    customer: Optional[dict[str, Any]] = None
    recent_messages: list[str] = field(default_factory=list)
    consent_note: Optional[str] = None
    consent_reason: Optional[str] = None        # machine-readable gate result, for templates
    has_trigger_data: bool = True

    def to_prompt_dict(self) -> dict[str, Any]:
        out = {
            "trigger_kind": self.kind,
            "audience": self.audience,
            "send_as": self.send_as,
            "language": self.language,
            "salutation": self.salutation,
            "merchant": self.merchant,
            "performance_30d": self.performance,
            "peer_benchmarks": self.peer,
            "offers": self.offers,
            "customer_aggregate": self.customer_aggregate,
            "review_themes": self.review_themes,
            "trigger_facts": self.trigger,
            "digest_item": self.digest_item,
            "customer": self.customer,
            "recent_messages_do_not_repeat": self.recent_messages,
            "consent_note": self.consent_note,
        }
        return {k: v for k, v in out.items() if v not in (None, {}, [])}


# ------------------------------------------------------------------ builders
def _merchant_block(merchant: dict[str, Any]) -> dict[str, Any]:
    ident = merchant.get("identity") or {}
    sub = merchant.get("subscription") or {}
    block = {
        "name": ident.get("name"),
        "owner_first_name": V.strip_title(str(ident.get("owner_first_name") or "")) or None,
        "locality": ident.get("locality"),
        "city": ident.get("city"),
        "google_profile_verified": ident.get("verified"),
        "subscription_status": sub.get("status"),
        "plan": sub.get("plan"),
    }
    if sub.get("days_remaining") not in (None, 0):
        block["subscription_days_remaining"] = sub.get("days_remaining")
    if sub.get("days_since_expiry"):
        block["days_since_subscription_expired"] = sub.get("days_since_expiry")
    signals = [humanize(s) for s in merchant.get("signals") or []]
    if signals:
        block["signals"] = signals
    return {k: v for k, v in block.items() if v not in (None, "")}


def _performance_block(merchant: dict[str, Any]) -> dict[str, Any]:
    perf = merchant.get("performance") or {}
    block: dict[str, Any] = {}
    for key in ("views", "calls", "directions", "leads"):
        if isinstance(perf.get(key), (int, float)):
            block[key] = fmt_num(perf[key])
    if perf.get("ctr") is not None:
        block["ctr"] = fmt_pct(perf.get("ctr"))
    delta = perf.get("delta_7d") or {}
    for key, value in delta.items():
        label = key.replace("_pct", "") + "_change_7d"
        pct = fmt_pct(value, signed=True)
        if pct:
            block[label] = pct
    return block


def _peer_block(category: dict[str, Any]) -> dict[str, Any]:
    peer = category.get("peer_stats") or {}
    block: dict[str, Any] = {}
    for key, value in peer.items():
        if key == "scope":
            block["scope"] = humanize(value)
        elif key.endswith("_pct") or key == "avg_ctr":
            block[key] = fmt_pct(value)
        elif isinstance(value, (int, float)):
            block[key] = fmt_num(value)
    return block


def _offers_block(category: dict[str, Any], merchant: dict[str, Any]) -> dict[str, Any]:
    offers = merchant.get("offers") or []
    active = [o.get("title") for o in offers if o.get("status") == "active" and o.get("title")]
    inactive = [f"{o.get('title')} ({o.get('status')})" for o in offers
                if o.get("status") != "active" and o.get("title")]
    priced_active = [t for t in active if PRICE_RE.search(t)]
    catalog = category.get("offer_catalog") or []
    catalog_priced = [o.get("title") for o in catalog
                      if o.get("type") == "service_at_price" and o.get("title")]
    block: dict[str, Any] = {}
    if active:
        block["active"] = active
    if inactive:
        block["inactive_do_not_promote"] = inactive
    best = priced_active[0] if priced_active else (active[0] if active else None)
    if best:
        block["best_active_offer"] = best
    if catalog_priced:
        # Suggestions from the category catalog — Vera may PROPOSE these,
        # but must never claim the merchant already runs them.
        block["catalog_suggestions"] = catalog_priced[:6]
    return block


def _customer_aggregate_block(merchant: dict[str, Any]) -> dict[str, Any]:
    agg = merchant.get("customer_aggregate") or {}
    out: dict[str, Any] = {}
    for key, value in agg.items():
        if isinstance(value, bool):
            continue
        if key.endswith("_pct") and isinstance(value, (int, float)):
            out[key] = fmt_pct(value)
        elif isinstance(value, (int, float)):
            out[key] = fmt_num(value)
    return out


def _review_block(merchant: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for theme in merchant.get("review_themes") or []:
        item = {"theme": humanize(theme.get("theme", "")), "sentiment": theme.get("sentiment")}
        if theme.get("occurrences_30d") is not None:
            item["mentions_30d"] = fmt_num(theme["occurrences_30d"])
        if theme.get("common_quote"):
            item["quote"] = theme["common_quote"]
        out.append(item)
    return out


def _find_digest(category: dict[str, Any], payload: dict[str, Any], kind: str) -> Optional[dict[str, Any]]:
    digest = category.get("digest") or []
    by_id = {d.get("id"): d for d in digest}
    for key in ("top_item_id", "digest_item_id", "alert_id", "item_id"):
        if payload.get(key) in by_id:
            return by_id[payload[key]]
    # Placeholder triggers: fall back to this week's category item of the matching kind
    # (still real category knowledge, so nothing is invented).
    by_kind = {"research_digest": ("research", "trend", "tech"), "research_digest_release": ("research", "trend", "tech"),
               "category_research_digest_release": ("research", "trend", "tech"), "regulation_change": ("compliance",),
               "cde_opportunity": ("cde",), "supply_alert": ("alert", "supply"),
               "category_trend_movement": ("trend",)}.get(kind)
    if by_kind:
        for wanted in by_kind:
            for item in digest:
                if item.get("kind") == wanted:
                    return item
    # A few kinds have an obviously related digest item even without an id.
    hints = {
        "ipl_match_today": ("ipl",),
        "category_seasonal": ("seasonal", "summer"),
        "seasonal_perf_dip": ("seasonal", "resolution"),
    }.get(kind, ())
    for item in digest:
        text = f"{item.get('id', '')} {item.get('kind', '')} {item.get('title', '')}".lower()
        if any(h in text for h in hints):
            return item
    return None


def _digest_block(item: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not item:
        return None
    block = {k: item.get(k) for k in ("title", "source", "summary", "actionable",
                                       "patient_segment", "credits", "kind")
             if item.get(k) not in (None, "")}
    if item.get("trial_n") is not None:
        block["trial_n"] = fmt_num(item["trial_n"])
    if item.get("patient_segment"):
        block["patient_segment"] = humanize(item["patient_segment"])
    if item.get("date"):
        dt = to_ist(item["date"])
        if dt:
            has_time = "T" in str(item["date"])
            block["date"] = human_date(dt) + (f", {human_time(dt)}" if has_time else "")
    return block


def _trigger_block(kind: str, payload: dict[str, Any], now: datetime,
                   customer: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Kind-specific, pre-formatted, now-relative facts from the trigger payload."""
    t: dict[str, Any] = {}
    p = payload or {}

    # Generic metric moves (perf_dip / perf_spike / seasonal_perf_dip / winback)
    if p.get("metric"):
        t["metric"] = humanize(p["metric"])
    if isinstance(p.get("delta_pct"), (int, float)):
        t["change"] = fmt_pct(p["delta_pct"], signed=True)
    if p.get("window"):
        t["window"] = str(p["window"]).replace("d", " days") if str(p["window"]).endswith("d") else p["window"]
    if p.get("vs_baseline") is not None:
        t["baseline_value"] = fmt_num(p["vs_baseline"])
    if p.get("likely_driver"):
        t["likely_driver"] = humanize(p["likely_driver"])
    if p.get("is_expected_seasonal"):
        t["expected_seasonal"] = True
    if p.get("season_note"):
        t["season_note"] = humanize(p["season_note"])

    # Deadlines / dates relative to now
    if p.get("deadline_iso"):
        t["deadline"] = human_date(p["deadline_iso"])
        days = days_between(now, parse_iso(p["deadline_iso"]))
        if days is not None and days >= 0:
            t["days_to_deadline"] = days
    if p.get("festival"):
        t["festival"] = p["festival"]
        if p.get("date"):
            t["festival_date"] = human_date(p["date"])
            days = days_between(now, parse_iso(p["date"]))
            if days is not None and days >= 0:
                t["days_until_festival"] = days

    # Account / subscription
    if p.get("days_remaining") is not None:
        t["days_remaining"] = p["days_remaining"]
    if p.get("plan"):
        t["plan"] = p["plan"]
    if p.get("renewal_amount") is not None:
        t["renewal_amount"] = fmt_inr(p["renewal_amount"])
    if p.get("days_since_expiry") is not None:
        t["days_since_expiry"] = p["days_since_expiry"]
    if isinstance(p.get("perf_dip_pct"), (int, float)):
        t["performance_change_since_expiry"] = fmt_pct(p["perf_dip_pct"], signed=True)
    if p.get("lapsed_customers_added_since_expiry") is not None:
        t["customers_lapsed_since_expiry"] = p["lapsed_customers_added_since_expiry"]
    if "verified" in p and p.get("verified") is False:
        t["google_profile_verified"] = False
    if p.get("verification_path"):
        t["verification_path"] = humanize(p["verification_path"]).replace(" or ", " or a ")
    if isinstance(p.get("estimated_uplift_pct"), (int, float)):
        t["estimated_uplift"] = fmt_pct(p["estimated_uplift_pct"])

    # Relationship
    if p.get("days_since_last_merchant_message") is not None:
        t["days_since_merchant_last_replied"] = p["days_since_last_merchant_message"]
    if p.get("last_topic"):
        t["last_topic"] = humanize(p["last_topic"])
    if p.get("ask_template"):
        t["ask"] = humanize(p["ask_template"])
    if p.get("intent_topic"):
        t["intent_topic"] = humanize(p["intent_topic"])
    if p.get("merchant_last_message"):
        t["merchant_last_message"] = p["merchant_last_message"]

    # Milestones / reviews
    if p.get("value_now") is not None:
        t["current_value"] = fmt_num(p["value_now"])
    if p.get("milestone_value") is not None:
        t["milestone"] = fmt_num(p["milestone_value"])
        if isinstance(p.get("value_now"), (int, float)):
            gap = p["milestone_value"] - p["value_now"]
            if gap > 0:
                t["remaining_to_milestone"] = fmt_num(gap)
    if p.get("theme"):
        t["review_theme"] = humanize(p["theme"])
    if p.get("occurrences_30d") is not None:
        t["mentions_30d"] = fmt_num(p["occurrences_30d"])
    if p.get("trend"):
        t["trend"] = p["trend"]
    if p.get("common_quote"):
        t["quote"] = p["common_quote"]

    # Events
    if p.get("match"):
        t["match"] = p["match"]
    if p.get("venue"):
        t["venue"] = p["venue"]
    if p.get("match_time_iso"):
        dt = to_ist(p["match_time_iso"])
        if dt:
            t["match_time"] = human_time(dt)
            t["match_day"] = dt.strftime("%A")
            t["match_is_today"] = days_between(now, dt) == 0
    if "is_weeknight" in p:
        t["is_weeknight"] = bool(p["is_weeknight"])
    if p.get("competitor_name"):
        t["competitor_name"] = p["competitor_name"]
    if p.get("distance_km") is not None:
        t["distance_km"] = fmt_num(p["distance_km"])
    if p.get("their_offer"):
        t["competitor_offer"] = p["their_offer"]
    if p.get("opened_date"):
        t["competitor_opened"] = human_date(p["opened_date"])
    if p.get("season"):
        t["season"] = humanize(p["season"])
    if p.get("trends"):
        pretty = []
        for tr in p["trends"]:
            m = re.match(r"^(.*?)_demand_([+-]\d+)$", str(tr))
            pretty.append(f"{humanize(m.group(1))} demand {m.group(2)}%" if m else humanize(tr))
        t["demand_shifts"] = pretty

    # Pharmacy alerts / refills
    if p.get("molecule"):
        t["molecule"] = p["molecule"]
    if p.get("affected_batches"):
        t["affected_batches"] = list(p["affected_batches"])
    if p.get("manufacturer"):
        t["manufacturer"] = p["manufacturer"]
    if p.get("molecule_list"):
        t["medicines"] = list(p["molecule_list"])
    if p.get("stock_runs_out_iso"):
        t["runs_out_on"] = human_date(p["stock_runs_out_iso"])
    if p.get("delivery_address_saved"):
        t["delivery_address_saved"] = True

    # CDE
    if p.get("credits") is not None:
        t["credits"] = p["credits"]
    if p.get("fee"):
        t["fee"] = humanize(p["fee"])

    # Customer-scoped
    if p.get("service_due"):
        t["service_due"] = humanize(p["service_due"]).replace("6 month", "6-month")
    if p.get("due_date"):
        t["due_date"] = human_date(p["due_date"])
    if p.get("available_slots"):
        t["slots"] = [s.get("label") for s in p["available_slots"] if s.get("label")]
    if p.get("next_session_options"):
        t["slots"] = [s.get("label") for s in p["next_session_options"] if s.get("label")]
    if p.get("trial_date"):
        t["trial_date"] = human_date(p["trial_date"])
    if p.get("wedding_date"):
        t["wedding_date"] = human_date(p["wedding_date"])
        days = days_between(now, parse_iso(p["wedding_date"]))
        if days is not None and days >= 0:
            t["days_to_wedding"] = days
    if p.get("next_step_window_open"):
        step = humanize(p["next_step_window_open"])
        m = re.match(r"^(.*?)\s*(\d+)\s*day$", step)
        t["next_step"] = f"{m.group(2)}-day {m.group(1)}" if m else step
    if p.get("days_since_last_visit") is not None:
        t["days_since_last_visit"] = p["days_since_last_visit"]
        t["weeks_since_last_visit"] = round(p["days_since_last_visit"] / 7)
    if p.get("previous_focus"):
        t["previous_focus"] = humanize(p["previous_focus"])
    if p.get("previous_membership_months") is not None:
        t["previous_membership_months"] = p["previous_membership_months"]
    if p.get("last_service_date"):
        last = parse_iso(p["last_service_date"])
        days = days_between(last, now)
        if days is not None and days > 0:
            t["months_since_last_service"] = max(1, round(days / 30.4))

    # Anything the kind-specific code above didn't use (new trigger kinds the judge
    # injects, e.g. weather_heatwave {"temperature_c": 44}) is still passed on, in
    # readable form, so the message can name the actual event instead of ignoring it.
    extra = _event_details({k: v for k, v in p.items() if k not in KNOWN_PAYLOAD_KEYS})
    if extra:
        t["event_details"] = extra
    return t


# Payload keys handled explicitly above, or pure plumbing (ids, placeholders).
KNOWN_PAYLOAD_KEYS = {
    "metric", "delta_pct", "window", "vs_baseline", "likely_driver", "is_expected_seasonal", "season_note",
    "deadline_iso", "festival", "date", "days_until", "days_remaining", "plan", "renewal_amount",
    "days_since_expiry", "perf_dip_pct", "lapsed_customers_added_since_expiry", "verified",
    "verification_path", "estimated_uplift_pct", "days_since_last_merchant_message", "last_topic",
    "ask_template", "last_ask_at", "intent_topic", "merchant_last_message", "value_now", "milestone_value",
    "is_imminent", "theme", "occurrences_30d", "trend", "common_quote", "match", "venue", "city",
    "match_time_iso", "is_weeknight", "competitor_name", "distance_km", "their_offer", "opened_date",
    "season", "trends", "shelf_action_recommended", "molecule", "affected_batches", "manufacturer",
    "molecule_list", "last_refill", "stock_runs_out_iso", "delivery_address_saved", "credits", "fee",
    "service_due", "due_date", "last_service_date", "available_slots", "next_session_options",
    "trial_date", "trial_completed", "wedding_date", "days_to_wedding", "next_step_window_open",
    "days_since_last_visit", "previous_focus", "previous_membership_months",
    # plumbing
    "category", "category_relevance", "top_item_id", "digest_item_id", "alert_id", "placeholder",
    "metric_or_topic", "merchant_id", "customer_id", "patient_id", "id",
}


def _event_details(extra: dict[str, Any]) -> dict[str, str]:
    """Format unknown payload fields: {'temperature_c': 44} -> {'temperature': '44°C'}."""
    out: dict[str, str] = {}
    for key in sorted(extra):
        value = extra[key]
        if value is None or isinstance(value, (bool, dict)):
            continue
        k = str(key).lower()
        label = humanize(re.sub(r"_(iso|c|km|pct|days)$", "", k))
        if isinstance(value, list):
            items = [humanize(v) for v in value if isinstance(v, (str, int, float)) and not isinstance(v, bool)]
            if items:
                out[label] = ", ".join(items[:6])
            continue
        if isinstance(value, (int, float)):
            if k.endswith("_pct"):
                out[label] = fmt_pct(value, signed=True) or fmt_num(value)
            elif k.endswith("_c") or "temp" in k:
                out[label] = f"{fmt_num(value)}°C"
            elif k.endswith("_km"):
                out[label] = f"{fmt_num(value)} km"
            elif k.endswith("_days"):
                out[label] = f"{fmt_num(value)} days"
            else:
                out[label] = fmt_num(value)
            continue
        text = str(value).strip()
        if not text:
            continue
        if k.endswith("_iso") or "date" in k or re.match(r"^\d{4}-\d{2}-\d{2}", text):
            when = human_date(text)
            if when:
                text = when + (f", {human_time(text)}" if "T" in text else "")
        elif re.fullmatch(r"[a-z0-9_]+", text):
            text = humanize(text)
        out[label] = text[:160]
    return out


MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def _beat_months(month_range: str) -> set[int]:
    """'Nov-Feb' -> {11,12,1,2}; 'Jan' -> {1}; 'Feb 14' -> {2}."""
    found = [MONTHS.index(m) + 1 for m in re.findall(r"[a-z]{3}", str(month_range).lower()) if m in MONTHS]
    if not found:
        return set()
    if len(found) == 1:
        return {found[0]}
    start, end = found[0], found[-1]
    months, m = {start}, start
    while m != end:
        m = m % 12 + 1
        months.add(m)
    return months


def upcoming_season(category: dict[str, Any], now: datetime) -> Optional[str]:
    """The category's seasonal beat that is on now or starts within ~2 months."""
    horizon = [((now.month - 1 + k) % 12) + 1 for k in range(0, 3)]
    for k, month in enumerate(horizon):
        for beat in category.get("seasonal_beats") or []:
            if month in _beat_months(beat.get("month_range", "")):
                return f"{beat.get('month_range')}: {beat.get('note')}"
    return None


def _customer_block(customer: dict[str, Any], now: datetime) -> dict[str, Any]:
    addressee, subject = V.customer_names(customer)
    rel = customer.get("relationship") or {}
    prefs = customer.get("preferences") or {}
    block: dict[str, Any] = {
        "address_as": addressee,
        "about": subject,                      # child's name when a parent is addressed
        "state": humanize(customer.get("state", "")),
        "language_pref": (customer.get("identity") or {}).get("language_pref"),
        "senior_citizen": (customer.get("identity") or {}).get("senior_citizen") or None,
        "visits_total": rel.get("visits_total"),
        "preferred_slots": humanize(prefs.get("preferred_slots")) if prefs.get("preferred_slots") else None,
        "channel": humanize(prefs.get("channel")) if prefs.get("channel") else None,
        "favourite": rel.get("favourite_dish"),
        "training_focus": humanize(prefs.get("training_focus")) if prefs.get("training_focus") else None,
    }
    services = [humanize(s) for s in rel.get("services_received") or [] if s and s != "..."]
    if services:
        block["recent_services"] = services[-3:]
    last = parse_iso(rel.get("last_visit"))
    days = days_between(last, now)
    if last and days is not None and days > 0:
        block["last_visit"] = human_date(rel.get("last_visit"))
        block["months_since_last_visit"] = max(1, round(days / 30.4)) if days >= 30 else None
        block["days_since_last_visit"] = days
    return {k: v for k, v in block.items() if v not in (None, "", [])}


# -------------------------------------------------------------------- entry
def build_fact_sheet(
    *,
    category: dict[str, Any],
    merchant: dict[str, Any],
    trigger: dict[str, Any],
    customer: Optional[dict[str, Any]],
    customer_facing: bool,
    consent_reason: Optional[str],
    now: datetime,
) -> FactSheet:
    kind = str(trigger.get("kind") or "unknown")
    payload = trigger.get("payload") or {}
    slug = category.get("slug") or merchant.get("category_slug") or ""
    tone = V.tone_of(category)

    if customer_facing and customer:
        audience, send_as = "customer", "merchant_on_behalf"
        language = V.customer_language(customer)
        addressee, _ = V.customer_names(customer)
        senior = (customer.get("identity") or {}).get("senior_citizen")
        salutation = "Namaste" if senior else (addressee or "there")
    else:
        audience, send_as = "merchant", "vera"
        language = V.merchant_language(merchant)
        salutation = V.merchant_salutation(category, merchant)

    history = [h.get("body", "") for h in (merchant.get("conversation_history") or [])
               if h.get("from") == "vera" and h.get("body")]

    consent_note = None
    if customer is not None and not customer_facing:
        addressee, subject = V.customer_names(customer)
        who = subject or addressee or "one of your customers"
        consent_note = (f"{who}'s {humanize(kind)} is due, but their WhatsApp consent "
                        f"does not cover this message ({consent_reason}). Ask the merchant, don't message the customer.")
    elif customer is None and trigger.get("scope") == "customer":
        consent_note = ("A customer-level event fired but no customer profile was shared, "
                        "so address the merchant only and do not invent customer details.")

    trigger_facts = _trigger_block(kind, payload, now, customer)
    if kind in {"festival_upcoming", "category_seasonal", "seasonal_perf_dip"} and not trigger_facts.get("festival"):
        season = upcoming_season(category, now)
        if season:
            trigger_facts["upcoming_season"] = season

    return FactSheet(
        kind=kind,
        trigger_id=str(trigger.get("id") or ""),
        suppression_key=str(trigger.get("suppression_key") or f"{kind}:{merchant.get('merchant_id', '')}"),
        category_slug=slug,
        audience=audience,
        send_as=send_as,
        language=language,
        tone=tone,
        tone_guide=V.TONE_GUIDE.get(tone, ""),
        taboos=V.taboos_of(category),
        vocab=V.vocab_of(category),
        salutation=salutation,
        merchant=_merchant_block(merchant),
        performance=_performance_block(merchant),
        peer=_peer_block(category),
        offers=_offers_block(category, merchant),
        customer_aggregate=_customer_aggregate_block(merchant),
        review_themes=_review_block(merchant),
        trigger=trigger_facts,
        digest_item=_digest_block(_find_digest(category, payload, kind)),
        customer=_customer_block(customer, now) if customer else None,
        recent_messages=history[-3:],
        consent_note=consent_note,
        consent_reason=consent_reason,
        has_trigger_data=payload_has_data(payload),
    )
