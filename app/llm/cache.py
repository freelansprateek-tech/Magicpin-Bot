"""Disk cache for LLM outputs, keyed by a hash of everything that affects them.

Why disk and not only memory: temperature 0 is NOT bit-for-bit deterministic
on every provider. Caching the first validated answer per input hash makes
"same input -> same output" hold across requests AND across restarts.

Key = sha256(provider, model, prompt_version, system prompt, user prompt).
Change any of them (e.g. new digest item pushed by the judge) and you get a
new key, so new context is never answered with a stale message.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Optional


def cache_key(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


class DiskCache:
    def __init__(self, directory: str) -> None:
        self.dir = Path(directory)
        self._lock = threading.Lock()
        self._memory: dict[str, dict[str, Any]] = {}

    def _path(self, key: str) -> Path:
        return self.dir / key[:2] / f"{key}.json"

    def get(self, key: str) -> Optional[dict[str, Any]]:
        with self._lock:
            if key in self._memory:
                return self._memory[key]
        path = self._path(key)
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        with self._lock:
            self._memory[key] = value
        return value

    def set(self, key: str, value: dict[str, Any]) -> None:
        with self._lock:
            self._memory[key] = value
        try:
            path = self._path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            # write-then-rename: a crash never leaves a half-written file
            fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(value, fh, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError:
            pass  # cache is an optimisation; never fail a request because of it

    def clear_memory(self) -> None:
        with self._lock:
            self._memory.clear()
