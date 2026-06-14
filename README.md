# SousChef

AI recipe-discovery assistant for home cooks who want to try something new. A cook chats, gets
**real retrieved recipes** (a list of cards → click for full text steps), can build a varied meal
plan + shopping list, and saves favorites. Two properties are never compromised: **the wall** — no
recipe that violates a cook's stated allergy/diet is ever surfaced (enforced in deterministic code,
not a prompt) — and **grounding** — the app never invents recipes or steps; lists come from
retrieval and detail views render the recipe's stored steps verbatim.

## Live

| Surface | URL |
|---------|-----|
| Cook widget (the app) | **https://widget-production-5547.up.railway.app** |
| Backend API health | https://sous-chef-production-721e.up.railway.app/health |

Deployed on Railway from a green `main`; HTTPS with a valid certificate. The operator dashboard and
tracing run on separate, unadvertised operator-gated URLs (not part of the public surface).

## Documentation

Start with the design overview, then drill in. A reviewer can describe the architecture, cite a
decision with its numbers, state the eval gates + results, explain the security model, and reproduce
the stack from these alone:

- [docs/DESIGN.md](docs/DESIGN.md) — architecture, the per-turn request flow, and the Railway topology.
- [docs/DECISIONS.md](docs/DECISIONS.md) — key decisions, each backed by a number (ML-vs-LLM, chunking,
  agent-vs-workflow, tracing backend, …).
- [docs/EVALS.md](docs/EVALS.md) — the eval gates, their committed thresholds, and the latest results.
- [docs/SECURITY.md](docs/SECURITY.md) — the wall, guardrails, redaction, secrets split, public surface.
- [docs/RUNBOOK.md](docs/RUNBOOK.md) — the exact local + deploy + release procedure, and failure recovery.
- [CLAUDE.md](CLAUDE.md) and [projectplanFolderForMd/](projectplanFolderForMd/) — the golden rules,
  stack, and the original design notes.

## Run it locally

**Prerequisites:** Docker + Docker Compose, [`uv`](https://docs.astral.sh/uv/), and Node 20 (for the
widget). Provider keys (`GROQ_API_KEY`, `EMBEDDINGS_API_KEY`) in your shell for seeding. A fresh clone
needs no manual file edits.

```bash
make up           # copies .env.example → .env, builds, starts backend + postgres + redis + vault + phoenix
export GROQ_API_KEY=... EMBEDDINGS_API_KEY=...
make seed         # seed real provider keys into the local (dev) Vault
make load-seed    # load the committed seed corpus (identical to prod) — network-free, idempotent
```

Then open the widget at **http://localhost:5173** and the API at **http://localhost:8000** (`/health`
should be 200). The seed corpus makes local data identical to production, so the demo behaves the same
locally as on the live URL. Full reproduce/deploy/release path:
[docs/RUNBOOK.md](docs/RUNBOOK.md) and
[specs/007-ship-public-deploy/quickstart.md](specs/007-ship-public-deploy/quickstart.md).

| Service  | URL / port              | Purpose                          |
|----------|-------------------------|----------------------------------|
| widget   | http://localhost:5173   | cook-facing React SPA            |
| backend  | http://localhost:8000   | FastAPI monolith                 |
| dashboard| http://localhost:8501   | operator console (Streamlit)     |
| postgres | localhost:5432          | Postgres + pgvector              |
| redis    | localhost:6379          | cache (optional)                 |
| vault    | http://localhost:8200   | secrets (dev mode, token `root`) |
| phoenix  | http://localhost:6006   | trace UI (OpenTelemetry)         |

### Other commands

```bash
make lint     # ruff + mypy
make test     # pytest (unit + integration + redteam)
make evals    # run all eval gates vs eval_thresholds.yaml
make down && make up   # tear down (incl. volumes) and come back clean
```

## Secrets

Real secrets live **only in Vault** — never in `.env`, code, or an image. `.env.example` holds
non-secret bootstrap values only (Vault address + token, service URLs). Managed datastore credentials
are platform-injected. See [docs/SECURITY.md](docs/SECURITY.md) and golden rule #4 in
[CLAUDE.md](CLAUDE.md).
