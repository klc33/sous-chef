# SousChef developer shortcuts. Foundation phase targets: up / down / seed (+ lint / test).
# `make up` is the single command that brings the whole stack to a running state.
.PHONY: up down seed ingest lint test

# Bring up the full stack. Ensures a .env exists (copied from the non-secret .env.example)
# so a fresh clone needs zero manual edits, then builds and starts all five services.
up:
	@test -f .env || cp .env.example .env
	docker compose up --build

# Tear the stack down and remove volumes (a clean slate; `make up` returns to healthy).
down:
	docker compose down -v

# Re-seed dev Vault secrets inside the running backend container (idempotent).
seed:
	docker compose exec backend sh scripts/seed_vault.sh

# Build the corpus offline: fetch sources → categorize → extract → allergens + nutrition → load.
# Idempotent and re-runnable; needs the ingestion dependency group (uv sync --group ingestion).
ingest:
	uv run python -m ingestion.run_ingest

# Lint + type-check (ruff + mypy) via uv — mirrors CI.
lint:
	uv run ruff check app alembic
	uv run mypy app

# Run the test suite via uv.
test:
	uv run pytest
