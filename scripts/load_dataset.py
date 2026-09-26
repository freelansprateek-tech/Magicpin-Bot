"""Push the expanded dataset (5 categories, 50 merchants, 200 customers, 100 triggers)
into a RUNNING bot, the way the judge's warmup does — then optionally tick.

Usage:
    python scripts/load_dataset.py                       # push everything to localhost:8080
    python scripts/load_dataset.py --tick                # ...then tick with all 100 triggers
    python scripts/load_dataset.py --url https://your-bot.example.com --no-triggers

Generate the expanded dataset first:
    cd starter/dataset && python generate_dataset.py --out ./expanded && cd ../..
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parent.parent
EXPANDED = ROOT / "starter" / "dataset" / "expanded"


def post(base: str, path: str, body: dict) -> tuple[int, dict]:
    req = request.Request(base + path, data=json.dumps(body).encode("utf-8"), method="POST",
                          headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument("--version", type=int, default=1)
    parser.add_argument("--no-triggers", action="store_true")
    parser.add_argument("--tick", action="store_true", help="call /v1/tick with all triggers afterwards")
    parser.add_argument("--now", default="2026-04-26T10:30:00Z")
    args = parser.parse_args()
    base = args.url.rstrip("/")

    if not EXPANDED.exists():
        print("Missing starter/dataset/expanded. Run: cd starter/dataset && python generate_dataset.py --out ./expanded")
        return 1

    plan = [("category", "categories", "slug"), ("merchant", "merchants", "merchant_id"),
            ("customer", "customers", "customer_id")]
    if not args.no_triggers:
        plan.append(("trigger", "triggers", "id"))

    trigger_ids: list[str] = []
    for scope, folder, key in plan:
        ok = stale = bad = 0
        for f in sorted((EXPANDED / folder).glob("*.json")):
            payload = json.loads(f.read_text(encoding="utf-8"))
            status, _ = post(base, "/v1/context", {"scope": scope, "context_id": payload[key],
                                                   "version": args.version, "payload": payload,
                                                   "delivered_at": args.now})
            ok += status == 200
            stale += status == 409
            bad += status not in (200, 409)
            if scope == "trigger":
                trigger_ids.append(payload[key])
        print(f"{scope:9s} accepted={ok} already_had_version={stale} errors={bad}")

    with request.urlopen(base + "/v1/healthz", timeout=10) as resp:
        print("healthz:", json.loads(resp.read().decode("utf-8"))["contexts_loaded"])

    if args.tick and trigger_ids:
        start = time.time()
        status, data = post(base, "/v1/tick", {"now": args.now, "available_triggers": trigger_ids})
        actions = data.get("actions", [])
        print(f"\ntick -> HTTP {status}, {len(actions)} actions in {time.time() - start:.2f}s\n")
        for a in actions:
            print(f"[{a['trigger_id']}] {a['send_as']} / {a['cta']}\n{a['body']}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
