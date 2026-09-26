"""Print the message the bot would compose for dataset triggers — no server needed.

Usage:
    python scripts/preview_messages.py                 # the 30 canonical test pairs
    python scripts/preview_messages.py --all           # all 100 expanded triggers
    python scripts/preview_messages.py --templates     # force template-only (no LLM calls)

Requires the expanded dataset:
    cd starter/dataset && python generate_dataset.py --out ./expanded && cd ../..
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.compose.composer import Composer  # noqa: E402
from app.core.timeutil import DEFAULT_NOW, now_from  # noqa: E402
from app.decision.consent import check_customer_consent  # noqa: E402

EXPANDED = ROOT / "starter" / "dataset" / "expanded"


def load_dir(sub: str, key: str) -> dict[str, dict]:
    out = {}
    for f in sorted((EXPANDED / sub).glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        out[data[key]] = data
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="all triggers, not just the 30 test pairs")
    parser.add_argument("--templates", action="store_true", help="template-only, never call the LLM")
    args = parser.parse_args()

    if not EXPANDED.exists():
        print("Expanded dataset missing. Run: cd starter/dataset && python generate_dataset.py --out ./expanded")
        return 1

    cats, merchants = load_dir("categories", "slug"), load_dir("merchants", "merchant_id")
    customers, triggers = load_dir("customers", "customer_id"), load_dir("triggers", "id")
    if args.all:
        items = [("-", tid) for tid in triggers]
    else:
        pairs = json.loads((EXPANDED / "test_pairs.json").read_text(encoding="utf-8"))["pairs"]
        items = [(p["test_id"], p["trigger_id"]) for p in pairs]

    composer = Composer(use_default_provider=not args.templates)
    now = now_from(DEFAULT_NOW)
    failures = 0
    for test_id, tid in items:
        trg = triggers[tid]
        merchant = merchants[trg["merchant_id"]]
        customer = customers.get(trg.get("customer_id") or "")
        facing, reason = False, None
        if customer is not None or trg.get("scope") == "customer":
            d = check_customer_consent(trg, customer)
            facing, reason = d.allowed, d.reason
        msg = composer.compose(category=cats[merchant["category_slug"]], merchant=merchant, trigger=trg,
                               customer=customer, customer_facing=facing, consent_reason=reason, now=now)
        errs = msg.meta.get("validator_errors") or []
        failures += bool(errs)
        print(f"== {test_id} {tid}  [{msg.send_as} / {msg.cta} / {msg.meta['path']}]")
        print(msg.body)
        if errs:
            print(f"   !! validator: {errs}")
        print()
    print(f"{len(items)} messages, {failures} with validator errors")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
