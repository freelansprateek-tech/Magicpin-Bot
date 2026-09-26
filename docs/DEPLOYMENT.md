# Deployment

The bot is one Docker container running **one** uvicorn worker. All state (pushed contexts, conversations and suppression) lives in memory in that process.

## Rules that matter for scoring

| Rule | Why |
|---|---|
| **Always-on instance, never "sleep" / "scale to zero"** | A sleeping free-tier service takes 30–60 s to wake. The judge times out at 30 s, and 3 failed healthz polls in a row disqualify the slot. |
| **Exactly 1 instance** | A second replica would have none of the context the judge pushed to the first one. |
| **Don't redeploy during the test window** | A restart wipes in-memory context. Redeploy before warmup, or not at all. |
| **API key only in the platform's secret settings** | Never in git, never in the Dockerfile. |

The container starts in about 1–2 s and every endpoint answers in milliseconds (plus LLM time on `/v1/tick`, capped at 9 s by the tick budget). So the only cold-start risk is the host putting the service to sleep.

## Option A — Railway (simplest from GitHub)

1. railway.app → **New Project** → **Deploy from GitHub repo** → pick your repo. Railway detects the `Dockerfile`.
2. **Variables**: add everything from `.env.example`, with your real `OPENAI_API_KEY`. Railway sets `PORT` itself.
3. **Settings** → keep **Serverless / app sleeping OFF** and **Replicas = 1**.
4. **Settings → Networking → Generate Domain**. That gives your public URL.

## Option B — Render

1. render.com → **New → Blueprint** → pick your repo. `render.yaml` is picked up.
2. Fill in the `sync: false` values it asks for (API key, team name, email).
3. Keep **plan: starter**. The free plan sleeps.

If you must use the free plan for a quick demo, an external monitor (e.g. UptimeRobot) hitting `https://<url>/v1/healthz` every 5 minutes keeps it awake. Don't rely on that for the real test.

## Option C — Fly.io

```bash
fly launch --no-deploy --copy-config        # edit `app =` in fly.toml to a unique name first
fly secrets set OPENAI_API_KEY=sk-... BOT_TEAM_NAME="..." BOT_TEAM_MEMBERS="..." BOT_CONTACT_EMAIL="..."
fly deploy
fly scale count 1
```

`fly.toml` already sets `auto_stop_machines = "off"` and `min_machines_running = 1`.

## Check the deployed bot

```bash
python scripts/smoke_test.py --url https://<your-url>                # all PASS
python scripts/load_dataset.py --url https://<your-url> --tick       # warmup like the judge
curl https://<your-url>/v1/healthz                                   # counts 5 / 50 / 200 / 100
curl -X POST https://<your-url>/v1/teardown                          # wipe before submitting
```

Run the teardown last, so the judge's warmup starts from zero, as it expects (api-call-examples 1.1).

## Local Docker (optional)

```bash
docker build -t vera-bot .
docker run --rm -p 8080:8080 --env-file .env vera-bot
```
