"""Smoke test against a RUNNING bot, replaying the calls from
starter/examples/api-call-examples.md.

Usage (with the server running in another terminal):
    python scripts/smoke_test.py
    python scripts/smoke_test.py --url https://your-bot.onrender.com

Uses only the Python standard library, so it works the same on Mac and Windows.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "starter" / "dataset"

GREEN, RED, YELLOW, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[0m"
failures = 0


def call(base: str, method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = request.Request(base + path, data=data, method=method,
                          headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


def check(name: str, ok: bool, info: str = "") -> None:
    global failures
    if ok:
        print(f"{GREEN}[PASS]{RESET} {name}")
    else:
        failures += 1
        print(f"{RED}[FAIL]{RESET} {name}  {info}")


def load_seed(filename: str, list_key: str, id_field: str, wanted_id: str) -> dict:
    """Return one record from a *_seed.json file, e.g. the merchant with a given id."""
    items = json.loads((DATASET / filename).read_text(encoding="utf-8"))[list_key]
    return next(item for item in items if item.get(id_field) == wanted_id)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8080")
    base = parser.parse_args().url.rstrip("/")
    print(f"Smoke-testing {base}\n")

    # 1.1 healthz
    status, data = call(base, "GET", "/v1/healthz")
    check("1.1 GET /v1/healthz", status == 200 and data.get("status") == "ok"
          and "contexts_loaded" in data, str(data))

    # 1.2 metadata
    status, data = call(base, "GET", "/v1/metadata")
    check("1.2 GET /v1/metadata", status == 200 and "team_name" in data, str(data))

    # 1.3 push the FULL dentists category, wrapped in the context envelope.
    # (The raw `curl -d @dentists.json` in api-call-examples.md would fail:
    #  the file is a bare payload, not a {scope, context_id, version, payload} body.)
    category = json.loads((DATASET / "categories" / "dentists.json").read_text(encoding="utf-8"))
    status, data = call(base, "POST", "/v1/context", {
        "scope": "category", "context_id": "dentists", "version": 1,
        "payload": category, "delivered_at": "2026-04-26T09:45:00Z"})
    check("1.3 POST /v1/context (category)", status == 200 and data.get("accepted") is True, str(data))

    # 1.4 push Dr. Meera's merchant context
    merchant = load_seed("merchants_seed.json", "merchants", "merchant_id", "m_001_drmeera_dentist_delhi")
    status, data = call(base, "POST", "/v1/context", {
        "scope": "merchant", "context_id": merchant["merchant_id"], "version": 1,
        "payload": merchant, "delivered_at": "2026-04-26T09:45:30Z"})
    check("1.4 POST /v1/context (merchant)", status == 200 and data.get("accepted") is True, str(data))

    # 1.5 same version again -> documented as 409 stale_version (store arrives in Phase 2)
    status, data = call(base, "POST", "/v1/context", {
        "scope": "merchant", "context_id": merchant["merchant_id"], "version": 1,
        "payload": merchant, "delivered_at": "2026-04-26T09:45:30Z"})
    if status == 409:
        check("1.5 same version re-push -> 409", data.get("reason") == "stale_version", str(data))
    else:
        print(f"{YELLOW}[SKIP]{RESET} 1.5 same version re-push -> 409 (expected from Phase 2; got {status})")

    # 400 on a malformed push
    status, data = call(base, "POST", "/v1/context", {
        "scope": "planet", "context_id": "x", "version": 1, "payload": {}})
    check("400 on invalid scope", status == 400 and data.get("reason") == "invalid_scope", str(data))

    # 2.1 push a trigger
    trigger = load_seed("triggers_seed.json", "triggers", "id", "trg_001_research_digest_dentists")
    status, data = call(base, "POST", "/v1/context", {
        "scope": "trigger", "context_id": trigger["id"], "version": 1,
        "payload": trigger, "delivered_at": "2026-04-26T10:32:00Z"})
    check("2.1 POST /v1/context (trigger)", status == 200 and data.get("accepted") is True, str(data))

    # 2.2 tick
    status, data = call(base, "POST", "/v1/tick", {
        "now": "2026-04-26T10:35:00Z", "available_triggers": [trigger["id"]]})
    check("2.2 POST /v1/tick", status == 200 and isinstance(data.get("actions"), list), str(data))
    required = {"conversation_id", "merchant_id", "send_as", "trigger_id", "template_name",
                "template_params", "body", "cta", "suppression_key", "rationale"}
    for i, action in enumerate(data.get("actions", [])):
        missing = required - set(action)
        check(f"    action[{i}] has all required fields", not missing, f"missing {missing}")

    # 2.4 reply
    status, data = call(base, "POST", "/v1/reply", {
        "conversation_id": "conv_001", "merchant_id": merchant["merchant_id"],
        "customer_id": None, "from_role": "merchant",
        "message": "Yes please send the abstract. Also draft the patient WhatsApp.",
        "received_at": "2026-04-26T10:42:00Z", "turn_number": 2})
    ok = status == 200 and data.get("action") in {"send", "wait", "end"} and data.get("rationale")
    if ok and data["action"] == "send":
        ok = bool(data.get("body", "").strip())
    check("2.4 POST /v1/reply", bool(ok), str(data))

    print()
    if failures:
        print(f"{RED}{failures} check(s) failed{RESET}")
        return 1
    print(f"{GREEN}All checks passed{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
