# Deployment

MassClaw is designed to run as a federated network of equal nodes. The
default topology for the hackathon submission is three Fly.io apps (US, EU,
AP) that auto-deploy on every push to `main`, with a Cloudflare Tunnel
fallback when the operator wants to serve from localhost instead.

Nothing in the code hardcodes hosting — every URL comes from
`IDENTITY_PUBLIC_BASE_URL`, every peer list from
`MASSCLAW_FEDERATION_PEERS`. You can run one node, three nodes, or thirty.
The same binary works on Fly, Render, Railway, Hetzner, a laptop, or a
Raspberry Pi.

## Requirements

| Service | Version | Purpose |
|---------|---------|---------|
| Python | 3.12+ | Backend runtime |
| PostgreSQL | 16+ with pgvector | Relational data + vector search |
| Redis | 7+ | Cache, pub/sub, rate limiting |
| Node.js | 18+ | Frontend (optional) |

## 1. Three-region Fly.io topology (primary)

The repo ships with three ready-to-deploy Fly configs:

- `deploy/fly/node-us.toml` — primary region `iad` (Ashburn, VA)
- `deploy/fly/node-eu.toml` — primary region `ams` (Amsterdam, NL)
- `deploy/fly/node-ap.toml` — primary region `sin` (Singapore, SG)

Each app is billed independently; the free `shared-cpu-1x@256mb` tier is
enough for hackathon traffic.

### 1.1 One-time operator setup (per region)

Run these once per region. Replace `us` with `eu` or `ap` to repeat.

```bash
# Create the Fly app.
fly apps create massclaw-us --org personal

# Attach a Postgres cluster (pgvector pre-installed on Fly's managed PG).
fly postgres create --name massclaw-us-db \
    --region iad --initial-cluster-size 1 \
    --vm-size shared-cpu-1x --volume-size 1
fly postgres attach massclaw-us-db --app massclaw-us

# Attach Redis. Upstash free tier works too; Fly's built-in is simplest.
fly redis create --name massclaw-us-redis --region iad
# The attach command prints a REDIS_URL — copy it into the secret below.

# Configure the secrets every node needs.
fly secrets set \
    JWT_SECRET_KEY="$(openssl rand -hex 32)" \
    IDENTITY_KEY_ENCRYPTION_KEY="$(openssl rand -hex 32)" \
    ANTHROPIC_API_KEY="sk-ant-..." \
    REDIS_URL="redis://..." \
    --app massclaw-us

# First deploy (subsequent deploys go through GitHub Actions).
fly deploy --config deploy/fly/node-us.toml --remote-only
```

### 1.2 Continuous deploy from GitHub

`.github/workflows/deploy-fly.yml` waits for the `Backend` CI job to succeed,
then deploys each region in parallel using per-region tokens.

Create one deploy token per app and store each under a repository secret:

```bash
fly tokens create deploy --app massclaw-us --expiry 8760h > /tmp/us.tok
# Paste contents into GitHub → Settings → Secrets → Actions → FLY_API_TOKEN_US
```

Repeat for `FLY_API_TOKEN_EU` and `FLY_API_TOKEN_AP`. The workflow
automatically skips any region whose token is not yet configured, so you
can bring regions online one at a time without failing the build.

### 1.3 Region-peer configuration

Each region's config advertises its neighbours via
`MASSCLAW_FEDERATION_PEERS`. The CRDT gossip task uses this list. Override
at deploy time if you reconfigure the topology (e.g. add a NANDA Registry
Quilt URL):

```bash
fly secrets set MASSCLAW_FEDERATION_PEERS="https://us.massclaw.example,https://eu.massclaw.example" \
    --app massclaw-ap
```

## 2. Cloudflare Tunnel fallback

Cloudflare Tunnel (`cloudflared`) is the zero-cost way to serve MassClaw
from a laptop — perfect for demo day, local-only federation tests, or
bursts when Fly's free tier hits its limit. One binary, no signup, no
credit card.

```bash
# Install.
brew install cloudflared    # macOS
# or download https://github.com/cloudflare/cloudflared/releases

# Start the full stack locally.
docker compose -f docker/docker-compose.yml up -d

# Expose port 8000 at a public URL.
cloudflared tunnel --url http://localhost:8000
# Output includes a line like:
#   https://random-words-42.trycloudflare.com
```

Hand the printed URL to judges or your stock-agent test harness. The
tunnel closes on Ctrl+C; the random subdomain is throwaway.

For a persistent tunnel with a custom domain (requires a free Cloudflare
account):

```bash
cloudflared tunnel login
cloudflared tunnel create massclaw-demo
cloudflared tunnel route dns massclaw-demo demo.massclaw.ai
# Write /etc/cloudflared/config.yml with the tunnel ID + ingress rules.
cloudflared tunnel run massclaw-demo
```

Run it under `systemd` / `launchd` if you need it up 24/7 on the operator's
machine.

