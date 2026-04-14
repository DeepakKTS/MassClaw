# Contributing to MassClaw

## Development Setup

1. Clone the repository and start infrastructure:

```bash
git clone https://github.com/DeepakKTS/MassClaw.git
cd MassClaw
make docker-up
```

2. Set up the backend:

```bash
cd backend
pip install -e ".[dev]"
cp ../.env.example .env  # then set your ANTHROPIC_API_KEY
make migrate
make seed
```

3. Set up the frontend:

```bash
cd frontend
npm install
```

4. Run the development stack:

```bash
make dev        # Start Docker services
make backend    # In one terminal
make frontend   # In another terminal
```

See `make help` for all available targets.

## Code Style

- **Linting**: [Ruff](https://docs.astral.sh/ruff/) for fast Python linting and import sorting.
  ```bash
  make lint       # Check for issues
  make format     # Auto-format code
  ```
- **Type Checking**: [mypy](https://mypy-lang.org/) with strict mode for the `app/` package.
  ```bash
  make typecheck
  ```
- All code must pass both `ruff check` and `mypy` with zero errors before merge.

## Testing

We use [pytest](https://docs.pytest.org/) with three test categories:

| Category | Command | What it covers |
|---|---|---|
| Unit | `make test-unit` | Individual functions and classes, mocked dependencies |
| Integration | `make test-integration` | Database queries, Redis operations, API endpoint contracts |
| End-to-end | `make test-e2e` | Full workflow execution through the API |

Run all tests:

```bash
make test
```

Run with coverage:

```bash
make test-cov
```

New features must include tests. Bug fixes must include a regression test.

## Pull Request Process

1. **Fork** the repository and create a feature branch from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```
2. **Make your changes** — keep commits focused and atomic.
3. **Run the full check suite** before pushing:
   ```bash
   make lint
   make typecheck
   make test
   ```
4. **Push** your branch and open a pull request against `main`.
5. Fill out the PR template. Link any related issues.
6. Address review feedback. All CI checks must pass before merge.

## Issue Guidelines

- **Bug reports**: Use the bug report template. Include steps to reproduce, expected behavior, and actual behavior. Attach logs or screenshots if relevant.
- **Feature requests**: Use the feature request template. Describe the problem you are solving, your proposed solution, and any alternatives you considered.
- **Questions**: Use GitHub Discussions, not issues.

Before opening a new issue, search existing issues to avoid duplicates.
