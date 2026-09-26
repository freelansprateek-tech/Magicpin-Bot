"""Shared test fixtures.

Tests always run in template-only mode (no LLM, no network, no API key needed)
with a throwaway cache directory, so they are fast and deterministic.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ["LLM_PROVIDER"] = "none"
os.environ["CACHE_DIR"] = tempfile.mkdtemp(prefix="vera-test-cache-")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import state  # noqa: E402
from app.main import app  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "starter" / "dataset"
EXPANDED = DATASET / "expanded"


@pytest.fixture()
def client():
    state.reset_all()
    with TestClient(app) as c:
        yield c
    state.reset_all()


@pytest.fixture(scope="session")
def seeds() -> dict[str, dict]:
    cats = {}
    for f in (DATASET / "categories").glob("*.json"):
        data = json.loads(f.read_text(encoding="utf-8"))
        cats[data["slug"]] = data
    load = lambda name, key, idf: {x[idf]: x for x in json.loads((DATASET / name).read_text(encoding="utf-8"))[key]}
    return {
        "categories": cats,
        "merchants": load("merchants_seed.json", "merchants", "merchant_id"),
        "customers": load("customers_seed.json", "customers", "customer_id"),
        "triggers": load("triggers_seed.json", "triggers", "id"),
    }


@pytest.fixture(scope="session")
def expanded() -> dict[str, dict]:
    """The full 50/200/100 dataset. Generated on first use if missing."""
    if not (EXPANDED / "test_pairs.json").exists():
        subprocess.run([sys.executable, "generate_dataset.py", "--out", "./expanded"],
                       cwd=DATASET, check=True, capture_output=True)

    def load_dir(sub: str, key: str) -> dict[str, dict]:
        out = {}
        for f in sorted((EXPANDED / sub).glob("*.json")):
            data = json.loads(f.read_text(encoding="utf-8"))
            out[data[key]] = data
        return out

    return {
        "categories": load_dir("categories", "slug"),
        "merchants": load_dir("merchants", "merchant_id"),
        "customers": load_dir("customers", "customer_id"),
        "triggers": load_dir("triggers", "id"),
        "pairs": json.loads((EXPANDED / "test_pairs.json").read_text(encoding="utf-8"))["pairs"],
    }


def push(client, scope: str, cid: str, payload: dict, version: int = 1):
    return client.post("/v1/context", json={"scope": scope, "context_id": cid, "version": version,
                                            "payload": payload, "delivered_at": "2026-04-26T10:00:00Z"})


def push_all(client, data: dict) -> None:
    for slug, cat in data["categories"].items():
        push(client, "category", slug, cat)
    for mid, m in data["merchants"].items():
        push(client, "merchant", mid, m)
    for cid, c in data["customers"].items():
        push(client, "customer", cid, c)
    for tid, t in data["triggers"].items():
        push(client, "trigger", tid, t)
