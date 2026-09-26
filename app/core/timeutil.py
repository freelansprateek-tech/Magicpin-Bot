"""Time helpers.

Rule: all "how long ago / how long until" maths uses the `now` the judge sends
(`now` in /v1/tick, `received_at` in /v1/reply) — never the server clock. That
keeps output deterministic and consistent with the judge's simulated time.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

# Every merchant in the dataset is in India. Calendar maths ("today", "in 3 days")
# and anything shown to a merchant use IST; storage/comparisons use UTC.
IST = timezone(timedelta(hours=5, minutes=30), name="IST")

# Scenario date used by the brief and the dataset (api-call-examples.md Phase 2).
# Used when a caller gives no time at all (bot.compose() without `now`).
DEFAULT_NOW = "2026-04-26T10:30:00Z"


def parse_iso(value: object) -> Optional[datetime]:
    """Parse '2026-04-26T10:30:00Z', '2026-04-26T19:30:00+05:30' or '2026-04-26'.

    Returns a timezone-aware UTC datetime, or None if the value is missing/invalid.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            try:
                dt = datetime.combine(date.fromisoformat(text[:10]), datetime.min.time())
            except ValueError:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def now_from(value: object) -> datetime:
    """The request's notion of 'now'. Falls back to DEFAULT_NOW (not the server
    clock) when the judge sends nothing parseable, so output stays deterministic."""
    return parse_iso(value) or parse_iso(DEFAULT_NOW)  # type: ignore[return-value]


def to_ist(value: object) -> Optional[datetime]:
    """Same instant, expressed in Indian time — use for anything shown to a merchant."""
    dt = parse_iso(value)
    return dt.astimezone(IST) if dt else None


def days_between(earlier: object, later: object) -> Optional[int]:
    """Whole IST calendar days from `earlier` to `later` (negative if `later` is before)."""
    a, b = to_ist(earlier), to_ist(later)
    if a is None or b is None:
        return None
    return (b.date() - a.date()).days


def is_expired(expires_at: object, now: object) -> bool:
    """True only when both timestamps are valid and `now` is past `expires_at`."""
    exp, current = parse_iso(expires_at), parse_iso(now)
    if exp is None or current is None:
        return False
    return current > exp


def human_date(value: object) -> Optional[str]:
    """'2026-12-15' -> '15 Dec 2026' (IST calendar date)."""
    dt = to_ist(value)
    if dt is None:
        return None
    return f"{dt.day} {dt.strftime('%b %Y')}"


def human_time(value: object) -> Optional[str]:
    """'2026-04-26T19:30:00+05:30' -> '7:30pm' (IST wall-clock time)."""
    dt = to_ist(value)
    if dt is None:
        return None
    return dt.strftime("%I:%M%p").lstrip("0").lower()
