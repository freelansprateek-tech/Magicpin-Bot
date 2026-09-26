# Hand-made scenarios

Situations the base dataset doesn't contain but the real judge can create
(new trigger kinds, injected digest items, version bumps, surprise customers,
multi-turn curveballs). `tests/test_scenarios.py` replays each file against the
HTTP API with a fresh bot.

Format:
- `contexts`: pushed in order. Each item has either
  - a literal `payload`, or
  - `seed` = `{"file": "merchants_seed.json", "id": "m_001_..."}` or `{"category": "dentists"}`,
    plus an optional `patch` that is deep-merged into it.
- `steps`: run in order. A step is one of:
  - `{"tick": {...}, "expect": {...}}`
  - `{"reply": {...}, "expect": {...}}`, where the conversation id `"$last"` means the last conversation started by a tick
  - `{"push": {...}, "expect_status": 409}`

Expectation keys:
- `count`, `send_as`, `cta_in`, `customer_id`, `action`, `action_in`
- `contains`, `not_contains` (case-insensitive)

Every sent body is also checked: no URL, and never an empty body.
