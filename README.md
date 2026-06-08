# SousChef

AI recipe-discovery assistant for home cooks who want to try something new. A cook chats, gets
**real retrieved recipes** (a list of cards → click for full text steps), can build a varied meal
plan + shopping list, and saves favorites.

This repository is built phase-by-phase with SpecKit. The current phase is the **foundation**: a
runnable, reproducible, secure skeleton — no cook-facing product logic yet. See
[CLAUDE.md](CLAUDE.md) and the design docs under [projectplanFolderForMd/](projectplanFolderForMd/)
for the full picture, and [specs/001-foundation/](specs/001-foundation/) for this phase's spec/plan.

## Quickstart

**Prerequisites:** Docker + Docker Compose, and [`uv`](https://docs.astral.sh/uv/) for local
runs/tests. A fresh clone needs **no manual file edits**.

```bash
make up
```

This single command:

- Copies `.env.example` → `.env` if you don't have one (non-secret bootstrap values only).
- Builds the backend image and starts **five services**: `backend`, `postgres` (pgvector),
  `redis`, `vault` (dev mode), and `phoenix` (tracing).
- Waits for Postgres, Redis, and Vault to report **healthy** before the backend starts.
- Runs the Alembic baseline migration (enables the `vector` extension), seeds dev Vault secrets
  automatically (no manual unseal/seed), then launches the API on **http://localhost:8000**.

| Service  | URL / port              | Purpose                          |
|----------|-------------------------|----------------------------------|
| backend  | http://localhost:8000   | FastAPI monolith                 |
| postgres | localhost:5432          | Postgres + pgvector              |
| redis    | localhost:6379          | cache / session store            |
| vault    | http://localhost:8200   | secrets (dev mode, token `root`) |
| phoenix  | http://localhost:6006   | trace UI (OpenTelemetry)         |

Tear everything down (including volumes) and come back cleanly:

```bash
make down && make up
```

### Other commands

```bash
make seed     # re-seed dev Vault secrets inside the running backend (idempotent)
make lint     # ruff + mypy
make test     # pytest
```

## Secrets

Real secrets live **only in Vault** — never in `.env`, code, or an image. `.env.example` holds
non-secret bootstrap values (Vault address + dev token, service URLs). See
[CLAUDE.md](CLAUDE.md) golden rule #4.
