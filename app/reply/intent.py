"""Rule-based intent classifier for inbound replies (English + Hindi/Hinglish).

Why rules and not an LLM here:
  * /v1/reply must answer in well under 10s, every time
  * the replay tests (auto-reply hell, intent transition, hostile) hinge on
    getting the INTENT right, and rules are deterministic and testable
  * an LLM is still free to write the opener; routing stays predictable

Order matters — the first match wins:
  auto_reply > hostile > opt_out > off_topic > later > hedged objection > yes > objection > question > unclear
("Ok, but it's too expensive" is an objection, not a yes.)
"""

from __future__ import annotations

import re
from typing import Optional

AUTO_REPLY_PATTERNS = [
    r"thank(s| you) for (contacting|reaching|your message|messaging)",
    r"(our|the) team will (respond|get back|revert|contact)",
    r"will (respond|reply|get back to you) (shortly|soon|as soon as)",
    r"automated (assistant|message|reply|response)",
    r"\bauto[- ]?reply\b",
    r"(currently|presently) (unavailable|away|closed|out of (the )?office)",
    r"(our )?business hours are",
    r"we (have received|received) your (message|query)",
    r"aapki jaankari ke liye .*shukriya",
    r"(main|hum) aapki .*(team tak|baat) pahuncha",
]

HOSTILE_PATTERNS = [
    r"\buseless\b", r"\bspam(ming)?\b", r"\bbothering\b", r"\bharass", r"\bstupid\b", r"\bidiot",
    r"\bnonsense\b", r"\bscam\b", r"\bfraud\b", r"\bshut up\b", r"\bget lost\b", r"\bdamn\b",
    r"\bbakwas\b", r"\bpagal\b", r"\bbekaar\b", r"\bfaltu\b", r"\bwhy (are|do) you keep\b",
    r"\bwaste of (my )?time\b",
]

OPT_OUT_PATTERNS = [
    r"\bnot interested\b", r"\bno thanks?\b", r"\bno,? thank you\b", r"\bstop (messag|send|texting|this)",
    r"\bdon'?t (message|send|contact|text)\b", r"\bunsubscribe\b", r"^\s*stop\s*\.?\s*$",
    r"\bremove me\b", r"\bnahi chahiye\b", r"\bmat bhejo\b", r"\bband karo\b", r"\binterest nahi\b",
    r"बंद करो", r"मत भेजो", r"नहीं चाहिए", r"^\s*नहीं\s*[।.]?\s*$",
    r"^\s*no\s*\.?\s*$", r"^\s*nahi\s*\.?\s*$",
]

OFF_TOPIC_PATTERNS = [
    r"\bgst\b", r"\bincome tax\b", r"\btax (filing|return)", r"\bitr\b", r"\bloan\b", r"\bvisa\b",
    r"\belectricity bill\b", r"\binsurance\b", r"\baccount(ing|ant)\b", r"\bcricket score\b",
    r"\bstock market\b", r"\bshare price\b", r"\bpassport\b", r"\baadhaar\b", r"\bpan card\b",
]

LATER_PATTERNS = [
    r"\blater\b", r"\bbusy\b", r"\bnot now\b", r"\bcall (me )?(tomorrow|later)\b", r"\bbaad mein\b",
    r"\bkal baat\b", r"\bin a meeting\b", r"\bafter some time\b", r"\bnext week\b", r"\bthoda ruk",
]

YES_PATTERNS = [
    r"^\s*(yes|yeah|yep|yup|ok|okay|okk?|sure|haan|haa|ha|ji|ji haan|done|confirm(ed)?|go|go ahead|"
    r"chalo|theek hai|thik hai|kar do|karo|send|please do|👍)\b",
    r"\blet'?s (do it|go|start|proceed)\b", r"\blets (do it|go|start)\b", r"\bgo ahead\b",
    r"\bplease (send|share|draft|do|go|proceed|start)\b", r"\bsend (me|it|the)\b", r"\bi want to (join|start|do)\b",
    r"\bjudna hai\b", r"\bjudrna hai\b", r"\bkar (do|dijiye)\b", r"\bbhej (do|dijiye)\b",
    r"\bsounds good\b", r"\bdo it\b", r"\bproceed\b", r"\bwhat'?s next\b", r"\bwhats next\b",
    r"^\s*(yes|confirm)\s*[.!]*\s*$",
    r"^\s*(हाँ|हां|जी|ठीक है|कर दो|भेज दो)",
]

OBJECTION_PATTERNS = [
    r"\btoo (expensive|costly|much)\b", r"\bno budget\b", r"\bmehenga\b", r"\bcan'?t afford\b",
    r"\balready (have|do|tried|using)\b", r"\bdoesn'?t work\b", r"\bdidn'?t work\b", r"\bnot sure\b",
    r"\bwhy should i\b", r"\bwhat'?s the (point|use)\b", r"\bno time\b", r"\bnot needed\b",
    r"\bzaroorat nahi\b", r"\bpaisa nahi\b", r"\bdon'?t need\b",
]

QUESTION_START = re.compile(
    r"^\s*(what|how|when|where|why|which|who|can|could|is|are|do|does|will|kya|kaise|kab|kitna|kitne|kaun)\b",
    re.I)

HEDGE = re.compile(r"\b(but|lekin|par|however)\b", re.I)

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
HINGLISH_WORDS = re.compile(r"\b(hai|haan|nahi|kya|kaise|karo|kar|dijiye|bhej|chahiye|mujhe|aap|hum|"
                            r"theek|thik|accha|acha|kab|kitna|baad|mein|ji)\b", re.I)


def _any(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, flags=re.I) for p in patterns)


def is_auto_reply(message: str, previous_inbound: Optional[str] = None) -> bool:
    text = message.strip()
    if previous_inbound and len(text) > 25 and text.lower() == previous_inbound.strip().lower():
        return True  # the same long message verbatim twice = canned
    return _any(AUTO_REPLY_PATTERNS, text)


def classify(message: str, previous_inbound: Optional[str] = None) -> str:
    text = (message or "").strip()
    if not text:
        return "unclear"
    if is_auto_reply(text, previous_inbound):
        return "auto_reply"
    if _any(HOSTILE_PATTERNS, text):
        return "hostile"
    if _any(OPT_OUT_PATTERNS, text):
        return "opt_out"
    if _any(OFF_TOPIC_PATTERNS, text):
        return "off_topic"
    if _any(LATER_PATTERNS, text) and not _any(YES_PATTERNS, text):
        return "later"
    if _any(OBJECTION_PATTERNS, text) and HEDGE.search(text):
        return "objection"
    if _any(YES_PATTERNS, text):
        return "yes"
    if _any(OBJECTION_PATTERNS, text):
        return "objection"
    if "?" in text or QUESTION_START.search(text):
        return "question"
    return "unclear"


def detect_language(message: str) -> str:
    """Per-turn language detection: 'hi-en' if the merchant wrote Hindi/Hinglish."""
    if DEVANAGARI.search(message or ""):
        return "hi-en"
    return "hi-en" if len(HINGLISH_WORDS.findall(message or "")) >= 2 else "en"


def slot_choice(message: str) -> Optional[int]:
    """Customer booking replies like '1', '2', 'option 2' -> 1-based index."""
    m = re.match(r"^\s*(?:option\s*)?([1-9])\s*[.!]?\s*$", message or "", flags=re.I)
    return int(m.group(1)) if m else None
