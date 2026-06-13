# Quickstart / Validation: Ship to a Public URL (v0.1.0)

Runnable acceptance for the three P1 stories plus the release. Detailed schemas live in
[data-model.md](data-model.md) and [contracts/](contracts/); this is the *verification* path.

## Prerequisites
- Docker + Docker Compose, `uv`, Node 20 (for the widget), a Railway account/project, the Railway CLI,
  and provider keys (`GROQ_API_KEY`, `EMBEDDINGS_API_KEY`) available in your shell for seeding.

---

## A. Fresh-clone reproduces locally (US3 / FR-007 / SC-003)
```bash
git clone <repo> && cd sous-chef
# one command: copies .env.example → .env, builds, starts backend+postgres+redis+vault+phoenix(+pgadmin)
make up
# seed real provider keys into the local (dev) Vault:
export GROQ_API_KEY=... EMBEDDINGS_API_KEY=... && make seed
# load the committed seed corpus (identical to prod):
uv run python scripts/load_seed_corpus.py
```
**Expect**: `GET http://localhost:8000/health` → 200; the widget at `http://localhost:5173` runs the demo
scenario end-to-end; missing secrets produce a clear seed-pointing error (FR-014), not a crash.

## B. Demo scenario (US1 / FR-016 / SC-001) — run on BOTH local and the live URL
1. Open the widget; ask for recipe ideas with an allergy/diet constraint → real retrieved cards.
2. Open a card → stored steps render **verbatim**.
3. Confirm **no** surfaced recipe violates the stated constraint (the wall).
4. Build a meal plan → request a shopping list → save a favorite → reload it.
5. Confirm the connection is HTTPS with a valid certificate (live URL).

**Expect**: 100% step completion, zero wall/grounding violations, identical behavior local vs. live (SC-006).

## C. Green-main gate (US2 / FR-002 / SC-002)
- Open a PR that deliberately fails a gate (e.g. a failing red-team probe) → required checks red →
  **cannot merge** → never deploys.
- Open a passing PR → merges to `main` → Railway auto-deploys that commit.
- Confirm branch protection lists `ruff`, `mypy`, `gates`, `evals-full`, `smoke` as required checks.

## D. Secrets posture (US4 / FR-004/005/006 / SC-004)
```bash
git grep -nE 'gsk-|sk-[A-Za-z0-9]{20}|hvs\.' -- ':!*.md' ':!specs/**'   # → zero real-key hits
```
- Confirm Railway variables hold only bootstrap/non-secret values; provider keys exist **only** in Vault.
- Remove a Vault key → backend fails fast at startup with a clear message.

## E. Deploy to Railway (RUNBOOK summary)
1. One project: add **PostgreSQL (pgvector)** + **Redis** plugins; create `backend`, `widget`,
   `dashboard`, `phoenix`, `vault` services (configs under `railway/` + root `railway.toml`).
2. Set Railway **bootstrap** variables only (see [contracts/secrets-keyspace.md](contracts/secrets-keyspace.md)).
3. Seed the **persistent** prod Vault once: `VAULT_ADDR=<prod> VAULT_TOKEN=<prod> \
   GROQ_API_KEY=... EMBEDDINGS_API_KEY=... sh scripts/seed_vault.sh`.
4. First deploy: backend runs `alembic upgrade head`; load the seed corpus
   (`scripts/load_seed_corpus.py`); Phoenix uses the `phoenix` schema on the same Postgres.
5. Confirm `/health` 200 promotes the deploy; widget public, dashboard/phoenix unadvertised.

## F. Release (US6 / FR-015 / SC-007)
- Rehearse §B on the live URL and §A on a fresh clone.
- Tag the exact live+reproducible commit:
```bash
git tag -a v0.1.0 -m "SousChef v0.1.0 — first public release"
git push origin v0.1.0
```
**Expect**: `v0.1.0` points at the commit running at the public URL and reproducible locally.
