"""Deterministic template messages — one per trigger kind, plus safe fallbacks.

Used when (a) no LLM is configured, (b) the LLM is slow or down, or (c) the
LLM draft fails the validator twice. They are also shown to the LLM as the
grounded baseline it must improve on.

Rules every template follows:
  * only facts from the FactSheet (so the validator always passes)
  * recipient's name first, the "why now" next, ONE ask in the last sentence
  * degrade gracefully: if a trigger has no data (placeholder), anchor on the
    merchant's own numbers instead of inventing specifics
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Callable, Optional

from app.compose.facts import FactSheet


@dataclass
class Draft:
    body: str
    cta: str
    rationale: str


# ------------------------------------------------------------------ helpers
def _join(*parts: Optional[str]) -> str:
    text = " ".join(p.strip() for p in parts if p and p.strip())
    return re.sub(r"\s+([,.?!])", r"\1", re.sub(r"\s{2,}", " ", text)).strip()


def _dot(text: Optional[str]) -> Optional[str]:
    """Ensure a fragment ends with exactly one sentence terminator."""
    if not text:
        return None
    text = text.strip()
    return text if text[-1] in ".!?" else text + "."


ABBREVIATIONS = ("dr.", "mr.", "mrs.", "ms.", "vs.", "no.", "st.", "approx.", "e.g.", "i.e.")


def _first_sentence(text: Optional[str]) -> Optional[str]:
    """First sentence, without splitting on abbreviations like 'Dr.' or 'vs.'."""
    if not text:
        return None
    text = text.strip()
    for m in re.finditer(r"[.!?](\s+|$)", text):
        candidate = text[: m.start() + 1]
        last_word = candidate.split()[-1].lower() if candidate.split() else ""
        if last_word in ABBREVIATIONS or re.fullmatch(r"[a-z]\.", last_word):
            continue
        return candidate
    return text


def _change(text: Optional[str]) -> Optional[str]:
    """'-21%' -> 'down 21%', '+15%' -> 'up 15%'."""
    if not text:
        return None
    return f"down {text.lstrip('-')}" if text.startswith("-") else f"up {text.lstrip('+')}"


KIND_LABELS = {
    "competitor_opened": "competitor check",
    "festival_upcoming": "festival planning note",
    "milestone_reached": "profile check",
    "perf_dip": "performance check",
    "perf_spike": "performance check",
    "review_theme_emerged": "check-in",
    "research_digest": "research note",
    "renewal_due": "plan renewal note",
}

CONSENT_PHRASES = {
    "recall_due": "a recall reminder",
    "appointment_tomorrow": "an appointment reminder",
    "chronic_refill_due": "a refill reminder",
    "customer_lapsed_soft": "a win-back message",
    "customer_lapsed_hard": "a win-back message",
    "trial_followup": "a trial follow-up",
    "wedding_package_followup": "a bridal follow-up",
}

RENEWAL_WINDOW_DAYS = 45   # a renewal nudge earlier than this reads as spam

AUDIENCE_WORD = {
    "dentists": "patient", "pharmacies": "customer", "gyms": "member",
    "salons": "client", "restaurants": "guest",
}

HOLD_PHRASES = {
    "dentists": "hold a slot for you",
    "salons": "hold a slot for you",
    "gyms": "hold a spot for you",
    "restaurants": "reserve a table for you",
    "pharmacies": "keep your usual order ready",
}


HINGLISH_YES = (
    "Bas YES reply kar dijiye.",
    "YES bhej dijiye, baaki main sambhal lungi.",
    "Reply YES — baaki kaam mera.",
)


def _pick(options: tuple[str, ...], *keys: object) -> str:
    """Deterministic choice (same inputs -> same wording), so 50 merchants don't
    all get an identical closing line. Uses sha1, not hash(), which is randomised per process."""
    digest = hashlib.sha1("|".join(str(k) for k in keys).encode("utf-8")).hexdigest()
    return options[int(digest[:8], 16) % len(options)]


def _ask_yes(s: FactSheet, action: str) -> str:
    if s.language == "hi-en":
        return f"Want me to {action}? {_pick(HINGLISH_YES, s.salutation, s.kind, action)}"
    return f"Want me to {action}? Reply YES."


def _ask_open(s: FactSheet, question: str) -> str:
    return question if s.language != "hi-en" else f"{question} Bata dijiye."


def _offer(s: FactSheet) -> Optional[str]:
    return s.offers.get("best_active_offer")


def _suggestion(s: FactSheet) -> Optional[str]:
    sugg = s.offers.get("catalog_suggestions") or []
    return sugg[0] if sugg else None


def _anchor(s: FactSheet) -> str:
    """One verifiable fact about this merchant, for triggers without data."""
    perf, peer = s.performance, s.peer
    if perf.get("ctr") and peer.get("avg_ctr") and _pct_val(perf["ctr"]) < _pct_val(peer["avg_ctr"]):
        return f"Your profile CTR is {perf['ctr']} vs a {peer['avg_ctr']} peer average."
    change = perf.get("calls_change_7d")
    if change and change.startswith("-") and perf.get("calls"):
        return f"Calls are {_change(change)} this week ({perf['calls']} in the last 30 days)."
    if perf.get("views") and perf.get("calls"):
        return f"Your profile got {perf['views']} views and {perf['calls']} calls in the last 30 days."
    if not s.offers.get("active"):
        return "You have no active offer live on your profile right now."
    return f"{s.merchant.get('name', 'Your listing')} is live in {s.merchant.get('locality', 'your area')}."


def _positive_anchor(s: FactSheet) -> Optional[str]:
    perf = s.performance
    for key, label in (("calls_change_7d", "calls"), ("views_change_7d", "profile views")):
        change = perf.get(key)
        if change and change.startswith("+"):
            return f"{label.capitalize()} are {_change(change)} this week."
    if perf.get("ctr") and s.peer.get("avg_ctr") and _pct_val(perf["ctr"]) > _pct_val(s.peer["avg_ctr"]):
        return f"Your CTR is {perf['ctr']}, ahead of the {s.peer['avg_ctr']} peer average."
    return None


STOPWORDS = {"with", "free", "offer", "your", "from", "month", "first", "combo", "plan", "annual"}


def _relevant_offer(s: FactSheet, text: str) -> Optional[str]:
    """First active offer (then catalog suggestion) sharing a meaningful word with `text`."""
    words = {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in STOPWORDS}
    for title in list(s.offers.get("active") or []) + list(s.offers.get("catalog_suggestions") or []):
        title_words = {w for w in re.findall(r"[a-z]{4,}", title.lower()) if w not in STOPWORDS}
        if words & title_words:
            return title
    return None


def _lc_first(text: str) -> str:
    """Lower-case the first letter after a colon ('...: your profile got...'), but keep
    names and acronyms ('CTR', 'Dr.') as they are."""
    if len(text) > 1 and text[0].isupper() and not text[1].isupper() and text.split()[0] not in {"Dr."}:
        return text[0].lower() + text[1:]
    return text


def _singular_phrase(text: str) -> str:
    """'high risk adults' -> 'high-risk adult' (so it reads 'your high-risk adult patients')."""
    words = str(text).replace("high risk", "high-risk").replace("low risk", "low-risk").split()
    if words and len(words[-1]) > 3 and words[-1].endswith("s") and not words[-1].endswith("ss"):
        words[-1] = words[-1][:-1]
    return " ".join(words)


def _pct_val(text: str) -> float:
    try:
        return float(str(text).replace("%", "").replace("+", ""))
    except ValueError:
        return 0.0


def _source_tag(d: dict[str, Any]) -> str:
    return f"— {d['source']}" if d.get("source") else ""


def _segment_count(s: FactSheet, segment: Optional[str]) -> Optional[str]:
    """If the merchant's aggregate has a count for the digest's patient segment, return it."""
    if not segment:
        return None
    words = [w.rstrip("s") for w in re.split(r"[\s_]+", segment.lower()) if len(w) > 2]
    for key, value in s.customer_aggregate.items():
        if words and all(w in key.lower() for w in words):
            return value
    return None


# --------------------------------------------------- merchant-facing kinds
def t_research(s: FactSheet) -> Draft:
    d = s.digest_item
    if not d:
        return t_generic(s)
    size = f" ({d['trial_n']}-patient study)" if d.get("trial_n") else ""
    count = _segment_count(s, d.get("patient_segment"))
    segment = _singular_phrase(d.get("patient_segment", ""))
    relevance = (f"Relevant to your {segment} {AUDIENCE_WORD.get(s.category_slug, 'customer')}s — you have {count} on your roster."
                 if count else
                 (f"Most relevant for {d['patient_segment']}." if d.get("patient_segment") else None))
    body = _join(
        f"{s.salutation}, one item from this week's digest worth a look: {d['title']}{size}.",
        _first_sentence(d.get("summary")),
        relevance,
        _ask_yes(s, f"pull a 2-min summary and draft a {AUDIENCE_WORD.get(s.category_slug, 'customer')}-friendly WhatsApp you can share"),
        _source_tag(d),
    )
    return Draft(body, "binary_yes_no",
                 "Research digest anchored on source + numbers; ties it to the merchant's own cohort; "
                 "reciprocity (I'll summarise + draft) with a single YES.")


def t_compliance(s: FactSheet) -> Draft:
    d = s.digest_item
    t = s.trigger
    if not d:
        return t_generic(s)
    deadline = (f"{t['days_to_deadline']} days left (effective {t['deadline']})."
                if t.get("days_to_deadline") is not None and t.get("deadline") else None)
    body = _join(
        f"{s.salutation}, compliance heads-up: {d['title']}.",
        _first_sentence(d.get("summary")),
        deadline,
        _dot(f"Suggested step: {d['actionable']}") if d.get("actionable") else None,
        _ask_yes(s, "draft a 1-page checklist for your team"),
        _source_tag(d),
    )
    return Draft(body, "binary_yes_no",
                 "Regulation change with a dated deadline; loss aversion (penalty/non-compliance) "
                 "plus effort externalisation (checklist).")


def t_cde(s: FactSheet) -> Draft:
    d = s.digest_item
    if not d:
        return t_generic(s)
    t = s.trigger
    credits = t.get("credits") or d.get("credits")
    body = _join(
        f"{s.salutation}, {d['title']}" + (f" on {d['date']}." if d.get("date") else "."),
        f"{credits} CDE credits." if credits else None,
        _first_sentence(d.get("summary")),
        _dot(f"Fee: {d['actionable']}") if d.get("actionable") and "₹" in d.get("actionable", "") else None,
        _ask_yes(s, "save the details and remind you a day before"),
    )
    return Draft(body, "binary_yes_no",
                 "Continuing-education event with date and credits; low-effort commitment (reminder).")


def t_supply_alert(s: FactSheet) -> Draft:
    t, d = s.trigger, s.digest_item or {}
    batches = ", ".join(t.get("affected_batches") or [])
    who = t.get("manufacturer")
    chronic = s.customer_aggregate.get("chronic_rx_count")
    summary = d.get("summary") or ""
    sentences = re.split(r"(?<=[.!?])\s+", summary)
    if batches:
        sentences = [x for x in sentences if "batch" not in x.lower()]
    detail = sentences[0] if sentences and sentences[0] else None
    body = _join(
        f"{s.salutation}, urgent: voluntary recall on {t.get('molecule', 'a molecule you stock')}"
        + (f" batches {batches}" if batches else "") + (f" ({who})." if who else "."),
        detail,
        f"You have {chronic} chronic-Rx customers on file." if chronic else None,
        _ask_yes(s, "filter them for these batches and draft the replacement WhatsApp"),
        _source_tag(d),
    )
    return Draft(body, "binary_yes_no",
                 "Urgent recall with exact batch numbers; offers the full workflow (filter + customer note).")


def t_ipl(s: FactSheet) -> Draft:
    t, d = s.trigger, s.digest_item
    when = "tonight" if t.get("match_is_today") else f"on {t.get('match_day', 'match day')}"
    head = f"{s.salutation}, {t.get('match', 'an IPL match')}" \
           + (f" at {t['venue']}" if t.get("venue") else "") \
           + f" {when}" + (f", {t['match_time']}." if t.get("match_time") else ".")
    offer = _offer(s)
    if t.get("is_weeknight") is False and d:
        advice = _join(f"Heads-up from this season's data: {_first_sentence(d.get('summary'))}",
                       f"So skip a dine-in match promo today and push your {offer} for delivery instead."
                       if offer else "So focus on delivery today rather than a dine-in promo.")
        action = "draft a delivery banner and an Insta story"
    else:
        advice = _first_sentence(d.get("summary")) if d else None
        sugg = next((x for x in s.offers.get("catalog_suggestions", []) if "match" in x.lower()), None)
        advice = _join(advice, f"A {sugg} fits tonight." if sugg else None)
        action = "set up the match-night post"
    body = _join(head, advice, _ask_yes(s, action))
    return Draft(body, "binary_yes_no",
                 "Match-day trigger with a data-backed call on whether a promo is worth it; "
                 "reuses the merchant's existing offer.")


def t_perf_dip(s: FactSheet) -> Draft:
    t, perf = s.trigger, s.performance
    if t.get("change") and t.get("metric"):
        head = f"{s.salutation}, your {t['metric']} dropped {t['change'].lstrip('+-')} over the last {t.get('window', '7 days')}" \
               + (f" (usual level: {t['baseline_value']})." if t.get("baseline_value") else ".")
    else:
        head = f"{s.salutation}, quick flag on {s.merchant.get('name', 'your listing')}: {_lc_first(_anchor(s))}"
    context = None
    if perf.get("ctr") and s.peer.get("avg_ctr") and _pct_val(perf["ctr"]) < _pct_val(s.peer["avg_ctr"]):
        context = f"Your CTR is {perf['ctr']} against a {s.peer['avg_ctr']} peer average."
    offer, sugg = _offer(s), _suggestion(s)
    if offer:
        action = f"refresh your Google posts around your {offer}"
    elif sugg:
        action = f"put up a {sugg} offer to lift calls this week"
    else:
        action = "refresh your Google posts this week"
    duplicate = context and perf.get("ctr") and perf["ctr"] in head
    body = _join(head, None if duplicate else context, _ask_yes(s, action))
    return Draft(body, "binary_yes_no",
                 "Performance dip stated with the exact change; loss aversion + a concrete fix.")


def t_perf_spike(s: FactSheet) -> Draft:
    t = s.trigger
    if t.get("change") and t.get("metric"):
        head = f"{s.salutation}, good news: {t['metric']} are {_change(t['change'])} over the last {t.get('window', '7 days')}" \
               + (f" (vs {t['baseline_value']} usual)." if t.get("baseline_value") else ".")
    else:
        positive = _positive_anchor(s)
        if not positive:
            return t_generic(s)
        head = f"{s.salutation}, good momentum at {s.merchant.get('name', 'your place')}: {_lc_first(positive)}"
    driver = f"Likely driver: {t['likely_driver']}." if t.get("likely_driver") else None
    action = ("line up one more post like it for this week" if driver
              else "put up a fresh Google post this week to keep the momentum going")
    body = _join(head, driver, _ask_yes(s, action))
    return Draft(body, "binary_yes_no", "Positive spike with driver; momentum lever, low-effort next step.")


def t_seasonal_dip(s: FactSheet) -> Draft:
    t, d = s.trigger, s.digest_item
    head = f"{s.salutation}, your {t.get('metric', 'numbers')} are {_change(t.get('change')) or 'down'} this week" \
           + (" — this is the expected seasonal lull, not something you did." if t.get("expected_seasonal") else ".")
    members = s.customer_aggregate.get("total_active_members")
    focus = f"Best use of this window: keep your {members} active members engaged." if members else None
    body = _join(head, _dot(_first_sentence(d.get("actionable"))) if d and d.get("actionable") else None,
                 focus, _ask_yes(s, "draft a simple attendance challenge for your members"))
    return Draft(body, "binary_yes_no",
                 "Pre-empts anxiety about an expected dip; reframes to retention with the member count.")


def t_renewal(s: FactSheet) -> Draft:
    t, perf = s.trigger, s.performance
    days = t.get("days_remaining", s.merchant.get("subscription_days_remaining"))
    plan = t.get("plan", s.merchant.get("plan", "magicpin"))
    stats = (f"Last 30 days: {perf['views']} profile views and {perf['calls']} calls."
             if perf.get("views") and perf.get("calls") else None)
    if isinstance(days, int) and days > RENEWAL_WINDOW_DAYS:
        # The data contradicts the trigger: don't push a renewal that isn't due.
        head = f"{s.salutation}, quick account check-in: your {plan} plan runs for another {days} days, so nothing to renew yet."
        body = _join(head, stats, _ask_yes(s, "suggest the one change that would help most this week"))
        return Draft(body, "binary_yes_no",
                     "Renewal trigger, but the plan has plenty of time left, so this is a value check-in, not a renewal push.")
    head = f"{s.salutation}, your {plan} plan renews in {days if days is not None else 'a few'} days" \
           + (f" ({t['renewal_amount']})." if t.get("renewal_amount") else ".")
    body = _join(head, stats, _ask_yes(s, "share the renewal details so nothing pauses"))
    return Draft(body, "binary_yes_no", "Renewal deadline with the value delivered; loss aversion.")


def t_winback(s: FactSheet) -> Draft:
    t = s.trigger
    head = f"{s.salutation}, it's been {t.get('days_since_expiry', 'a while')} days since your plan lapsed."
    impact = _join(
        f"Since then {t['customers_lapsed_since_expiry']} customers have gone quiet" if t.get("customers_lapsed_since_expiry") else None,
        f"and calls are {_change(t['performance_change_since_expiry'])}." if t.get("performance_change_since_expiry") else None,
    )
    if impact and not impact.endswith("."):
        impact += "."
    body = _join(head, impact, _ask_yes(s, "restart your profile upkeep this week"))
    return Draft(body, "binary_yes_no", "Win-back with the measurable cost of lapsing; single YES.")


def t_milestone(s: FactSheet) -> Draft:
    t = s.trigger
    metric = t.get("metric", "reviews").replace("review count", "reviews")
    if not t.get("milestone"):
        return t_generic(s)
    if t.get("remaining_to_milestone"):
        head = f"{s.salutation}, you're at {t.get('current_value')} {metric} — just {t['remaining_to_milestone']} away from {t['milestone']}."
    else:
        head = f"{s.salutation}, you've crossed {t.get('milestone', 'a milestone')} {metric}!"
    body = _join(head, _ask_yes(s, "send a review request to your recent regulars to cross it this week"))
    return Draft(body, "binary_yes_no", "Milestone proximity (goal-gradient) with an effortless next step.")


def t_review_theme(s: FactSheet) -> Draft:
    t = s.trigger
    theme = t.get("review_theme")
    if not theme:
        neg = next((r for r in s.review_themes if r.get("sentiment") == "neg"), None)
        theme = neg.get("theme") if neg else None
        count = neg.get("mentions_30d") if neg else None
        quote = neg.get("quote") if neg else None
    else:
        count, quote = t.get("mentions_30d"), t.get("quote")
    if not theme:
        return t_generic(s)
    body = _join(
        f"{s.salutation}, {count + ' reviews' if count else 'several reviews'} in the last 30 days mention {theme}"
        + (f", e.g. \"{quote}\"." if quote else "."),
        "It's trending up." if t.get("trend") == "rising" else None,
        _ask_yes(s, "draft a polite public reply you can reuse"),
    )
    return Draft(body, "binary_yes_no", "Review pattern with count + real quote; reputation loss aversion.")


def t_dormant(s: FactSheet) -> Draft:
    t = s.trigger
    days = t.get("days_since_merchant_last_replied")
    head = f"{s.salutation}, it's been {days} days since we last spoke." if days else f"{s.salutation}, quick one."
    body = _join(head, _anchor(s), _ask_yes(s, "send you a short snapshot of what to fix first"))
    return Draft(body, "binary_yes_no", "Re-engagement with one verifiable fact (curiosity) and a tiny ask.")


def t_curious_ask(s: FactSheet) -> Draft:
    offer = _offer(s)
    guess = f" Is it still the {offer}?" if offer else ""
    body = _join(
        f"Hi {s.salutation}! Quick question — what's been most asked-for at {s.merchant.get('name', 'your place')} this week?{guess}",
        "I'll turn your answer into a Google post and a short WhatsApp reply for pricing questions.",
    )
    body = _ask_open(s, body)
    return Draft(body, "open_ended",
                 "Asking-the-merchant lever: zero-commitment question, reciprocity offered up front.")


def t_planning(s: FactSheet) -> Draft:
    t = s.trigger
    topic = t.get("intent_topic", "plan")
    offer = _offer(s)
    body = _join(
        f"{s.salutation}, picking up on the {topic} — here's how I'll set it up:",
        f"a one-page outline built around your {offer}," if offer else "a one-page outline,",
        "a Google post announcing it, and a WhatsApp message for your regulars.",
        "Reply CONFIRM and I'll send all three drafts for your edits.",
    )
    return Draft(body, "binary_confirm_cancel",
                 "Merchant already said yes: switches straight to action with a concrete artefact list, no re-qualifying.")


def t_gbp_unverified(s: FactSheet) -> Draft:
    t = s.trigger
    uplift = f" Verified profiles typically see about {t['estimated_uplift']} more visibility." if t.get("estimated_uplift") else ""
    path = f" Verification is by {t['verification_path']}." if t.get("verification_path") else ""
    body = _join(f"{s.salutation}, your Google profile is still unverified.{uplift}{path}",
                 _ask_yes(s, "start the verification for you now"))
    return Draft(body, "binary_yes_no", "Unverified profile with estimated uplift; effort externalisation.")


def t_competitor(s: FactSheet) -> Draft:
    t = s.trigger
    if not t.get("competitor_name"):
        return t_generic(s)
    where = f" {t['distance_km']} km away" if t.get("distance_km") else " nearby"
    their = f", advertising {t['competitor_offer']}" if t.get("competitor_offer") else ""
    offer = _offer(s)
    ours = f" Your {offer} is live — worth making it more visible this week." if offer else None
    body = _join(f"{s.salutation}, heads-up: {t['competitor_name']} opened{where}"
                 + (f" on {t['competitor_opened']}" if t.get("competitor_opened") else "") + f"{their}.",
                 ours, _ask_yes(s, "refresh your listing and post to hold your ground"))
    return Draft(body, "binary_yes_no", "Competitor event with distance and their offer; loss aversion.")


def t_festival(s: FactSheet) -> Draft:
    t = s.trigger
    if not t.get("festival"):
        beat = s.trigger.get("upcoming_season")
        if not beat:
            return t_generic(s)
        if "retention" in beat.lower():
            idea, action = None, f"draft a post for your regular {AUDIENCE_WORD.get(s.category_slug, 'customer')}s for this window"
        else:
            match = _relevant_offer(s, beat)
            idea = (f"Your {match} fits this well." if match in (s.offers.get("active") or [])
                    else f"A {match} offer would fit it." if match else None)
            action = f"draft a post around {'it' if match else 'this season'}"
        body = _join(f"{s.salutation}, this time of year: {beat}.", idea, _ask_yes(s, action))
        return Draft(body, "binary_yes_no",
                     "Festival trigger without details: anchored on the category's seasonal calendar; "
                     "an offer is only attached when it matches the season.")
    fest = t.get("festival", "The festival")
    when = f" is {t['days_until_festival']} days away ({t['festival_date']})." if t.get("days_until_festival") is not None \
        else (f" is on {t['festival_date']}." if t.get("festival_date") else " is coming up.")
    offer, sugg = _offer(s), _suggestion(s)
    idea = f"Your {offer} could anchor a {fest} campaign." if offer else (f"A {sugg} offer could anchor it." if sugg else None)
    body = _join(f"{s.salutation}, {fest}{when}", idea, _ask_yes(s, f"draft the {fest} post and offer early"))
    return Draft(body, "binary_yes_no", "Festival date with countdown; plan-ahead framing using a real offer.")


def t_category_seasonal(s: FactSheet) -> Draft:
    t, d = s.trigger, s.digest_item
    shifts = t.get("demand_shifts")
    head = f"{s.salutation}, seasonal shift this month: {', '.join(shifts)}." if shifts else f"{s.salutation}, the season is turning."
    tip = _dot(d.get("actionable")) if d else None
    body = _join(head, tip, _ask_yes(s, "draft a shelf-and-post plan for it"))
    return Draft(body, "binary_yes_no", "Category demand shift with numbers; practical action.")


def _consent_gap_reason(reason: Optional[str]) -> str:
    """Say WHY we held back, precisely (opted out vs. consent scope vs. churned)."""
    reason = reason or ""
    if reason == "customer_opted_out":
        return "they've opted out of WhatsApp reminders"
    if reason == "no_recorded_consent":
        return "there's no WhatsApp consent on record for them"
    if reason == "customer_churned":
        return "they're marked as churned and haven't agreed to win-back messages"
    return "their WhatsApp consent doesn't cover this kind of message"


def t_consent_gap(s: FactSheet) -> Draft:
    kind = s.kind.replace("_", " ")
    c = s.customer or {}
    who = c.get("about") or c.get("address_as")
    if who:
        body = _join(
            f"{s.salutation}, {who} is due {CONSENT_PHRASES.get(s.kind, 'a follow-up message')}, but "
            f"{_consent_gap_reason(s.consent_reason)}, so I haven't contacted them.",
            _ask_yes(s, "draft a short note you can send them yourself"),
        )
        why = "Customer event, but consent does not cover this outreach: informs the merchant instead of messaging the customer."
    else:
        body = _join(
            f"{s.salutation}, a {kind} event came up for one of your customers, but I don't have their profile yet, "
            "so I haven't messaged anyone.",
            _ask_yes(s, "prepare the message once their details are in"),
        )
        why = "Customer event without a customer profile: no customer details invented, merchant kept in the loop."
    return Draft(body, "binary_yes_no", why)


def t_event(s: FactSheet) -> Optional[Draft]:
    """A trigger kind we have no template for, but whose payload has real details:
    lead with the event itself (the why-now), then one relevant offer if any."""
    details = s.trigger.get("event_details") or {}
    if not details:
        return None
    kind = s.kind.replace("_", " ")
    facts = "; ".join(f"{k} {v}" for k, v in list(details.items())[:4])
    match = _relevant_offer(s, f"{kind} {facts}")
    idea = (f"Your {match} fits this well." if match in (s.offers.get("active") or [])
            else f"A {match} offer would fit it." if match else None)
    audience = AUDIENCE_WORD.get(s.category_slug, "customer")
    body = _join(f"{s.salutation}, heads-up on a {kind}: {facts}.", idea,
                 _ask_yes(s, f"draft a short post and {audience} note about it for today"))
    return Draft(body, "binary_yes_no",
                 f"New trigger kind '{kind}': leads with the event's own details from the payload, "
                 "then a timely, low-effort action.")


def t_generic(s: FactSheet) -> Draft:
    event = t_event(s)
    if event is not None:
        return event
    kind = s.kind.replace("_", " ")
    label = KIND_LABELS.get(s.kind, "update")
    body = _join(f"{s.salutation}, a quick {label} for {s.merchant.get('name', 'you')}.", _anchor(s),
                 _ask_yes(s, "suggest the one change that would help most this week"))
    return Draft(body, "binary_yes_no", f"No kind-specific data for '{kind}', so anchored on one verifiable merchant fact.")


# --------------------------------------------------- customer-facing kinds
def _cust_open(s: FactSheet, emoji: str = "") -> str:
    c = s.customer or {}
    name = s.merchant.get("name", "us")
    tail = f" {emoji}" if emoji else "."
    if s.salutation == "Namaste":
        return f"Namaste — {name} yahan{tail}" if s.language == "hi-en" else f"Namaste — {name} here{tail}"
    who = c.get("address_as")
    return f"Hi {who}, {name} here{tail}" if who else f"Hi, {name} here{tail}"


def _hold(s: FactSheet) -> str:
    return HOLD_PHRASES.get(s.category_slug, "hold a slot for you")


def _slot_ask(s: FactSheet, slots: list[str]) -> tuple[str, str]:
    if len(slots) >= 2:
        a, b = slots[0], slots[1]
        lead = f"Aapke liye 2 slots ready hain: {a} ya {b}." if s.language == "hi-en" else f"Two slots are open: {a} or {b}."
        return _join(lead, f"Reply 1 for {a.split(',')[0]}, 2 for {b.split(',')[0]}, or tell us a time that works."), "multi_choice_slot"
    if len(slots) == 1:
        return f"Next open slot: {slots[0]}. Reply YES to book it, or tell us a time that works.", "binary_yes_no"
    return "Reply YES and we'll share a couple of slots that suit you.", "binary_yes_no"


def c_recall(s: FactSheet) -> Draft:
    t, c = s.trigger, s.customer or {}
    months = t.get("months_since_last_service") or c.get("months_since_last_visit")
    since = f"It's been about {months} months since your last visit — " if months else ""
    due = t.get("service_due", "check-up")
    due_line = f"{since}your {due} is due" + (f" (by {t['due_date']})." if t.get("due_date") else ".")
    offer = _offer(s)
    ask, cta = _slot_ask(s, t.get("slots") or [])
    body = _join(_cust_open(s, "🦷" if s.category_slug == "dentists" else ""),
                 due_line[0].upper() + due_line[1:], f"Current offer: {offer}." if offer else None, ask)
    return Draft(body, cta, "Customer recall with real due date and open slots; honours language and slot preference.")


def c_appointment(s: FactSheet) -> Draft:
    t = s.trigger
    slot = (t.get("slots") or [None])[0]
    service = (t.get("event_details") or {}).get("service") or t.get("service_due")
    what = f"{service} appointment" if service else "appointment"
    if slot:
        line = (f"Aapka {what} {slot} ko hai." if s.language == "hi-en"
                else f"Just a reminder: your {what} is on {slot}.")
        why = "Appointment reminder with the real slot from the trigger; easy confirm or reschedule."
    else:
        line = f"A quick reminder about your {what} with us tomorrow."
        why = "Appointment reminder; no invented time since none was provided."
    body = _join(_cust_open(s), line, "Reply YES to confirm, or tell us if you need to reschedule.")
    return Draft(body, "binary_yes_no", why)


def c_refill(s: FactSheet) -> Draft:
    t, c = s.trigger, s.customer or {}
    meds = t.get("medicines")
    if not meds:
        return c_generic(s)
    who = c.get("address_as") or ""
    m = re.match(r"^(mr|mrs|ms)\.?\s+(.+)$", who, flags=re.I)
    ji = f"{m.group(2)} ji" if m else (who or "Aap")
    delivery = next((o for o in s.offers.get("active", []) if "delivery" in o.lower()), None)
    senior = next((o for o in s.offers.get("active", []) if "senior" in o.lower()), None) if c.get("senior_citizen") else None
    if s.language == "hi-en":
        line = f"{ji} ki medicines ({', '.join(meds)})" + (f" {t['runs_out_on']} ko khatam hongi." if t.get("runs_out_on") else " refill ke liye due hain.")
        extra = _join(f"{senior} applicable." if senior else None, f"{delivery}." if delivery else None)
        ask = "Same order dispatch kar dein? Reply CONFIRM, ya dosage change ho to bata dijiye."
    else:
        line = f"{ji}'s medicines ({', '.join(meds)})" + (f" run out on {t['runs_out_on']}." if t.get("runs_out_on") else " are due for refill.")
        extra = _join(f"{senior} applies." if senior else None, f"{delivery}." if delivery else None)
        ask = "Reply CONFIRM to dispatch the same order, or tell us if the dosage changed."
    body = _join(_cust_open(s), line, extra, ask)
    return Draft(body, "binary_confirm_cancel", "Chronic refill with exact molecules and run-out date; respectful senior framing.")


def c_trial(s: FactSheet) -> Draft:
    t, c = s.trigger, s.customer or {}
    if s.category_slug not in {"gyms", "salons"} and not t.get("slots"):
        return c_generic(s)
    kid = c.get("about")
    when = f" on {t['trial_date']}" if t.get("trial_date") else ""
    thanks = f"Thanks for bringing {kid} for the trial{when}!" if kid else f"Thanks for trying a session with us{when}!"
    ask, cta = _slot_ask(s, t.get("slots") or [])
    return Draft(_join(_cust_open(s), thanks, ask), cta, "Trial follow-up with the real next session; one easy yes.")


def c_wedding(s: FactSheet) -> Draft:
    t = s.trigger
    days = t.get("days_to_wedding")
    head = f"{days} days to your wedding" if days is not None else "Your wedding is coming up"
    step = t.get("next_step", "skin-prep program")
    pref = (s.customer or {}).get("preferred_slots")
    pref = pref.title() if pref else pref
    body = _join(_cust_open(s, "💍"), f"{head} — a good time to start the {step}.",
                 f"Want us to hold a {pref} slot for your first session? Reply YES." if pref
                 else "Want us to hold a slot for your first session? Reply YES.")
    return Draft(body, "binary_yes_no", "Bridal follow-up with countdown from the real wedding date; preference honoured.")


def c_lapsed(s: FactSheet) -> Draft:
    t, c = s.trigger, s.customer or {}
    weeks = t.get("weeks_since_last_visit")
    if not weeks and c.get("days_since_last_visit"):
        weeks = round(c["days_since_last_visit"] / 7) or None
        if weeks:
            s.trigger["weeks_since_last_visit"] = weeks   # keep the sheet in sync for grounding
    gap = f"It's been about {weeks} weeks — happens to everyone, no pressure." if weeks else "It's been a while — no pressure at all."
    focus = t.get("previous_focus") or c.get("training_focus")
    offer = _offer(s)
    body = _join(_cust_open(s), gap,
                 f"Happy to help you pick up your {focus} goals again." if focus else None,
                 f"Our {offer} offer is on right now." if offer else None,
                 f"Want us to {_hold(s)} this week? Reply YES.")
    return Draft(body, "binary_yes_no", "No-shame win-back referencing their past goal and a real offer.")


def c_generic(s: FactSheet) -> Draft:
    offer = _offer(s)
    body = _join(_cust_open(s), f"Our {offer} offer is on right now." if offer else "We'd love to see you again soon.",
                 f"Want us to {_hold(s)} this week? Reply YES.")
    return Draft(body, "binary_yes_no", "Customer message with only the merchant's real offer; no invented specifics.")


# ---------------------------------------------------------------- dispatch
MERCHANT_TEMPLATES: dict[str, Callable[[FactSheet], Draft]] = {
    "research_digest": t_research,
    "research_digest_release": t_research,
    "category_research_digest_release": t_research,
    "category_trend_movement": t_research,
    "regulation_change": t_compliance,
    "cde_opportunity": t_cde,
    "supply_alert": t_supply_alert,
    "ipl_match_today": t_ipl,
    "perf_dip": t_perf_dip,
    "perf_spike": t_perf_spike,
    "seasonal_perf_dip": t_seasonal_dip,
    "renewal_due": t_renewal,
    "winback_eligible": t_winback,
    "milestone_reached": t_milestone,
    "review_theme_emerged": t_review_theme,
    "dormant_with_vera": t_dormant,
    "curious_ask_due": t_curious_ask,
    "scheduled_recurring": t_curious_ask,
    "active_planning_intent": t_planning,
    "gbp_unverified": t_gbp_unverified,
    "competitor_opened": t_competitor,
    "festival_upcoming": t_festival,
    "category_seasonal": t_category_seasonal,
}

CUSTOMER_TEMPLATES: dict[str, Callable[[FactSheet], Draft]] = {
    "recall_due": c_recall,
    "appointment_tomorrow": c_appointment,
    "chronic_refill_due": c_refill,
    "trial_followup": c_trial,
    "wedding_package_followup": c_wedding,
    "bridal_followup": c_wedding,
    "customer_lapsed_soft": c_lapsed,
    "customer_lapsed_hard": c_lapsed,
}


def render(sheet: FactSheet) -> Draft:
    if sheet.audience == "customer":
        return CUSTOMER_TEMPLATES.get(sheet.kind, c_generic)(sheet)
    if sheet.consent_note:
        return t_consent_gap(sheet)
    return MERCHANT_TEMPLATES.get(sheet.kind, t_generic)(sheet)
