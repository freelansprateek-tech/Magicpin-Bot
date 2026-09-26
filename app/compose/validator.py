"""Grounding + format validator. Runs on EVERY message (LLM or template).

Checks, in order:
  1. non-empty and not absurdly long
  2. no URLs (api-call-examples F.4: hard fail, -3)
  3. no category taboo words ("guaranteed", "miracle", ...)
  4. no preamble ("I hope you're doing well...")
  5. every number in the body appears in the fact sheet  <- grounding
     (except small "N min" effort promises, e.g. "ready in 10 min")
  6. the recipient is addressed by name (when we know it)
  7. CTA shape matches the declared cta, and there is only one "Reply ..." ask
  8. not a repeat of something already sent

Number normalisation makes "2,100" == "2100", "₹1,499" == "1499",
"38%" == "38", "2.10" == "2.1", so formatting differences never cause a
false rejection.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Iterable

from app.compose.facts import FactSheet

NUMBER_RE = re.compile(r"(?<![A-Za-z])\d[\d,]*(?:\.\d+)?")
URL_RE = re.compile(r"https?://|www\.|\b[a-z0-9-]+\.(?:com|in|org|net|io|co|ly|app)\b", re.I)
PREAMBLE_RE = re.compile(r"\b(i hope (you|this)|hope you('| a)re (doing )?well|i('| a)m reaching out|"
                         r"i am writing to)\b", re.I)
ALWAYS_ALLOWED = {"1", "2"}          # "Reply 1 for Wed, 2 for Thu"
# "ready in 10 min" / "a 2-min read" is a promise about Vera's own effort, not a data
# claim, so small minute counts are exempt from grounding (bigger ones are not).
EFFORT_RE = re.compile(r"\b(\d{1,2})\s?-?\s?(?:min|mins|minute|minutes)\b", re.I)
MAX_EFFORT_MINUTES = 15
MAX_BODY_CHARS = 700


def normalize_number(token: str) -> str:
    token = token.replace(",", "").strip().rstrip(".")
    if "." in token:
        token = token.rstrip("0").rstrip(".")
    if token.startswith("0.") :
        return token
    return token.lstrip("0") or "0"


def numbers_in(text: str) -> set[str]:
    return {normalize_number(m.group()) for m in NUMBER_RE.finditer(text)}


def allowed_numbers(sheet: FactSheet) -> set[str]:
    blob = json.dumps(sheet.to_prompt_dict(), ensure_ascii=False)
    allowed = numbers_in(blob) | ALWAYS_ALLOWED
    # Signed percentages appear as "+18%" / "-5%" in the sheet; the body may drop the sign.
    return allowed


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)


def validate(body: str, cta: str, sheet: FactSheet, already_sent: Iterable[str] = ()) -> ValidationResult:
    errors: list[str] = []
    text = (body or "").strip()

    if not text:
        return ValidationResult(False, ["empty body"])
    if len(text) > MAX_BODY_CHARS:
        errors.append(f"body too long ({len(text)} chars > {MAX_BODY_CHARS})")
    if URL_RE.search(text):
        errors.append("contains a URL")

    lowered = text.lower()
    for taboo in sheet.taboos:
        if taboo and re.search(rf"(?<!\w){re.escape(taboo)}(?!\w)", lowered):
            errors.append(f"uses taboo phrase '{taboo}'")

    if PREAMBLE_RE.search(text):
        errors.append("has a generic preamble")

    effort_free = EFFORT_RE.sub(lambda m: "" if int(m.group(1)) <= MAX_EFFORT_MINUTES else m.group(0), text)
    ungrounded = sorted(numbers_in(effort_free) - allowed_numbers(sheet), key=lambda s: (len(s), s))
    if ungrounded:
        errors.append(f"numbers not in fact sheet: {', '.join(ungrounded)}")

    name = sheet.salutation
    if name and name not in {"there", "Doctor"} and name.lower() not in lowered:
        errors.append(f"does not address the recipient as '{name}'")

    # Count "Reply YES / Reply 1 / YES reply kar..." asks, not the word "reply" in general
    reply_asks = len(re.findall(r"\breply\s+(?:with\s+)?(?:yes|no|confirm|stop|change|\d)\b|\byes reply\b", lowered))
    if reply_asks > 1:
        errors.append("more than one 'Reply ...' ask (multiple CTAs)")
    if cta in {"binary_yes_no", "binary_confirm_cancel"} and not re.search(r"\b(YES|CONFIRM)\b", text):
        errors.append("binary CTA declared but no YES/CONFIRM ask in body")
    if cta == "open_ended" and "?" not in text:
        errors.append("open-ended CTA declared but body asks no question")
    if cta == "multi_choice_slot" and not re.search(r"\b1\b", text):
        errors.append("multi-choice CTA declared but no numbered options")

    norm = " ".join(lowered.split())
    for previous in already_sent:
        if previous and " ".join(previous.lower().split()) == norm:
            errors.append("repeats a message already sent")
            break

    return ValidationResult(not errors, errors)
