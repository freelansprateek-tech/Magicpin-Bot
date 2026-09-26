# Development guide

Build and run instructions. The one-page submission summary is in `../README.md`; deployment is in `DEPLOYMENT.md`.

An HTTP bot that magicpin's judge calls to push context and ask for proactive WhatsApp messages to merchants (and, on their behalf, to their customers).

At its core is a deterministic `compose(category, merchant, trigger, customer)` pipeline:

1. **Decision layer** (rules, no LLM) picks the single best trigger per merchant and checks consent and suppression.
2. **Fact sheet** holds only grounded, pre-formatted facts from the four contexts. Dates and times are shown in IST.
3. **LLM** (OpenAI or Anthropic, set in `.env`) writes the message from the fact sheet.
4. **Grounding validator** rejects invented numbers, prices, URLs, taboo words, repeats and extra CTAs.
5. **Deterministic template** is used if the LLM fails validation twice, is slow or is not configured.

> `PROGRESS.md` (repo root) records what is done, every decision, and what comes next.

---

## 1. Project structure

```
magicpin-ai-challenge/            ← project root (your GitHub repo can be named vera-bot)
├── README.md                     one-page submission README (approach, model, trade-offs)
├── PROGRESS.md                   phase status + decisions — read this to resume work
├── docs/DEVELOPMENT.md           this guide
├── docs/DEPLOYMENT.md            Railway / Render / Fly, no cold starts
├── Dockerfile  .dockerignore     production image (1 worker, non-root, healthcheck)   (P7)
├── render.yaml  fly.toml         platform configs, always-on, 1 instance             (P7)
├── submission.jsonl              30 canonical test-pair outputs                      (P6)
├── .gitignore
├── .env.example                  template of every env var (committed)
├── .env                          YOUR secrets (you create it — NEVER committed)
├── requirements.txt              pinned dependencies
├── pytest.ini
├── bot.py                        compose() entry point required by brief §7.1        (P4)
│
├── app/
│   ├── __init__.py
│   ├── main.py                   FastAPI app: all endpoints                          (P1–P5)
│   ├── config.py                 settings from env vars                              (P1)
│   ├── schemas.py                request/response models from the testing brief      (P1)
│   ├── state.py                  process-wide singletons + reset for teardown        (P2)
│   ├── core/
│   │   ├── store.py              versioned context store (409 on same/lower version) (P2)
│   │   ├── timeutil.py           "now" from the request; IST dates/times             (P2)
│   │   └── conversations.py      per-conversation + per-merchant reply state         (P5)
│   ├── decision/
│   │   ├── kinds.py              trigger-kind registry: category fit, consent scopes (P3)
│   │   ├── consent.py            customer consent + opt-in gate                      (P3)
│   │   ├── suppression.py        sent keys, merchant blocks, cooldown                (P3)
│   │   ├── scoring.py            explainable additive score per candidate            (P3)
│   │   └── planner.py            picks ≤1 action per merchant, ≤20 per tick          (P3)
│   ├── compose/
│   │   ├── facts.py              builds the fact sheet                               (P4)
│   │   ├── voice.py              category tone, taboos, salutation, language         (P4)
│   │   ├── prompts.py            versioned prompts, per-kind guidance                (P4)
│   │   ├── validator.py          grounding + CTA / taboo / URL / repeat checks       (P4)
│   │   ├── templates.py          deterministic fallback message per trigger kind     (P4)
│   │   └── composer.py           LLM → validate → retry once → template              (P4)
│   ├── llm/
│   │   ├── base.py               provider interface + factory                        (P4)
│   │   ├── openai_provider.py    temperature 0 + fixed seed + JSON mode              (P4)
│   │   ├── anthropic_provider.py                                                     (P4)
│   │   └── cache.py              disk cache keyed by a hash of the prompt            (P4)
│   └── reply/
│       ├── intent.py             rule-based intent + language detection              (P5)
│       └── handler.py            next action for /v1/reply                           (P5)
│
├── scripts/
│   ├── smoke_test.py             replays api-call-examples.md against a running bot  (P1)
│   ├── load_dataset.py           pushes the expanded dataset into a running bot      (P3)
│   ├── preview_messages.py       prints the message for each of the 30 test pairs    (P4)
│   └── make_submission.py        writes submission.jsonl via bot.compose()           (P6)
│
├── tests/                        95 tests, run offline in template-only mode
│   ├── conftest.py               fixtures; generates the expanded dataset if missing
│   ├── test_endpoints.py  test_store.py  test_timeutil.py  test_decision.py
│   ├── test_validator.py  test_compose.py  test_reply.py
│   ├── test_scenarios.py         replays the hand-made scenarios below                (P6)
│   └── scenarios/                9 situations NOT in the dataset (new kinds, injected
│                                 digest, version bump, surprise customer, opt-out,
│                                 hostile+GST, Hinglish commit, Hindi auto-reply, expiry)
│
└── starter/                      magicpin's starter pack (unchanged)
    ├── challenge-brief.md  challenge-testing-brief.md
    ├── engagement-design.md  engagement-research.md
    ├── judge_simulator.py        patched config only: key/provider/now read from env (P6)
    ├── examples/                 api-call-examples.md, case-studies.md
    └── dataset/                  categories/, *_seed.json, generate_dataset.py
        └── expanded/             generated — not committed
```

