"""Replays the hand-made scenarios in tests/scenarios/*.json against the HTTP API.

Each scenario starts from a wiped bot (the `client` fixture), pushes its contexts,
then runs tick / reply / push steps and checks the expectations. See
tests/scenarios/README.md for the file format.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import DATASET

SCENARIOS = sorted((Path(__file__).parent / "scenarios").glob("*.json"))
SEED_FILES = {
    "merchants_seed.json": ("merchants", "merchant_id"),
    "customers_seed.json": ("customers", "customer_id"),
    "triggers_seed.json": ("triggers", "id"),
}
URL_RE = re.compile(r"https?://|www\.", re.I)


def _deep_merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _payload(item: dict[str, Any]) -> dict[str, Any]:
    if "payload" in item:
        payload = copy.deepcopy(item["payload"])
    else:
        seed = item["seed"]
        if "category" in seed:
            payload = json.loads((DATASET / "categories" / f"{seed['category']}.json").read_text(encoding="utf-8"))
        else:
            list_key, id_field = SEED_FILES[seed["file"]]
            records = json.loads((DATASET / seed["file"]).read_text(encoding="utf-8"))[list_key]
            payload = copy.deepcopy(next(r for r in records if r[id_field] == seed["id"]))
    if item.get("patch"):
        payload = _deep_merge(payload, item["patch"])
    if item.get("append_digest"):
        payload.setdefault("digest", []).append(item["append_digest"])
    return payload


def _push(client, item: dict[str, Any]):
    return client.post("/v1/context", json={
        "scope": item["scope"], "context_id": item["id"], "version": item.get("version", 1),
        "payload": _payload(item), "delivered_at": "2026-04-26T10:00:00Z"})


def _check_text(body: str, expect: dict[str, Any], where: str) -> None:
    lowered = body.lower()
    for needle in expect.get("contains", []):
        assert needle.lower() in lowered, f"{where}: expected {needle!r} in: {body}"
    for needle in expect.get("not_contains", []):
        assert needle.lower() not in lowered, f"{where}: did not expect {needle!r} in: {body}"
    assert not URL_RE.search(body), f"{where}: URL in body: {body}"


@pytest.mark.parametrize("path", SCENARIOS, ids=[p.stem for p in SCENARIOS])
def test_scenario(client, path: Path):
    scenario = json.loads(path.read_text(encoding="utf-8"))
    for item in scenario["contexts"]:
        r = _push(client, item)
        assert r.status_code == 200, f"context push {item['scope']}/{item['id']}: {r.json()}"

    last_conversation = None
    for n, step in enumerate(scenario["steps"], start=1):
        where = f"{path.stem} step {n}"
        expect = step.get("expect", {})

        if "push" in step:
            r = _push(client, step["push"])
            assert r.status_code == step.get("expect_status", 200), f"{where}: {r.status_code} {r.json()}"
            continue

        if "tick" in step:
            r = client.post("/v1/tick", json=step["tick"])
            assert r.status_code == 200, where
            actions = r.json()["actions"]
            if "count" in expect:
                assert len(actions) == expect["count"], f"{where}: {len(actions)} actions: {actions}"
            for action in actions:
                assert action["body"].strip(), f"{where}: empty body"
                if "send_as" in expect:
                    assert action["send_as"] == expect["send_as"], f"{where}: {action}"
                if "customer_id" in expect:
                    assert action["customer_id"] == expect["customer_id"], f"{where}: {action}"
                if "cta_in" in expect:
                    assert action["cta"] in expect["cta_in"], f"{where}: {action}"
                _check_text(action["body"], expect, where)
            if actions:
                last_conversation = actions[0]["conversation_id"]
            continue

        if "reply" in step:
            body = dict(step["reply"])
            if body.get("conversation_id") == "$last":
                assert last_conversation, f"{where}: no conversation started yet"
                body["conversation_id"] = last_conversation
            body.setdefault("from_role", "merchant")
            r = client.post("/v1/reply", json=body)
            assert r.status_code == 200, where
            data = r.json()
            if "action" in expect:
                assert data["action"] == expect["action"], f"{where}: {data}"
            if "action_in" in expect:
                assert data["action"] in expect["action_in"], f"{where}: {data}"
            if data["action"] == "send":
                assert data["body"].strip(), f"{where}: empty body"
                if "cta_in" in expect:
                    assert data["cta"] in expect["cta_in"], f"{where}: {data}"
                _check_text(data["body"], expect, where)
            continue

        raise AssertionError(f"{where}: unknown step {step}")