## 3. Local-only federation (development + Day 11 test)

For iteration without any cloud involvement, use the docker-compose profile
at `docker/federation.yml`. Three containers act as independent MassClaw
nodes on your laptop, sharing only the code they run:

```bash
docker compose -f docker/federation.yml up -d
# Spins up node-a, node-b, node-c, each with its own Postgres + Redis.
```

This is the environment the partition-heal-converge test in
`scripts/demo_partition.sh` targets.

## 4. Single-node local development

```bash
# 1. Start infrastructure
docker compose -f docker/docker-compose.yml up -d postgres redis

# 2. Setup backend
cd backend
pip install -e ".[dev]"
alembic upgrade head

# 3. Seed sample agents
python ../scripts/seed_agents.py

# 4. Run backend
uvicorn app.main:app --reload --port 8000

# 5. (Optional) Run frontend
cd ../frontend
npm install && npm run dev
```

## 5. Environment variable reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | Yes | — | asyncpg URL to Postgres (with pgvector) |
| `DATABASE_SYNC_URL` | Yes | — | Sync URL for Alembic |
| `REDIS_URL` | Yes | `redis://localhost:6379` | Cache + gossip peer state |
| `JWT_SECRET_KEY` | Yes | — | HS256 signing key for API JWTs (generate with `openssl rand -hex 32`) |
| `IDENTITY_KEY_ENCRYPTION_KEY` | Yes (signed mode) | — | ChaCha20-Poly1305 KEK for at-rest agent keys (64-char hex) |
| `IDENTITY_INSTANCE_KEY_PATH` | No | `/var/lib/massclaw/instance.key` | Where the Ed25519 instance key is persisted |
| `IDENTITY_PUBLIC_BASE_URL` | Yes (federation) | `http://localhost:8000` | Public URL this node publishes in AgentFacts |
| `IDENTITY_INSTANCE_NAME` | No | `MassClaw` | Human-readable node name |
| `IDENTITY_TRUST_ZONE` | No | `self-issued` | Certification level declared in AgentFacts |
| `MASSCLAW_FEDERATION_PEERS` | No | — | Comma-separated peer base URLs |
| `NANDA_INDEX_ENABLED` | No | `false` | Participate in the NANDA Registry Quilt |
| `NANDA_INDEX_BASE_URL` | No | — | URL of the deployed NANDA Index (set when Quilt-accepted) |
| `NANDA_INDEX_SESSION_COOKIE` | No | — | OAuth session cookie for NANDA Index writes |
| `NANDA_INDEX_USERNAME` | No | — | Our handle on the NANDA Index |
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key for Claude |
| `OPENAI_API_KEY` | No | — | OpenAI fallback (optional) |
| `EMBEDDING_MODEL` | No | `all-MiniLM-L6-v2` | Sentence-transformer model |
| `LOG_LEVEL` | No | `INFO` | Logging level |
| `LOG_FORMAT` | No | `console` | `console` or `json` (prefer `json` in production) |
| `CORS_ORIGINS` | No | `["http://localhost:3000"]` | Allowed CORS origins |
| `AUTH_REQUIRED` | No | `true` | Authentication enforced by default; set `false` for local dev only |

## 6. Health + readiness

| Endpoint | Purpose | When to use |
|----------|---------|-------------|
| `GET /health` | Liveness — checks DB + Redis | Fly service check, K8s liveness |
| `GET /ready` | Readiness — confirms full init | Load-balancer health check, K8s readiness |
| `GET /.well-known/agent-facts.json` | NANDA discovery surface | Stock OpenClaw agent first call |

## 7. Background workers (optional but recommended)

For production-grade deployments, run Celery workers alongside the web
process. Fly supports this by adding a `worker` process group to the same
app — or you can run them as a separate `fly.toml`:

```bash
# Worker (health checks, trust decay, memory GC, gossip sync)
celery -A app.workers.celery_app worker --loglevel=info \
    -Q health,trust,memory,workflow,audit,crdt_sync

# Beat scheduler (periodic tasks)
celery -A app.workers.celery_app beat --loglevel=info
```

## 8. Smoke test after deploy

```bash
# Core health.
curl https://massclaw-us.fly.dev/health
curl https://massclaw-us.fly.dev/ready

# NANDA discovery.
curl https://massclaw-us.fly.dev/.well-known/agent-facts.json | jq .

# DID resolution.
curl "https://massclaw-us.fly.dev/api/v1/identity/resolve?did=did:key:z..."

# Content-addressed memory fetch.
curl https://massclaw-us.fly.dev/api/v1/memory/by-hash/z...

# Fact resolution.
curl -XPOST https://massclaw-us.fly.dev/api/v1/memory/facts/resolve \
    -H 'content-type: application/json' \
    -d '{"subject":"what is the deadline","mode":"audit"}'
```

All endpoints return `200` on a healthy node. If any fail,
`fly logs --app massclaw-us` shows the startup trace (including the Alembic
migration output).
