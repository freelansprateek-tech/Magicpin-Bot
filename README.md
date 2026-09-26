# Vera bot — magicpin AI Challenge

An HTTP bot (`/v1/context`, `/v1/tick`, `/v1/reply`, `/v1/healthz`, `/v1/metadata`, plus `/v1/teardown`) that decides **whether** to message a merchant, and **what** to say, from the four contexts. `bot.py` exposes the brief's `compose(category, merchant, trigger, customer)`, and `submission.jsonl` holds the 30 test-pair outputs.

## Approach

```
context push ─► versioned store ─► decision layer (rules) ─► fact sheet ─► LLM draft ─► grounding validator ─► send
                (409 on same/old)   consent · suppression     only real,     (temp 0,     numbers/₹/dates must   │
                                    scoring · 1 per recipient  pre-formatted  fixed seed)  exist in the sheet,    │
                                                              facts (IST)       │          no URL/taboo/repeat    │
                                                                                 └─ fails twice / slow ─► deterministic template
```

1. **Decide before writing.** Each trigger gets an explainable additive score:
   - urgency, whether the payload carries real data, and the size of the metric move;
   - whether the merchant already said yes, and whether the trigger fits the category;
   - whether a service+price offer exists, recent engagement, and expiry;
   - penalties when the data contradicts the trigger (e.g. "renewal due" with 211 days left).

   The bot sends at most one message per recipient per tick and holds back weak or contradicted signals. It never repeats a `suppression_key`. Each recipient (merchant or customer) has a 30-minute cooldown. Hostile or "stop" replies silence that merchant for 30 days. The score breakdown becomes the `rationale`.
2. **Consent first.** A customer message goes out only if the customer opted in, the consent scope covers that kind of message, and a churned customer has agreed to win-back offers. Otherwise the merchant gets a short note and the customer is never contacted.
3. **Ground everything.**
   - **Fact sheet:** the LLM only sees values that exist in the contexts. They are pre-formatted, dates and times are in IST, and "days left" is computed from the judge's `now`, never the server clock.
   - **Validator:** it rejects any number, ₹ amount or percentage that isn't in the fact sheet (after normalising formats, e.g. "2,100" = "2100", "₹1,499" = "1499"), plus URLs, taboo words, preambles, multiple CTAs and repeats.
   - **Unknown trigger kinds:** their payload fields are still passed on, so a `weather_heatwave` message names the 44°C.
4. **Category voice** comes from `voice` (tone, taboos, vocabulary). Messages use Hindi-English code-mix when the merchant lists `hi`, and the owner's first name ("Dr." once, never twice).
5. **Replies** use rule-based intent handling, which is deterministic and fast:
   - **Auto-reply** (English or Hindi, counted per merchant across conversations): nudge the owner once, then wait 24 h, then end.
   - **"Let's do it" / "haan kar do":** switch to action immediately, with no more qualifying questions.
   - **Objection:** reframe once.
   - **Off-topic (e.g. GST):** decline politely once.
   - **Hostile:** end.

## Model choice

**OpenAI `gpt-4o-mini`** (configurable: `LLM_PROVIDER=openai|anthropic|none`). A tick can need up to 20 messages within the judge's 10 s budget. A fast, cheap model, called in parallel under a hard deadline, fits that budget. Quality comes from the fact sheet and validator rather than model size. Calls use temperature 0, a fixed seed and JSON mode, and a disk cache keyed by a hash of the prompt makes repeat inputs return identical output. If the model is slow, down or ungrounded, the deterministic template is sent instead, so the bot never times out or invents facts.

## Trade-offs

- **In-memory state, one worker:** it's the fastest and simplest option, and the brief allows it. A restart loses context, so the bot is deployed as a single always-on instance (`docs/DEPLOYMENT.md`).
- **Rules for decisions and intents, LLM only for wording:** routing is predictable and testable, but it can misread a phrasing the patterns don't cover.
- **Strict grounding:** it occasionally rejects a good LLM draft (for example one quoting a derived number), costing a retry. That's chosen over any risk of hallucination.
- **Regional-language customers (Telugu, Tamil, Kannada mixes) get English:** a model writing those languages unreliably is worse than clear English.

## What additional context would have helped most

- Real payloads for the 75 generated triggers, which are placeholders.
- Consent scopes that match the trigger kinds: most generated customers only consented to promotional offers.
- Merchants' real open appointment slots.
- Which past offers and messages actually converted.
- Locality-level (not metro-level) peer stats.

## Run it

```bash
pip install -r requirements.txt && cp .env.example .env      # add OPENAI_API_KEY
pytest                                                        # 95 tests, offline, no key needed
uvicorn app.main:app --port 8080                              # then: python scripts/smoke_test.py
python scripts/make_submission.py                             # regenerate submission.jsonl
```

Full build guide: `docs/DEVELOPMENT.md`. Deployment: `docs/DEPLOYMENT.md`. Decisions and data quirks: `PROGRESS.md`.
# Magicpin-Bot