Every folder under `app/` and `tests/` also contains an `__init__.py`.

## 2. Setup (first time, macOS)

Open the project folder in VS Code (*File → Open Folder…*), then open a terminal (*Terminal → New Terminal*):

```bash
python3.11 --version                 # if "not found": brew install python@3.11
python3.11 -m venv .venv
source .venv/bin/activate            # prompt now starts with (.venv)
pip install -r requirements.txt
cp .env.example .env                 # then edit .env (see below)
```

On Windows, use `py -3.11 -m venv .venv`, then `.venv\Scripts\Activate.ps1`, then `copy .env.example .env`.

In VS Code, pick the interpreter: *Cmd+Shift+P → "Python: Select Interpreter" → .venv*.

## 3. Environment variables (`.env`)

| Variable | Meaning | Your value |
|---|---|---|
| `BOT_TEAM_NAME`, `BOT_TEAM_MEMBERS`, `BOT_CONTACT_EMAIL` | shown in `/v1/metadata` | your name / email |
| `LLM_PROVIDER` | `openai`, `anthropic` or `none` (templates only) | `openai` |
| `LLM_MODEL` | model id | `gpt-4o-mini` |
| `OPENAI_API_KEY` | secret — **only** in `.env` | `sk-...` |
| `ANTHROPIC_API_KEY` | secret | leave empty |
| `LLM_TIMEOUT_SECONDS` | per-call timeout before template fallback | `8` |
| `LLM_MAX_PARALLEL` | concurrent LLM calls per tick | `8` |
| `CACHE_DIR` | where LLM outputs are cached (deterministic replays) | `cache` |
| `PORT`, `LOG_LEVEL`, `BOT_VERSION`, `BOT_SUBMITTED_AT` | server + metadata | leave defaults |

> ⚠️ **Never commit `.env`**, and never paste an API key into any `.py` file, including `starter/judge_simulator.py`. If a key is ever pushed by mistake, revoke it on the provider's dashboard immediately.

## 4. Run and test

```bash
pytest                                            # expect: 95 passed (no API key needed)

uvicorn app.main:app --reload --port 8080         # terminal 1: the bot
```

In terminal 2, run `source .venv/bin/activate` first:
```bash
python scripts/smoke_test.py                      # all checks PASS
python scripts/load_dataset.py --tick             # warmup like the judge, then one tick
python scripts/preview_messages.py --templates    # 30 test-pair messages, no LLM cost
python scripts/preview_messages.py                # same, written by your LLM (uses the API)
```

Interactive API docs: http://localhost:8080/docs

### Judge simulator (uses your OpenAI key from `.env`)

Restart the bot before each run (or `curl -X POST localhost:8080/v1/teardown`). A second run re-pushes version 1 of everything, and the bot correctly answers 409, which the simulator prints as FAIL.

```bash
python starter/judge_simulator.py                                   # JUDGE_SCENARIO from .env (default: all)
JUDGE_SCENARIO=full_evaluation python starter/judge_simulator.py    # scores every message it gets
```

The simulator repeats one simulated "now" on every tick, so the bot's per-recipient 30-minute cooldown makes it look more restrained than in the real test, where time advances.

### Submission file

```bash
python scripts/make_submission.py                 # 30 lines, written by your LLM (cached after the first run)
python scripts/make_submission.py --templates     # deterministic, no API calls
```

> Run the server with **one** worker (the default). State is kept in memory.

## 5. Git and GitHub

First time only:
```bash
git init
git add .
git status        # must NOT list .env, .venv/, cache/, __pycache__/ or starter/dataset/expanded/
git commit -m "Vera bot: phases 1-7"
```

On github.com, create a new **private** repository (for example `vera-bot`) with no README, .gitignore or licence. Then:
```bash
git remote add origin https://github.com/<your-username>/vera-bot.git
git branch -M main
git push -u origin main
```

After each later phase:
```bash
pytest && git add . && git commit -m "<phase message>" && git push
```

## 6. Phases

| Phase | Deliverable | Status |
|---|---|---|
| 1 | Endpoints with spec-shaped responses, smoke test | ✅ |
| 2 | Versioned context store + tests | ✅ |
| 3 | Decision layer (consent, suppression, scoring, planner) + tests | ✅ |
| 4 | Composer, grounding validator, templates, LLM providers, `bot.py` | ✅ |
| 5 | `/v1/reply` intent handling + conversation state | ✅ |
| 6 | Judge simulator patch, quality fixes, hand-made scenarios, `submission.jsonl` | ✅ |
| 7 | Dockerfile, deploy configs without cold starts, one-page submission README | ✅ |
