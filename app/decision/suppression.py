"""Suppression ledger: never send the same nudge twice, respect opt-outs.

Three kinds of memory:
  * sent suppression keys  -> a trigger's nudge goes out at most once
  * merchant blocks        -> hostile / "stop messaging me" silences a merchant
                              for 30 days (api-call-examples 4.3)
  * last-send time         -> soft cooldown per RECIPIENT (the merchant, or one of
                              their customers) so nobody gets two proactive
                              messages within 30 min (urgent triggers bypass it).
                              A skipped trigger isn't lost: the judge keeps listing
                              active triggers, so it goes out on a later tick.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Optional

RECIPIENT_COOLDOWN = timedelta(minutes=30)   # the whole test window is 60 simulated minutes
URGENT_BYPASS_LEVEL = 4


def recipient_key(merchant_id: str, customer_id: str | None = None) -> str:
    """Who actually receives the message: the merchant, or one customer of theirs.
    A patient's recall reminder must not be blocked by a note sent to the clinic owner."""
    return f"{merchant_id}::{customer_id}" if customer_id else merchant_id


class SuppressionLedger:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sent_keys: set[str] = set()
        self._blocked_until: dict[str, datetime] = {}
        self._block_reason: dict[str, str] = {}
        self._last_sent: dict[str, datetime] = {}

    def clear(self) -> None:
        with self._lock:
            self._sent_keys.clear()
            self._blocked_until.clear()
            self._block_reason.clear()
            self._last_sent.clear()

    # ----------------------------------------------------------------- writes
    def mark_sent(self, suppression_key: str, recipient: str, at: datetime) -> None:
        with self._lock:
            if suppression_key:
                self._sent_keys.add(suppression_key)
            self._last_sent[recipient] = at

    def block_merchant(self, merchant_id: str, until: datetime, reason: str) -> None:
        with self._lock:
            self._blocked_until[merchant_id] = until
            self._block_reason[merchant_id] = reason

    # ------------------------------------------------------------------ reads
    def was_sent(self, suppression_key: str) -> bool:
        with self._lock:
            return bool(suppression_key) and suppression_key in self._sent_keys

    def blocked_reason(self, merchant_id: str, now: datetime) -> Optional[str]:
        with self._lock:
            until = self._blocked_until.get(merchant_id)
            if until and now < until:
                return self._block_reason.get(merchant_id, "blocked")
            return None

    def in_cooldown(self, recipient: str, now: datetime, urgency: int) -> bool:
        if urgency >= URGENT_BYPASS_LEVEL:
            return False
        with self._lock:
            last = self._last_sent.get(recipient)
            return last is not None and timedelta(0) <= now - last < RECIPIENT_COOLDOWN
