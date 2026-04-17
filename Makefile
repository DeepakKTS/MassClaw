.PHONY: dev test migrate seed lint format docker-up docker-down backend frontend celery help federation-up federation-down federation-test federation-summary

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

# Federation demo (3-node CRDT convergence scenario — the GATING Phase 1 proof)
federation-up: ## Bring up the 3-node federation + seed shared workflow
	./scripts/demo_up.sh

federation-down: ## Stop + remove the federation containers + network
	./scripts/demo_down.sh

federation-test: ## Run the full partition/heal/converge scenario and assert convergence
	./scripts/demo_federation_test.sh

federation-summary: ## Show each node's Merkle root + record count
	./scripts/demo_summary.sh

# Cleanup
clean: ## Remove build artifacts and caches
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name node_modules -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .next -exec rm -rf {} + 2>/dev/null || true
