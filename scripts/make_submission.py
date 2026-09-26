"""Build submission.jsonl (challenge-brief.md §7.2): one line per canonical test pair.

Usage:
    python scripts/make_submission.py                # uses the LLM configured in .env
    python scripts/make_submission.py --templates    # deterministic templates only (no API calls)

Each line: {"test_id", "body", "cta", "send_as", "suppression_key", "rationale"}
It goes through bot.compose() — the exact function the brief asks for — with
now = the dataset's scenario date, so the output is reproducible.
Needs the expanded dataset (generated automatically if missing).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "starter" / "dataset"
EXPANDED = DATASET / "expanded"
OUT = ROOT / "submission.jsonl"

REQUIRED = ("test_id", "body", "cta", "send_as", "suppression_key", "rationale")
VALID_CTAS = {"open_ended", "binary_yes_no", "binary_confirm_cancel", "multi_choice_slot", "none"}
URL_RE = re.compile(r"https?://|www\.", re.I)


def load_dir(sub: str, key: str) -> dict[str, dict]:
    out = {}
    for f in sorted((EXPANDED / sub).glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        out[data[key]] = data
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--templates", action="store_true", help="template-only, never call the LLM")
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()

    if args.templates:
        os.environ["LLM_PROVIDER"] = "none"          # must be set before app.config is imported
    sys.path.insert(0, str(ROOT))
    from app.core.timeutil import DEFAULT_NOW  # noqa: E402
    from bot import compose  # noqa: E402

    if not (EXPANDED / "test_pairs.json").exists():
        print("Generating the expanded dataset...")
        subprocess.run([sys.executable, "generate_dataset.py", "--out", "./expanded"], cwd=DATASET, check=True)

    cats = load_dir("categories", "slug")
    merchants = load_dir("merchants", "merchant_id")
    customers = load_dir("customers", "customer_id")
    triggers = load_dir("triggers", "id")
    pairs = json.loads((EXPANDED / "test_pairs.json").read_text(encoding="utf-8"))["pairs"]

    lines, problems = [], []
    for pair in pairs:
        trigger = triggers[pair["trigger_id"]]
        merchant = merchants[pair["merchant_id"]]
        customer = customers.get(pair.get("customer_id") or "")
        msg = compose(cats[merchant["category_slug"]], merchant, trigger, customer, now=DEFAULT_NOW)
        line = {"test_id": pair["test_id"], **{k: msg[k] for k in REQUIRED if k != "test_id"}}
        # Shape checks — a malformed line is scored 0 by the judge.
        if any(not str(line[k]).strip() for k in REQUIRED):
            problems.append(f"{pair['test_id']}: empty field")
        if line["cta"] not in VALID_CTAS:
            problems.append(f"{pair['test_id']}: invalid cta {line['cta']}")
        if URL_RE.search(line["body"]):
            problems.append(f"{pair['test_id']}: URL in body")
        lines.append(line)
        path = "llm" if "[written by: llm]" in line["rationale"] or "[written by: cache]" in line["rationale"] else "template"
        print(f"{pair['test_id']}  {line['send_as']:<18} {line['cta']:<22} {path:<8} {line['body'][:70]}...")

    Path(args.out).write_text("".join(json.dumps(l, ensure_ascii=False) + "\n" for l in lines), encoding="utf-8")
    print(f"\nWrote {len(lines)} lines to {args.out}")
    if len(lines) != 30:
        problems.append(f"expected 30 lines, got {len(lines)}")
    for p in problems:
        print("PROBLEM:", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
