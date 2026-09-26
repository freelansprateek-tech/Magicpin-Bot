"""Prompt construction. Versioned: bump PROMPT_VERSION whenever wording changes
(it is part of the cache key and is logged with every message)."""

from __future__ import annotations

import json

from app.compose.facts import FactSheet
from app.compose.templates import Draft

PROMPT_VERSION = "composer_v2"

SYSTEM_PROMPT = """You write ONE WhatsApp message for Vera, magicpin's merchant assistant.

HARD RULES (a validator rejects violations):
1. Use ONLY facts in FACT_SHEET. Every number, price, date, name, source must appear there verbatim.
   Never invent offers, prices, slots, counts, competitors, studies or outcomes. Catalog suggestions may be PROPOSED, never described as already running.
2. Exactly one call-to-action, in the final sentence. No URLs. No "I hope you're doing well"-style preamble.
3. Start by addressing the recipient exactly as FACT_SHEET.salutation.
4. Never use these taboo phrases: {taboos}.
5. Do not repeat any message in recent_messages_do_not_repeat.
6. If consent_note is present, address the merchant only; do not message or promise to message the customer.

STYLE:
- Tone: {tone} — {tone_guide}
- Language: {language_rule}
- Be specific: lead with the concrete "why now" (the trigger fact), add at most 2-3 supporting numbers.
- Use at least one lever: loss aversion, curiosity, social proof from the sheet, effort externalisation ("I'll draft it"), or a single binary YES.
- Merchant fit: use THIS merchant's own numbers/offers/history from the sheet; never a sentence that could be sent to any merchant.
- Why-now first: the first sentence names the trigger event (the date, number or headline that makes today the day).
- Social proof ONLY from peer_stats / digest in the sheet (e.g. "peer average CTR is 3%"); never invent other merchants.
- An effort promise about Vera's own work ("ready in 10 min") is fine; any other number must come from the sheet.
- Peer tone, not promotional. Short: 2-4 sentences, under 450 characters.
- Category vocabulary you may use: {vocab}

Return ONLY JSON: {{"body": "...", "cta": "binary_yes_no|binary_confirm_cancel|open_ended|multi_choice_slot|none", "rationale": "one sentence: why this message, which facts, which lever"}}"""

LANGUAGE_RULES = {
    "hi-en": "natural Hindi-English code-mix (Roman script), mostly English with Hindi connectors; keep numbers and names exact.",
    "en": "clear, simple English.",
}

KIND_GUIDANCE = {
    "research_digest": "Cite the source; connect the finding to this merchant's own patients/cohort if the sheet has a count.",
    "regulation_change": "State the rule change, the deadline and days left; offer to do the prep work.",
    "supply_alert": "Urgent but calm; exact batch numbers and manufacturer; offer the full workflow.",
    "ipl_match_today": "Use the digest data to judge whether a promo makes sense today; a contrarian call is fine if the data supports it.",
    "seasonal_perf_dip": "Reassure: the dip is expected; redirect to retention.",
    "curious_ask_due": "Ask the merchant a low-effort question; offer something back for answering. open_ended CTA.",
    "active_planning_intent": "The merchant ALREADY said yes. Do not ask qualifying questions; deliver a concrete draft/next step.",
    "competitor_opened": "Factual, not alarmist; only the competitor facts in the sheet.",
    "recall_due": "Customer-facing, sent from the merchant's number; offer the listed slots; no medical claims.",
    "chronic_refill_due": "Customer-facing, respectful; exact medicine names and run-out date; CONFIRM to dispatch.",
    "customer_lapsed_hard": "Customer-facing, warm, zero guilt or shame.",
    "customer_lapsed_soft": "Customer-facing, warm, zero guilt or shame.",
    "perf_dip": "Lead with the exact drop and window; compare to peer average if in the sheet; one concrete fix.",
    "perf_spike": "Celebrate briefly with the exact rise; name the likely driver only if the sheet has it; offer to repeat it.",
    "renewal_due": "If days_remaining is large, do NOT push renewal — make it a value check-in. Otherwise: days left + value delivered.",
    "winback_eligible": "Show the measurable cost of lapsing (from the sheet); no guilt; one YES to restart.",
    "milestone_reached": "Goal-gradient: how close they are; offer the effortless step to cross it.",
    "review_theme_emerged": "Quote the review theme/quote from the sheet; offer a reusable reply or fix; no defensiveness.",
    "dormant_with_vera": "Re-open with ONE verifiable fact about their account (curiosity), tiny ask; no guilt about silence.",
    "festival_upcoming": "Festival + date/countdown from the sheet; tie to a real or catalog offer only if it fits.",
    "category_seasonal": "State the demand shifts with their numbers; one practical shelf/post action.",
    "cde_opportunity": "Event title, date/time, credits and fee exactly as in the sheet; offer to save/remind.",
    "gbp_unverified": "Unverified profile + estimated uplift from the sheet; offer to start verification.",
    "wedding_package_followup": "Customer-facing, warm; days to wedding from the sheet; propose the next step; preferred slot if known.",
    "trial_followup": "Customer-facing; thank for the trial; offer the listed next session only.",
    "appointment_tomorrow": "Customer-facing reminder; only times present in the sheet; easy reschedule option.",
}


DEFAULT_GUIDANCE = ("New or unusual trigger kind: make the why-now explicit using trigger.event_details "
                    "(the event's own facts), connect it to this merchant's category, one low-effort action.")


def build_prompts(sheet: FactSheet, baseline: Draft, feedback: str | None = None) -> tuple[str, str]:
    system = SYSTEM_PROMPT.format(
        taboos=", ".join(f'"{t}"' for t in sheet.taboos) or "none",
        tone=sheet.tone,
        tone_guide=sheet.tone_guide,
        language_rule=LANGUAGE_RULES.get(sheet.language, LANGUAGE_RULES["en"]),
        vocab=", ".join(sheet.vocab[:12]) or "general business terms",
    )
    parts = [
        f"TRIGGER KIND: {sheet.kind}",
        f"GUIDANCE: {KIND_GUIDANCE.get(sheet.kind, DEFAULT_GUIDANCE)}",
        "FACT_SHEET:",
        json.dumps(sheet.to_prompt_dict(), ensure_ascii=False, indent=1),
        "BASELINE (grounded, deterministic). Keep its facts; make it sharper and more compelling:",
        json.dumps({"body": baseline.body, "cta": baseline.cta}, ensure_ascii=False),
    ]
    if feedback:
        parts.append(f"YOUR PREVIOUS DRAFT WAS REJECTED: {feedback}. Fix exactly these problems.")
    return system, "\n".join(parts)
