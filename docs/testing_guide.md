# Testing MassClaw — End-to-End Guide

Everything below assumes you just pulled `main`. Runs green on macOS; same on Linux. Docker is optional except for the federation demos.

---

## 1. One-command sanity check

```bash
cd backend
python -m pytest tests/unit tests/integration --ignore=tests/unit/test_scheduler_cache.py -q
```

Expect **615 passed** in ~12 s.

> `test_scheduler_cache.py` has a known event-loop quirk with asyncpg on local Postgres, unrelated to any MassClaw code. It passes in CI where Postgres runs inside Docker.

### Production build

```bash
cd frontend
npm install
npm run build
```

Next.js static-generates every page; a clean build is your green light that every route compiles.

---

## 2. Start the stack locally

**Requirements**: Python 3.12+, Node 20+, a local Postgres (`localhost:5432`, user `massclaw`, password `massclaw`, db `massclaw`), and Redis 7.

```bash
# Terminal 1 — DB migrations + backend
cd backend
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

```bash
# Terminal 2 — frontend dev server
cd frontend
npm run dev -- --port 3000
```

```bash
# Terminal 3 — Celery beat + worker for gossip / GC / trust-decay
cd backend
celery -A app.celery_app worker --loglevel=INFO &
celery -A app.celery_app beat   --loglevel=INFO &
```

---

## 3. Smoke tests — copy-paste in a terminal

```bash
BASE=http://localhost:8000

# 3a. Health
curl -s $BASE/health | jq

# 3b. Discover MassClaw via the NANDA well-known surface
curl -s $BASE/.well-known/agent-facts.json | jq '.label, .skills[].id'

# 3c. List the ten built-in policy rules
curl -s $BASE/api/v1/policy/registry/rules | jq

# 3d. Dry-run a low-trust payment (expect deny)
curl -s -X POST $BASE/api/v1/policy/registry/evaluate \
  -H 'Content-Type: application/json' \
  -d '{"action_category":"payment","amount":100,"agent_trust_score":0.2}' | jq

# 3e. Submit a workflow
WF=$(curl -s -X POST $BASE/api/v1/workflows/submit \
  -H 'Content-Type: application/json' \
  -d '{"instruction":"Summarize top three supply chain bottlenecks","budget":25}' | jq -r .workflow_id)
echo "workflow: $WF"

# 3f. Watch progress (SSE)
curl -N $BASE/api/v1/workflows/$WF/stream | head -n 20

# 3g. Get the final result once status=completed
curl -s $BASE/api/v1/workflows/$WF/status   | jq '.status'
curl -s $BASE/api/v1/workflows/$WF/result   | jq

# 3h. Pull signed policy-decision audit records
curl -s "$BASE/api/v1/audit/policy-decisions?limit=10" | jq '.[].decision.action'

# 3i. Error envelope shape (malformed JSON → 422)
curl -s -X POST $BASE/api/v1/policy/registry/evaluate \
  -H 'Content-Type: application/json' -d '{bad' | jq
```

Every error response looks like:

```json
{
  "error_code": "VALIDATION_ERROR",
  "detail": "Request validation failed",
  "next_steps": "Inspect the `errors` field to fix the malformed input, then retry.",
  "errors": [...]
}
```

---

## 4. Frontend — click-through test script

Open http://localhost:3000 and verify each route:

| # | Route | What to check |
|---|---|---|
| 1 | `/` | Dashboard stats (agents, workflows, memory, trust) — no `—` placeholders once backend is up |
| 2 | `/agents` | Registry list renders. Empty state is friendly if registry is empty; error banner if backend is down. |
| 3 | `/agents/[id]` | AgentFacts panel renders with signature-verify button (green check) |
| 4 | `/missions/new` | Submit a task — redirects to `/missions/[id]` with live SSE updates |
| 5 | `/missions/[id]` | Reflection verdict badges appear on each completed task |
| 6 | `/approvals` | If you submit a high-risk capability task, a row appears here with **checkpoint hash** visible; approve/deny reflects instantly |
| 7 | `/policy` | 10 rule rows. Toggle a rule → row colour flips. Paste a JSON PolicyContext in the tester → decision badge appears |
| 8 | `/audit` → **Policy Decisions** tab | Click a row → full decision + context JSON expands; signing DID shown |
| 9 | `/memory` | Type a semantic query → ranked matches appear |
| 10 | `/trust`, `/wallet`, `/evolution`, `/tools`, `/mcp` | Each renders with real data, no blank screens |

### Frontend ↔ backend round-trip probe

1. Open DevTools → Network tab.
2. Reload `/policy`.
3. You should see `GET /api/v1/policy/registry/rules` → 200 OK, JSON array of 10.
4. Click any toggle → `POST /api/v1/policy/registry/rules/{id}/disable` → 200, `{ enabled: false }`.

Any 4xx/5xx here → something broke. Response body shows the structured envelope.

---

## 5. Federation demo (Docker required)

```bash
./scripts/demo_up.sh                                # 12 containers
./scripts/demo_summary.sh                           # three Merkle roots — should all agree after ~10 s
./scripts/demo_write.sh node-a "deadline: Apr 30"   # signed write on node-a
./scripts/demo_partition.sh node-c                  # cut node-c off the gossip network
./scripts/demo_write.sh node-b "deadline: May 15"   # offline write reaches a, not c
./scripts/demo_write.sh node-c "deadline: Apr 28"   # offline write stays on c
./scripts/demo_heal.sh node-c                       # re-attach node-c
./scripts/demo_summary.sh --watch 2                 # watch roots converge
./scripts/demo_query.sh node-a "what is the deadline" --mode audit   # three records, all signatures valid
./scripts/demo_down.sh
```

**Full end-to-end assertion** (single command):

```bash
./scripts/demo_federation_test.sh
# exit 0 = every scenario passed, including convergence within 60 s
```

---

## 6. Chaos tests

```bash
./scripts/chaos_partition_mid_write.sh  # partition in the middle of writes → converge after heal
./scripts/chaos_kill_mid_write.sh       # docker kill node-a just after a write → peers have it
./scripts/chaos_malformed_records.sh    # hammers bad input into every API class → every response is a valid envelope
```

---

## 7. When something fails

| Symptom | Likely cause | Fix |
|---|---|---|
| Backend 503 on first request | Redis not running | `redis-server &` or `brew services start redis` |
| `alembic upgrade head` errors | Postgres not reachable | Check `DATABASE_URL`; default is `postgresql+asyncpg://massclaw:massclaw@localhost:5432/massclaw` |
| Frontend "Failed to load" | CORS or backend port mismatch | Ensure backend on :8000; `CORS_ORIGINS` setting includes `http://localhost:3000` |
| Tests: "column author_did does not exist" | Local DB behind migrations | `alembic upgrade head` again |
| Tests: "Event loop is closed" on `test_scheduler_cache` | Local-only asyncpg quirk | Expected; use `--ignore=tests/unit/test_scheduler_cache.py` |
| Federation nodes never converge | Gossip worker not running | `docker compose -f docker/federation.yml logs worker-a` — look for "gossip_tick" lines |
| Stock agent probe: 413 on payload | Body-size cap | Default is 1 MiB; raise via `MASSCLAW_MAX_REQUEST_BODY_BYTES` env var |

---

## 8. Continuous testing loop

```bash
# Watch the backend suite as you edit
cd backend
python -m pytest tests/unit tests/integration \
  --ignore=tests/unit/test_scheduler_cache.py \
  -q --tb=short -x
```

The suite finishes in ~12 s, so you can leave it running in a terminal split and edit with confidence.
