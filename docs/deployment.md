# Deployment Guide

## Requirements

| Service | Version | Purpose |
|---------|---------|---------|
| Python | 3.12+ | Backend runtime |
| PostgreSQL | 16+ with pgvector | Relational data + vector search |
| Redis | 7+ | Cache, pub/sub, rate limiting |
| Node.js | 18+ | Frontend (optional) |

## Environment Variables

Copy `.env.example` to `backend/.env` and configure:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | Yes | — | PostgreSQL async connection URL |
| `REDIS_URL` | Yes | `redis://localhost:6379` | Redis connection URL |
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key for Claude |
| `JWT_SECRET_KEY` | Yes | — | Secret for JWT signing (generate with `openssl rand -hex 32`) |
| `JWT_ALGORITHM` | No | `HS256` | JWT algorithm |
| `JWT_EXPIRY_MINUTES` | No | `60` | Token expiry |
| `OPENAI_API_KEY` | No | — | OpenAI fallback (optional) |
| `EMBEDDING_MODEL` | No | `all-MiniLM-L6-v2` | Sentence-transformer model |
| `LOG_LEVEL` | No | `INFO` | Logging level |
| `CORS_ORIGINS` | No | `["http://localhost:3000"]` | Allowed CORS origins |
| `RATE_LIMIT_REQUESTS` | No | `100` | Requests per window |
| `RATE_LIMIT_WINDOW_SECONDS` | No | `60` | Rate limit window |
| `AUTH_REQUIRED` | No | `false` | Enforce authentication on write endpoints |

## Local Development

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

## Docker Deployment

```bash
# Full stack
docker compose -f docker/docker-compose.yml up -d

# Backend only
docker build -f backend/Dockerfile -t massclaw-backend backend/
docker run -p 8000:8000 --env-file backend/.env massclaw-backend
```

## Health & Readiness

| Endpoint | Purpose | When to use |
|----------|---------|-------------|
| `GET /health` | Liveness — checks DB and Redis connectivity | K8s liveness probe |
| `GET /ready` | Readiness — confirms app is fully initialized | K8s readiness probe, load balancer |

## NEST Compatibility

MassClaw is designed for NEST-like deployment:

- **Stateless backend**: All state in PostgreSQL/Redis, no local filesystem
- **Environment-driven config**: Every setting via env vars
- **Standard ports**: HTTP on 8000, no custom protocols
- **Container-ready**: Dockerfile with health check built in
- **Clean service boundaries**: Backend, database, cache are separate services

### NEST deployment steps:
1. Deploy PostgreSQL with pgvector extension
2. Deploy Redis
3. Set environment variables
4. Deploy backend container
5. Run `alembic upgrade head` on first deploy
6. Run `python scripts/seed_agents.py` for demo data

### Startup consideration
The embedding model (`all-MiniLM-L6-v2`, ~90MB) downloads on first startup. For faster cold starts, pre-download into the Docker image or mount a volume with the cached model at `~/.cache/torch/sentence_transformers/`.

## Background Workers (Optional)

For production, run Celery workers for background tasks:

```bash
# Worker (health checks, trust decay, memory GC)
celery -A app.workers.celery_app worker --loglevel=info -Q health,trust,memory,workflow,audit

# Beat scheduler (periodic tasks)
celery -A app.workers.celery_app beat --loglevel=info
```

These are optional for demo/hackathon but recommended for production.
