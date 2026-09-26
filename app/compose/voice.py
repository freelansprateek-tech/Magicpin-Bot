"""Category voice rules, salutations and language choice.

Everything is read from the CategoryContext when present; the small defaults
below only cover fields the judge might omit. They are style rules, not facts,
so they cannot introduce a hallucinated number or name.
"""

from __future__ import annotations

import re
from typing import Any, Optional

DEFAULT_TONES = {
    "dentists": "peer_clinical",
    "salons": "warm_practical",
    "restaurants": "warm_busy_practical",
    "gyms": "energetic_disciplined",
    "pharmacies": "trustworthy_precise",
}

TONE_GUIDE = {
    "peer_clinical": "Clinical peer-to-peer. Technical terms welcome, cite sources, no hype, no overclaims.",
    "warm_practical": "Warm, friendly, practical fellow professional. Light emoji OK.",
    "warm_busy_practical": "Operator-to-operator: short, practical, talk covers/footfall/AOV.",
    "energetic_disciplined": "Coach-like and motivating but grounded in numbers. No shame, no guilt.",
    "trustworthy_precise": "Trustworthy and precise neighbourhood pharmacist. Exact molecule names, calm tone.",
}

# Always-banned phrases on top of the category taboos (generic hype / legal risk).
GLOBAL_TABOOS = ["guaranteed", "100% safe", "miracle", "best in city", "amazing deal"]


def tone_of(category: dict[str, Any]) -> str:
    voice = category.get("voice") or {}
    return voice.get("tone") or DEFAULT_TONES.get(category.get("slug", ""), "warm_practical")


def taboos_of(category: dict[str, Any]) -> list[str]:
    """Taboo phrases, reading BOTH key names (schema says `taboos`, dataset uses `vocab_taboo`)."""
    voice = category.get("voice") or {}
    raw = list(voice.get("vocab_taboo") or []) + list(voice.get("taboos") or [])
    cleaned = []
    for phrase in raw + GLOBAL_TABOOS:
        # "FDA-approved (use only when actually applicable)" -> "FDA-approved"
        text = re.sub(r"\s*\(.*?\)\s*", " ", str(phrase)).strip().lower()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def vocab_of(category: dict[str, Any]) -> list[str]:
    return list((category.get("voice") or {}).get("vocab_allowed") or [])


def strip_title(first_name: str) -> str:
    """'Dr. Sameer' -> 'Sameer' (generated dentists already carry the title)."""
    return re.sub(r"^(dr\.?|doctor)\s+", "", first_name.strip(), flags=re.I)


def merchant_salutation(category: dict[str, Any], merchant: dict[str, Any]) -> str:
    identity = merchant.get("identity") or {}
    first = strip_title(str(identity.get("owner_first_name") or ""))
    if category.get("slug") == "dentists":
        return f"Dr. {first}" if first else "Doctor"
    return first or str(identity.get("name") or "there")


def merchant_language(merchant: dict[str, Any]) -> str:
    """'hi-en' when the merchant lists Hindi, else 'en'."""
    langs = [str(l).lower() for l in (merchant.get("identity") or {}).get("languages") or []]
    return "hi-en" if "hi" in langs else "en"


def customer_language(customer: dict[str, Any]) -> str:
    """Hindi or Hindi-English mix -> 'hi-en'. Other regional mixes -> 'en' (decision:
    we do not generate Telugu/Tamil/Kannada text we cannot verify)."""
    pref = str((customer.get("identity") or {}).get("language_pref") or "").lower()
    if pref.startswith("hi") or pref in {"hindi"}:
        return "hi-en"
    return "en"


def customer_names(customer: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """Return (addressee, subject).
    'Aanya (parent: Sneha)' -> ('Sneha', 'Aanya'); 'Priya' -> ('Priya', None);
    '(walk-in, no profile)' -> (None, None)."""
    raw = str((customer.get("identity") or {}).get("name") or "").strip()
    if not raw or raw.startswith("("):
        return None, None
    m = re.match(r"^(.*?)\s*\(parent:\s*(.*?)\)\s*$", raw, flags=re.I)
    if m:
        return m.group(2).strip() or None, m.group(1).strip() or None
    return raw, None
