# SousChef — Dependency Groups (uv) & Lean Docker Builds

Goal: when we build the Docker image for **one** part of the system, it downloads **only** that part's
libraries — not everything. We do this with `uv` dependency grouping in a single `pyproject.toml`, and
each Dockerfile installs just its group.

## The model
- **`[project.dependencies]`** — a tiny **shared base** every Python surface needs.
- **`[project.optional-dependencies]` (extras)** — one extra **per shippable image**: `backend`,
  `dashboard`. Each image installs only its extra.
- **`[dependency-groups]`** — **non-shipped tooling** that never goes in a serving image: `dev`, `test`,
  `evals`, `ingestion`, `ml`. These run locally or in CI/jobs, not in the backend/dashboard containers.

Why split extras vs groups: **extras** describe runtime "features of the app" you ship; **dependency
groups** are development/job-time tools that should never bloat (or widen the attack surface of) a
serving image. Keeping `torch` out of every container (a hard project rule) falls out of this naturally —
training libs live in the `ml` group, which no image installs.

---

## `pyproject.toml` (the grouped dependency section)

> Versions are resolved/pinned by `uv` into `uv.lock`. Add new libraries with the grouped `uv add`
> commands below — **never leave a dependency ungrouped.**

```toml
[project]
name = "sous-chef"
version = "0.1.0"
requires-python = ">=3.11"

# Shared base — the small set EVERY Python surface imports (config/schemas/HTTP/logging).
dependencies = [
  "pydantic",
  "pydantic-settings",
  "httpx",
  "structlog",
]

[project.optional-dependencies]
# ── BACKEND IMAGE (the FastAPI monolith) ──────────────────────────────────────
backend = [
  "fastapi",
  "uvicorn[standard]",
  "sqlalchemy",
  "alembic",
  "psycopg[binary]",          # Postgres driver
  "pgvector",                 # vector column + search
  "redis",                    # session memory
  "hvac",                     # Vault secrets adapter
  "groq",                     # LLM (generation + agent)
  "openai",                   # embeddings via a separate provider (Groq is chat-only)
  # NOTE: guardrails are deterministic regex rails in app/guardrails/ — no framework dep.
  # nemoguardrails was dropped (unused in app/ + pulled a C++ build dep that bloated the image).
  "presidio-analyzer",        # PII detection
  "presidio-anonymizer",      # PII redaction
  "scikit-learn",             # SERVE the trained classifier (no torch)
  "joblib",                   # load the model.joblib artifact
  "opentelemetry-sdk",        # tracing
  "openinference-instrumentation",
  "arize-phoenix-otel",       # exporter to the Phoenix service (NOT the Phoenix server)
  "slowapi",                  # rate limiting
]
# ── DASHBOARD IMAGE (Streamlit operator app) ──────────────────────────────────
dashboard = [
  "streamlit",
  "streamlit-authenticator",  # cookie login that survives refresh
  "httpx",                    # calls the backend's /admin API
  "pandas",                   # tabular display
]

[dependency-groups]
# These NEVER ship in a serving image — local/CI/job only.
dev       = ["ruff", "mypy"]
test      = ["pytest", "pytest-asyncio", "httpx"]
ingestion = ["httpx", "pandas", "datasets", "kaggle", "openai", "scikit-learn"]  # corpus build job
ml        = ["scikit-learn", "joblib", "pandas", "jupyterlab"]                   # offline training (NO torch — train on Colab)
evals     = ["ragas", "scikit-learn", "pandas", "pytest"]                        # eval suites / CI gates

[tool.uv]
default-groups = ["dev"]   # local `uv sync` includes dev tools; images pass --no-dev to skip them
```

---

## Adding libraries (always into a group)

```powershell
uv add --optional backend <pkg>      # → backend image only
uv add --optional dashboard <pkg>    # → dashboard image only
uv add --group ingestion <pkg>       # → ingestion job only
uv add --group ml <pkg>              # → offline training only
uv add --group evals <pkg>           # → eval suites only
uv add --group dev <pkg>             # → lint/type tooling
uv add <pkg>                         # → shared base (rare; only if EVERY surface needs it)
```
Each command updates `pyproject.toml` + `uv.lock`. **Never** `pip install`, and never add an
ungrouped dependency unless it's truly shared by every surface.

---

## Installing per surface (what each context pulls)

