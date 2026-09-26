# Progress log

Read this first when resuming work in a new session.

## Status (2026-09-27)

- **Done: all 7 phases.** 95 tests pass offline (template-only mode, no API key needed), including 9 hand-made scenarios.
- **Still to do yourself:**
  1. Create `.env` and the venv on your Mac; run `pytest` (expect 95 passed).
  2. Run the judge simulator with your OpenAI key; note the weakest dimensions (`docs/DEVELOPMENT.md`).
  3. Regenerate `submission.jsonl` with the LLM: `python scripts/make_submission.py`. The committed file was made with templates only.
  4. Push to GitHub, then deploy with `docs/DEPLOYMENT.md` (always-on, 1 instance). Run the smoke test on the URL, then `/v1/teardown`.
  5. Set `BOT_SUBMITTED_AT` / `BOT_VERSION`, and submit the URL + repo.
- **Possible improvements if time allows:**
  - LLM-written replies for `question` / `objection` turns (currently rule-based templates).
  - Pre-warming the LLM cache during warmup.

## Phase 6–7 changes (this session)

- **Time shown in IST.** The IPL match showed "2:00pm" instead of 7:30pm; the refill date was one day early.
- **Cooldown is per recipient, 30 min** (was per merchant, 60 min).
  - A patient's recall is no longer blocked by a note to the clinic owner.
  - Later triggers still go out inside the 60-minute test.
- **Unknown trigger kinds** (e.g. `weather_heatwave`) pass their payload through as `event_details`, so messages name the actual event.
- **Templates:**
  - festival nudges attach an offer only when it fits the season;
  - renewal "due" with 45+ days left becomes a value check-in (and scores −30);
  - Hinglish closing lines vary deterministically;
  - the consent note says exactly why (opted out / no consent / scope / churned);
  - appointment reminders use the real slot.
- **Intent:** "Ok, but too expensive" is an objection; Devanagari yes/stop are recognised.
- **Prompt v2:** guidance for every known kind plus a default for new kinds; merchant-fit and why-now rules; small "N min" effort promises exempt from grounding.
- **Judge simulator:** config reads the key, provider and simulated now from env/.env (`JUDGE_*`). Scoring code unchanged.
- **New files:**
  - `scripts/make_submission.py` and `submission.jsonl`;
  - `Dockerfile`, `.dockerignore`, `render.yaml`, `fly.toml`;
  - `docs/DEPLOYMENT.md`, `docs/DEVELOPMENT.md`;
  - `README.md` rewritten as the one-page submission README.

## Decisions (keep consistent)

| Topic | Decision | Why |
|---|---|---|
| LLM | OpenAI `gpt-4o-mini` via `.env`; provider swappable (`LLM_PROVIDER`) | user's key; fast and cheap for 20 msgs/tick; supports `seed` |
| Determinism | temperature 0 + fixed seed + disk cache keyed by prompt hash + template fallback | temperature 0 alone isn't bit-exact across calls/restarts |
| Same-version context push | no-op + **409** `stale_version` | api-call-examples 1.5 and the reference skeleton; the brief's "no-op" is about state |
| URLs in messages | never | api-call-examples F.4: −3 per URL |
| Consent | customer message only if `reminder_opt_in` isn't false AND the consent scope covers the trigger kind (churned customers need `winback_offers`); otherwise a merchant-facing note | brief: respect consent; 32/200 customers lack it |
| Language | Hindi-English code-mix when merchant languages include `hi` (or the customer pref says hi); other regional prefs → English | the LLM can't reliably write Telugu/Tamil/Kannada |
| Time | all "how long until/ago" uses the request's `now`; merchant-facing dates/times in IST | determinism; dataset times carry +05:30 |
| Judge's `available_triggers` | trusted as "active" even if `expires_at` has passed; with an empty list the bot picks from its store and skips expired ones | the judge is the source of truth for simulated time |
| Per-tick limit | one action per recipient (merchant, or one of their customers), ≤ 20 | brief allows one per merchant+conversation |
| Cooldown | 30 min per recipient; urgency ≥ 4 bypasses | 60-minute test window; skipped triggers are re-offered on later ticks |
| Auto-reply | counted per merchant, across conversation ids: nudge once → wait 24 h → end | the simulator uses a new conversation id each turn |
| Hostile / "stop" | end + silence that merchant for 30 days | api-call-examples 4.3 |
| Storage | in-memory, single worker, single always-on instance | allowed by brief; tiny dataset; restart-safe because the judge re-pushes |

## Known data quirks handled

- Generated dentists already have "Dr." in `owner_first_name`, so the bot must not write "Dr. Dr.".
- 75/100 expanded triggers have placeholder payloads, so messages are built from merchant/category data only.
- Mismatched kinds (e.g. chronic refill for a dentist) are penalised in scoring and composed without invented details.
- The recall manufacturer differs across files (MfrZ / X): the trigger payload wins.
- The case studies contain facts that are not in the dataset; copy their structure only.
- `api-call-examples.md`'s `curl -d @dentists.json` doesn't work (bare payload, no envelope); `smoke_test.py` wraps it.
