.PHONY: help dev backend frontend celery-worker celery-beat \
	migrate migrate-create migrate-rollback seed \
	test test-db-setup test-unit test-integration test-e2e test-cov \
	lint format typecheck ci-local clean \
	docker-up docker-down docker-logs \
	federation-up federation-down federation-test federation-summary \
	fed-native-up fed-native-down fed-native-purge \
	harness-up harness-down harness-status

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# Docker
docker-up: ## Start all infrastructure services
	docker compose -f docker/docker-compose.yml up -d

docker-down: ## Stop all infrastructure services
	docker compose -f docker/docker-compose.yml down

docker-logs: ## Tail docker logs
	docker compose -f docker/docker-compose.yml logs -f

# Backend
backend: ## Run backend dev server
	cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

celery-worker: ## Run Celery worker
	cd backend && celery -A app.workers.celery_app worker -l info -Q health,trust,memory,workflow,audit

celery-beat: ## Run Celery beat scheduler
	cd backend && celery -A app.workers.celery_app beat -l info

# Frontend
frontend: ## Run frontend dev server
	cd frontend && npm run dev

# Database
migrate: ## Run database migrations
	cd backend && alembic upgrade head

migrate-create: ## Create new migration (usage: make migrate-create msg="description")
	cd backend && alembic revision --autogenerate -m "$(msg)"

migrate-rollback: ## Rollback last migration
	cd backend && alembic downgrade -1

# Development
dev: docker-up ## Start full development stack
	@echo "Infrastructure started. Run 'make backend' and 'make frontend' in separate terminals."

seed: ## Seed database with development data
	cd backend && python -m scripts.seed_agents

# Testing
#
# The suite runs against a dedicated `<db>_test` database and Redis db 9 —
# tests/conftest.py rewrites the URLs at import time and refuses to start if
# they do not point somewhere disposable. Run test-db-setup once per machine.
test-db-setup: ## Create the test database and its extensions (needs superuser once)
	@set -e; \
	db="$${TEST_DB:-massclaw_test}"; \
	echo "==> creating database $$db (owner: massclaw)"; \
	PGPASSWORD=massclaw psql -h localhost -U massclaw -d massclaw \
		-tAc "SELECT 1 FROM pg_database WHERE datname='$$db'" | grep -q 1 \
		&& echo "    already exists" \
		|| PGPASSWORD=massclaw createdb -h localhost -U massclaw "$$db"; \
	echo "==> installing extensions"; \
	echo "    pg_trgm is a trusted extension, so the massclaw role can add it;"; \
	echo "    vector is not, so this step connects as your local superuser."; \
	PGPASSWORD=massclaw psql -h localhost -U massclaw -d "$$db" \
		-c "CREATE EXTENSION IF NOT EXISTS pg_trgm;" >/dev/null; \
	psql -d "$$db" -c "CREATE EXTENSION IF NOT EXISTS vector;" >/dev/null; \
	echo "==> extensions present:"; \
	psql -d "$$db" -tAc "SELECT '    ' || extname FROM pg_extension ORDER BY 1"; \
	echo "Test database ready. Tables are created by conftest on first run."

test: ## Run all backend tests
	cd backend && pytest -v --tb=short

test-unit: ## Run unit tests only
	cd backend && pytest tests/unit -v --tb=short

test-integration: ## Run integration tests only
	cd backend && pytest tests/integration -v --tb=short

test-e2e: ## Run end-to-end tests
	cd backend && pytest tests/e2e -v --tb=short

test-cov: ## Run tests with coverage report
	cd backend && pytest --cov=app --cov-report=html --cov-report=term-missing

# Linting & Formatting
lint: ## Run linter
	cd backend && ruff check .

format: ## Format code
	cd backend && ruff format .

typecheck: ## Run type checker
	cd backend && mypy app/

# Local CI mirror — a SUPERSET of .github/workflows/ci.yml.
#
# Every check CI runs is run here, in the same order, so a green ci-local
# means CI has nothing new to tell you. Two checks are stricter than CI on
# purpose and are labelled [extra]: the frontend typecheck (CI has no tsc
# step) and mypy being visible rather than swallowed.
#
# This used to claim "EXACT same checks" while both omitting the frontend
# build that CI runs and adding a tsc step that CI does not — so it could
# pass while CI failed, and vice versa. Keep the two in sync when either
# changes.
#
# Note: CI installs with `-c backend/constraints.txt`. If a version-specific
# failure reproduces in CI but not here, compare against that file first.
ci-local: ## Run every CI check locally, plus a frontend typecheck
	@echo "=== Backend: ruff check ===" && cd backend && ruff check app/
	@echo "=== Backend: ruff format --check ===" && cd backend && ruff format --check app/
	@echo "=== Backend: mypy [extra: advisory in CI] ===" && cd backend && mypy app/ || true
	@echo "=== Backend: pytest (unit + integration) ===" && cd backend && pytest tests/unit/ tests/integration/ -q --tb=short
	@echo "=== Frontend: tsc [extra: not in CI] ===" && cd frontend && npx tsc --noEmit
	@echo "=== Frontend: lint ===" && cd frontend && npm run lint
	@echo "=== Frontend: build ===" && cd frontend && npm run build
	@echo "All CI checks passed locally. Safe to push."

# Federation demo (3-node CRDT convergence scenario — the GATING Phase 1 proof)
federation-up: ## Bring up the 3-node federation + seed shared workflow
	./scripts/demo_up.sh

federation-down: ## Stop + remove the federation containers + network
	./scripts/demo_down.sh

federation-test: ## Run the full partition/heal/converge scenario and assert convergence
	./scripts/demo_federation_test.sh

federation-summary: ## Show each node's Merkle root + record count
	./scripts/demo_summary.sh

# Native 3-node federation (no Docker — uses Homebrew Postgres 17 + Redis)
fed-native-up: ## Bring up 3-node federation as native processes (no Docker)
	./scripts/dev_federation_up.sh

fed-native-down: ## Stop native federation
	./scripts/dev_federation_down.sh

fed-native-purge: ## Stop native federation AND drop databases
	./scripts/dev_federation_down.sh --purge

# OpenClaw hardening harness (local, zero-cost)
harness-up: ## Start harness server on :19000
	$(MAKE) -C testing/openclaw_harness harness-up

harness-down: ## Stop harness server
	$(MAKE) -C testing/openclaw_harness harness-down

harness-status: ## Show harness status
	$(MAKE) -C testing/openclaw_harness harness-status

# Cleanup
clean: ## Remove build artifacts and caches
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name node_modules -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .next -exec rm -rf {} + 2>/dev/null || true