| Context | Command | Pulls |
|---|---|---|
| Backend image | `uv sync --frozen --no-dev --extra backend` | base + backend |
| Dashboard image | `uv sync --frozen --no-dev --extra dashboard` | base + dashboard |
| Ingestion job | `uv sync --frozen --no-dev --group ingestion` | base + ingestion |
| ML training (local) | `uv sync --frozen --group ml` | base + ml (+ dev) |
| Evals (CI) | `uv sync --frozen --no-dev --group evals` | base + evals |
| Local dev (all) | `uv sync --all-extras` | base + all extras + dev |

`--frozen` = use `uv.lock` exactly (reproducible); `--no-dev` = skip the default `dev` group; naming one
`--extra` pulls **only** that extra (not the other one). That's what makes each image minimal.

---

## Using dependencies day-to-day

You almost never install "the whole project." A new library goes into the group of whoever uses it **at
runtime**, then you run things with `uv run`.

**Where does a new dependency go?**
| The library is used by… | Add it with | Lands in |
|---|---|---|
| the FastAPI backend (ships in backend image) | `uv add --optional backend X` | `backend` extra |
| the Streamlit dashboard | `uv add --optional dashboard X` | `dashboard` extra |
| the corpus-build job only | `uv add --group ingestion X` | `ingestion` group |
| offline classifier training only | `uv add --group ml X` | `ml` group |
| eval suites / CI gates only | `uv add --group evals X` | `evals` group |
| lint / type / test tooling | `uv add --group dev X` / `--group test X` | `dev` / `test` group |
| literally every surface (rare) | `uv add X` | shared base |

**Per context — what you actually type:**
- **Working on the backend:**
  ```powershell
  uv add --optional backend <pkg>          # add a backend lib (updates pyproject + uv.lock)
  uv run uvicorn app.main:app --reload     # run it (uses .venv, no manual activation)
  uv run pytest                            # tests
  ```
- **Working across surfaces** (install everything once): `uv sync --all-extras`, then `uv run …`.
- **Building an image** — you don't think about deps; the Dockerfile pins the group:
  ```powershell
  docker build -t souschef-backend .                         # → uv sync --extra backend (backend libs only)
  docker build -f dashboard/Dockerfile -t souschef-dash .    # → uv sync --extra dashboard (dashboard libs only)
  ```
- **Running a job locally:** `uv run python -m ingestion.run_ingest` (sync `--group ingestion` first if needed).
- **CI eval job:** `uv sync --frozen --no-dev --group evals` then `uv run python evals/run_evals.py`.
- **Fresh clone / teammate / Railway:** `uv sync` (or `--extra backend`) installs exactly what's in
  `uv.lock` — reproducible.

**Payoff:** the backend image never downloads streamlit/ragas/jupyter, the dashboard never downloads the
backend stack, and **no image ever gets torch** — and every install is pinned by `uv.lock`.

---

## Dockerfiles — each installs only its group

### `Dockerfile` (repo root → the **backend** image)
```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /srv

# 1) Dependency layer (cached). ONLY the backend extra, no dev/test/ml/evals.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev --extra backend

# 2) App layer
COPY app ./app
COPY prompts ./prompts
COPY alembic ./alembic
COPY alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra backend

ENV PATH="/srv/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### `dashboard/Dockerfile` (the **dashboard** image)
```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /srv

# Build from the repo root so pyproject.toml + uv.lock are in context:
#   docker build -f dashboard/Dockerfile -t souschef-dashboard .
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev --extra dashboard

COPY dashboard ./dashboard
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra dashboard

ENV PATH="/srv/.venv/bin:$PATH"
EXPOSE 8501
CMD ["streamlit", "run", "dashboard/app.py", "--server.port=8501", "--server.address=0.0.0.0"]
```

The **widget** image is Node/Vite, not Python — it has its own `widget/Dockerfile` and `package.json`,
so it never touches these Python groups.

### Result
- Backend image ships `fastapi … scikit-learn/joblib … otel` and **not** `streamlit`, `ragas`,
  `jupyterlab`, `ruff`, `pytest`, `kaggle`, etc.
- Dashboard image ships `streamlit + httpx + pandas` and **not** the whole backend stack.
- No image ever contains `torch` or the training/eval tooling. Images stay small and build fast.

---

## Phoenix & Vault are services, not Python deps
Phoenix and Vault run as their **own containers** (official `arizephoenix/phoenix` and `hashicorp/vault`
images) via `docker-compose` / Railway — the backend only carries the lightweight **otel exporter**
(`arize-phoenix-otel`) to send spans to them. So tracing/secrets add almost nothing to the backend image.
