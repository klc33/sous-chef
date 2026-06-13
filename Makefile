# SousChef developer shortcuts. Foundation phase targets: up / down / seed (+ lint / test).
# `make up` is the single command that brings the whole stack to a running state.
.PHONY: up down seed load-seed ingest train lint test evals pgadmin

# Bring up the full stack. Ensures a .env exists (copied from the non-secret .env.example)
# so a fresh clone needs zero manual edits, then builds and starts all services. The `local`
# profile is activated so the local-only pgAdmin UI (005/US2) comes up alongside the stack; a
# bare `docker compose up` (no profile) deliberately omits it — the local-only signal.
up:
	@test -f .env || cp .env.example .env
	docker compose --profile local up --build

# Print the local pgAdmin URL (brought up by `make up`). The `souschef` Postgres server is
# pre-provisioned (docker/pgadmin/servers.json), so it's connectable on first boot. Local-only.
pgadmin:
	@echo "pgAdmin → http://localhost:5050  (login with PGADMIN_DEFAULT_EMAIL / PGADMIN_DEFAULT_PASSWORD from .env)"

# Tear the stack down and remove volumes (a clean slate; `make up` returns to healthy).
down:
	docker compose down -v

# Re-seed dev Vault secrets inside the running backend container (idempotent).
# Pipes the ON-DISK script via stdin (sh -s) so edits take effect without rebuilding the image,
# and forwards GROQ_API_KEY / EMBEDDINGS_API_KEY / OPENAI_API_KEY from the operator's env into the
# container so real keys reach Vault (golden rule #4). OPENAI_API_KEY is the chat key for the
# provider-agnostic seam (005), forwarded the same way so `LLM_PROVIDER=openai` works after a re-seed.
# Unset keys fall back to the script's dev placeholders.
seed:
	docker compose exec -T -e GROQ_API_KEY -e EMBEDDINGS_API_KEY -e OPENAI_API_KEY backend sh -s < scripts/seed_vault.sh

# Load the committed seed corpus into Postgres inside the running backend container — the local data
# path (mirrors deploy + CI). Network-free + idempotent on (source, source_id): it upserts the
# pre-embedded rows from seeds/corpus/ and makes ZERO provider calls, so `make up` → `make seed` →
# `make load-seed` brings a fresh clone to real retrieval results without running `make ingest`. The
# corpus ships in the backend image (Dockerfile COPY seeds), so this runs entirely from baked-in files.
load-seed:
	docker compose exec -T backend python -m scripts.load_seed_corpus

# Build the corpus offline: fetch sources → categorize → extract → allergens + nutrition → load.
# Idempotent and re-runnable; needs the ingestion dependency group (uv sync --group ingestion).
# This is the OPERATOR pipeline that BUILDS the seed (then `export_seed_corpus.py` freezes it); the
# day-to-day local/CI/prod path is `make load-seed`, which never hits the network.
ingest:
	uv run python -m ingestion.run_ingest

# Train the intent classifier offline (TF-IDF + logistic regression → ml/artifacts/model.joblib).
# Needs the ml dependency group (uv sync --group ml). Prints the held-out macro-F1 to set the gate.
train:
	uv run python -m ml.train_classifier

# Lint + type-check (ruff + mypy) via uv — mirrors CI.
lint:
	uv run ruff check app alembic
	uv run mypy app

# Run the test suite via uv.
test:
	uv run pytest

# Run every eval gate vs eval_thresholds.yaml ("evals are the grade", golden rule #6).
# Deterministic gates (classifier macro-F1, red-team refusal, redaction leaks) always run; the offline
# gates (RAG hit@3, agent tool-selection) need `make up` + `make ingest` and SKIP cleanly otherwise.
# Exit is non-zero iff a gate that actually ran scored below its threshold.
evals:
	uv run python -m evals.run_evals
