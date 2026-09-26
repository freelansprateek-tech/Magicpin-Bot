"""Versioned in-memory context store.

Contract (challenge-testing-brief.md §2.1 + api-call-examples.md 1.5/1.6):
  * key = (scope, context_id)
  * nothing stored yet     -> store it                     -> 200 accepted
  * incoming > stored      -> replace atomically           -> 200 accepted
  * incoming == stored     -> no-op (idempotent re-post)   -> 409 stale_version
  * incoming <  stored     -> ignored                      -> 409 stale_version

Why in-memory (vs SQLite / Redis):
  * the whole base dataset is ~255 small JSON docs (< 1 MB) and the brief
    explicitly allows in-memory storage;
  * every tick reads many contexts, so reads must be sub-millisecond;
  * cost: a restart loses state. Acceptable because we run ONE always-on
    instance and the judge pushes context at warmup. SQLite would add
    durability across restarts; Redis would add sharing across replicas —
    neither is needed for a single-instance, 60-minute test.
"""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional


SCOPES = ("category", "merchant", "customer", "trigger")


def _ack_time() -> str:
    """Wall-clock time for the ack only (never used in composition)."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class _Entry:
    version: int
    payload: dict[str, Any]


@dataclass(frozen=True)
class PutResult:
    accepted: bool
    current_version: int
    stored_at: Optional[str] = None


class ContextStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data: dict[tuple[str, str], _Entry] = {}

    # ------------------------------------------------------------------ writes
    def put(self, scope: str, context_id: str, version: int, payload: dict[str, Any]) -> PutResult:
        if scope not in SCOPES:
            raise ValueError(f"invalid scope: {scope!r}")
        # Deep-copy BEFORE taking the lock: the caller can't mutate what we store,
        # and a slow copy of a 500 KB payload never blocks readers.
        entry = _Entry(version, copy.deepcopy(payload))
        key = (scope, context_id)
        with self._lock:
            current = self._data.get(key)
            if current is not None and version <= current.version:
                return PutResult(accepted=False, current_version=current.version)
            # Single reference swap = atomic replace: readers see old OR new, never a mix.
            self._data[key] = entry
            return PutResult(accepted=True, current_version=version, stored_at=_ack_time())

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    # ------------------------------------------------------------------- reads
    def get(self, scope: str, context_id: Optional[str]) -> Optional[dict[str, Any]]:
        if not context_id:
            return None
        with self._lock:
            entry = self._data.get((scope, context_id))
        return entry.payload if entry else None

    def version(self, scope: str, context_id: str) -> Optional[int]:
        with self._lock:
            entry = self._data.get((scope, context_id))
        return entry.version if entry else None

    def all(self, scope: str) -> dict[str, dict[str, Any]]:
        """{context_id: payload} for one scope (a snapshot, safe to iterate)."""
        with self._lock:
            return {cid: e.payload for (s, cid), e in self._data.items() if s == scope}

    def counts(self) -> dict[str, int]:
        result = {scope: 0 for scope in SCOPES}
        with self._lock:
            for scope, _cid in self._data:
                result[scope] += 1
        return result

    # ----------------------------------------------------------- convenience
    def category_for(self, merchant: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        """The merchant's CategoryContext, looked up by merchant.category_slug
        (never parsed from the merchant id — generated ids contain typos)."""
        if not merchant:
            return None
        return self.get("category", merchant.get("category_slug"))
