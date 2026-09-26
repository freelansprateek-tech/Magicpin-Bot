"""Pre-submission stress test: will the bot survive the judge's load and time limits?

Checks, against a RUNNING bot (local or deployed):
  1. healthz answers and reports the loaded dataset
  2. one big /v1/tick (up to 20 actions) finishes inside the 10 s budget, every action well-formed
  3. sustained mixed traffic at the judge's max rate (10 req/s) for N seconds:
     no errors, no timeouts, latency per endpoint (p50 / p95 / max)
  4. healthz still OK afterwards and nothing was lost

Usage (load the data first, exactly like the judge's warmup):
    python scripts/load_dataset.py --url http://localhost:8080
    python scripts/stress_test.py --url http://localhost:8080 --seconds 30

Standard library only, so it runs anywhere.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parent.parent
EXPANDED = ROOT / "starter" / "dataset" / "expanded"
REQUIRED = {"conversation_id", "merchant_id", "send_as", "trigger_id", "template_name",
            "template_params", "body", "cta", "suppression_key", "rationale"}
BUDGET = {"/v1/healthz": 2.0, "/v1/tick": 10.0, "/v1/reply": 10.0}   # api-call-examples summary table
REPLIES = [
    "Yes please, go ahead", "Thank you for contacting us! Our team will respond shortly.",
    "Not interested", "How much does it cost?", "Btw can you help with my GST filing?",
    "haan kar do", "Call me later, busy now", "Ok but it's too expensive",
]
GREEN, RED, YELLOW, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[0m"


def call(base: str, method: str, path: str, body: dict | None = None, timeout: float = 30.0):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
    start = time.perf_counter()
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            return resp.status, payload, time.perf_counter() - start
    except error.HTTPError as e:
        return e.code, {}, time.perf_counter() - start
    except Exception as e:  # timeout / connection refused
        return 0, {"error": str(e)}, time.perf_counter() - start


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(p / 100 * (len(ordered) - 1))))]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument("--seconds", type=int, default=30, help="duration of the 10 req/s phase")
    parser.add_argument("--rps", type=float, default=10.0)
    parser.add_argument("--now", default="2026-04-26T10:30:00Z")
    args = parser.parse_args()
    base = args.url.rstrip("/")
    failures: list[str] = []

    # 1. healthz + data loaded
    status, health, secs = call(base, "GET", "/v1/healthz")
    if status != 200:
        print(f"{RED}Bot not reachable at {base} ({health}){RESET}")
        return 1
    counts = health.get("contexts_loaded", {})
    print(f"1. healthz OK in {secs * 1000:.0f} ms — loaded {counts}")
    if not counts.get("merchant") or not counts.get("trigger"):
        print(f"{YELLOW}   No merchants/triggers loaded. Run scripts/load_dataset.py first for a realistic test.{RESET}")

    # 2. one full tick against the 10 s budget
    trigger_ids = sorted(p.stem for p in (EXPANDED / "triggers").glob("*.json"))[:60] if EXPANDED.exists() else []
    status, data, secs = call(base, "POST", "/v1/tick", {"now": args.now, "available_triggers": trigger_ids})
    actions = data.get("actions", []) if status == 200 else []
    bad = [a.get("trigger_id") for a in actions if REQUIRED - set(a) or not str(a.get("body", "")).strip()]
    print(f"2. big tick: {len(actions)} actions in {secs:.2f} s (budget 10 s)")
    if status != 200:
        failures.append(f"tick returned HTTP {status}")
    if secs > BUDGET["/v1/tick"]:
        failures.append(f"tick took {secs:.1f} s > 10 s")
    if len(actions) > 20:
        failures.append(f"tick returned {len(actions)} actions > 20")
    if bad:
        failures.append(f"malformed actions: {bad}")
    conv_ids = [a["conversation_id"] for a in actions] or ["conv_stress"]
    merchant_ids = [a["merchant_id"] for a in actions] or [None]

    # 3. sustained traffic at the judge's max rate
    print(f"3. {args.rps:.0f} req/s for {args.seconds} s (healthz / reply / tick mix)...")
    latencies: dict[str, list[float]] = {k: [] for k in BUDGET}
    errors: list[str] = []
    lock = threading.Lock()

    def one(i: int) -> None:
        kind = ("/v1/healthz", "/v1/reply", "/v1/reply", "/v1/tick")[i % 4]
        if kind == "/v1/healthz":
            status, body, secs = call(base, "GET", kind)
        elif kind == "/v1/reply":
            status, body, secs = call(base, "POST", kind, {
                "conversation_id": conv_ids[i % len(conv_ids)], "merchant_id": merchant_ids[i % len(merchant_ids)],
                "customer_id": None, "from_role": "merchant", "message": REPLIES[i % len(REPLIES)],
                "received_at": args.now, "turn_number": 2 + i % 4})
            if status == 200 and body.get("action") == "send" and not str(body.get("body", "")).strip():
                status = -1
        else:
            status, body, secs = call(base, "POST", kind, {"now": args.now, "available_triggers": trigger_ids[:10]})
        with lock:
            latencies[kind].append(secs)
            if status != 200:
                errors.append(f"{kind} -> {status} {body.get('error', '')}".strip())

    threads: list[threading.Thread] = []
    interval = 1.0 / args.rps
    start = time.perf_counter()
    for i in range(int(args.seconds * args.rps)):
        target = start + i * interval
        time.sleep(max(0.0, target - time.perf_counter()))
        t = threading.Thread(target=one, args=(i,), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(timeout=35)

    total = sum(len(v) for v in latencies.values())
    print(f"   {total} requests, {len(errors)} errors")
    for path, values in latencies.items():
        if not values:
            continue
        p95, worst = pct(values, 95), max(values)
        flag = RED if worst > BUDGET[path] else GREEN
        print(f"   {path:<12} n={len(values):<4} p50={statistics.median(values) * 1000:6.0f} ms  "
              f"p95={p95 * 1000:6.0f} ms  max={flag}{worst * 1000:6.0f} ms{RESET}  (budget {BUDGET[path]:.0f} s)")
        if worst > BUDGET[path]:
            failures.append(f"{path} slowest call {worst:.1f} s > {BUDGET[path]:.0f} s budget")
    if errors:
        failures.append(f"{len(errors)} failed requests, e.g. {errors[:3]}")

    # 4. still healthy, nothing lost
    status, after, _ = call(base, "GET", "/v1/healthz")
    print(f"4. healthz after load: HTTP {status}, loaded {after.get('contexts_loaded')}")
    if status != 200:
        failures.append("healthz failed after load")
    elif after.get("contexts_loaded") != counts:
        failures.append("context counts changed during the test (was the bot restarted?)")

    print()
    if failures:
        for f in failures:
            print(f"{RED}FAIL{RESET} {f}")
        return 1
    print(f"{GREEN}PASS — the bot handled the judge's rate and time limits.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
