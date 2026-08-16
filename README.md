# LinkPlease

Automates DMs for Instagram comment keywords, built against the PseudoGram
mock API for the LinkPlease intern take-home.

## How it works

- `POST /webhook` verifies the HMAC signature, matches the comment text
  against stored rules (case-insensitive substring), and atomically claims
  `(user_id, rule_id)` via a `UNIQUE` constraint in SQLite — this is what
  makes redelivered/duplicate events safe under real concurrency, not an
  app-level check.
- A background worker polls `pending` rows, respects the 10-req/60s rate
  limit, and retries `500`s with backoff (capped at 5 attempts) while never
  retrying `400`s.
- A background reconciler polls `in_flight` rows older than 30s via
  `GET /v1/dm/{dm_id}`, since an accepted (`202`) DM can still resolve to
  `failed` later.
- `GET /stats` is a live query over persisted state — no in-memory counters
  that could drift or reset.

See `FAILURES.md` for known gaps, found through actual testing.

## Run locally

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

export PSEUDOGRAM_API_KEY=your_key_here
export PSEUDOGRAM_BASE_URL=https://pseudogram-api.onrender.com

uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Run tests

```bash
python3 -m pytest tests/ -v
```

Covers: the dedup race under real `asyncio` concurrency, signature
verification, `comment.deleted` cancellation, worker retry/backoff behavior
against `429`/`500`/`400`, reconciler promotion of stale `in_flight` rows,
and a 500-event load scenario with 200 redelivered duplicates shuffled out
of order.

## Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `PSEUDOGRAM_API_KEY` | yes (prod) | `""` | Sent as `X-API-Key`; also the HMAC secret for verifying inbound webhook signatures |
| `PSEUDOGRAM_BASE_URL` | no | `https://pseudogram-api.onrender.com` | Mock API base URL |
| `DB_PATH` | no | `linkplease.db` in repo root | SQLite file location |
| `VERIFY_SIGNATURES` | no | `true` | Set `false` only for local testing without a real key |

## Deploying (Render, matches the assignment's own stack)

1. Push this repo to GitHub.
2. On Render: New → Web Service → connect the repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
5. Add environment variables `PSEUDOGRAM_API_KEY` and `PSEUDOGRAM_BASE_URL`
   in the Render dashboard.
6. **Important:** Render's free tier uses an ephemeral filesystem — the
   SQLite file is wiped on every deploy/restart. Add a persistent disk
   (Render → Disks) mounted at `/data`, and set `DB_PATH=/data/linkplease.db`,
   or the "survive a restart without losing state" requirement silently
   fails in production even though it's correct in code.
7. Confirm the deployed `/webhook` URL is reachable, then use
   `POST /v1/simulate/start` against it to run the assignment's own load
   test before submitting.

## Project layout

```
app/
  main.py              # FastAPI app, lifespan starts worker + reconciler tasks
  db.py                # SQLite schema, connection, all reads/writes
  schemas.py           # pydantic models
  signature.py         # HMAC-SHA256 webhook verification
  pseudogram_client.py # wrapper over /v1/dm/send and /v1/dm/{id}
  worker.py            # send loop: rate limiting, retry/backoff
  reconciler.py        # catches DMs that failed after being accepted
  routes/
    rules.py
    webhook.py
    stats.py
tests/
  test_core.py          # dedup race, signatures, comment.deleted, worker, reconciler
  test_load.py           # 500-event scenario with shuffled duplicates
FAILURES.md
