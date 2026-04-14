# Changelog

All notable changes to MassClaw are documented here.

## [1.0.0] - 2026-04-14

### Added
- **Registry Layer**: Agent registration with capability declarations, dynamic discovery with trust/cost/capability filtering, and capability-based search API.
- **Trust Layer**: Bayesian trust scoring with temporal decay, multi-dimensional trust breakdown (reliability, accuracy, speed, cost-efficiency), and automatic trust updates from workflow outcomes.
- **Memory Layer**: Shared vector memory backed by pgvector (384-dim embeddings via all-MiniLM-L6-v2), semantic search across agent workflows, and background garbage collection for stale entries.
- **Orchestration Layer**: Plain-English task submission, automatic task decomposition via LLM, intelligent agent selection based on capability and trust, and parallel sub-task execution with result aggregation.
- **Wallet Layer**: Per-workflow budget tracking with reserve/charge/release economics, cost guardrails preventing overspend, and detailed cost breakdowns per agent and sub-task.
- **Safety Layer**: 3-layer content filtering (input validation, policy engine, output screening), prompt injection detection, and configurable policy rules with evaluate API.
- **Evolution Layer**: Performance-based agent ranking, automatic promotion and demotion based on historical outcomes, and fitness scoring across reliability, accuracy, speed, and cost.
- **Audit Layer**: Complete decision trail for every action, per-workflow audit history, and searchable audit log API.
- **LLM Abstraction**: Anthropic Claude as primary provider, OpenAI as fallback, and mock provider for testing.
- **API**: Full REST API with interactive Swagger and ReDoc documentation, health and readiness endpoints, and capability discovery endpoint.
- **Frontend**: Next.js 14 Command Center dashboard with agent registry view, workflow monitoring, trust visualization, and memory explorer.
- **Background Workers**: Celery-based health monitoring, trust decay, memory garbage collection, and workflow processing.
- **OpenClaw Integration**: External agent registration and task delegation protocol documented in `docs/openclaw_integration.md`.

### Security
- 3-layer content filtering pipeline on all inputs and outputs.
- Prompt injection detection with pattern matching and LLM-based analysis.
- Policy engine with configurable allow/deny rules evaluated at request time.
- Input validation via Pydantic v2 strict schemas on every endpoint.
- Environment-based secrets management (API keys never hardcoded).
- Rate limiting via Redis to prevent abuse.

### Infrastructure
- Docker Compose setup for PostgreSQL 16 (with pgvector), Redis 7, backend, frontend, and Celery workers.
- Alembic migrations for reproducible database schema management.
- Makefile with targets for dev, test, lint, format, typecheck, migrate, seed, and Docker lifecycle.
- Multi-stage Docker builds for production-optimized images.
- Health (`/health`) and readiness (`/ready`) endpoints for container orchestration.
- Seed scripts for bootstrapping development data.
