# SousChef — Project Initialization & Workflow

How to start the project in the `sous-chef` repo and proceed from there. Every step has the **command(s)**
and a **why**. Commands are written for **Windows PowerShell** (the cross-platform ones — git, docker,
npm — work anywhere).

Companion docs: skeleton script → [structure_build_commands.md](structure_build_commands.md) · phase
plan + paste-ready SpecKit blocks → [sous-chef-spec-plan.md](sous-chef-spec-plan.md) · agent rules →
[CLAUDE.md](CLAUDE.md).

---

## Prerequisites (install once)

```powershell
# Git
winget install Git.Git

# Python 3.11+
winget install Python.Python.3.12

# Node.js 18+ (for the React widget)
winget install OpenJS.NodeJS.LTS

# Docker Desktop (runs Postgres/pgvector, Redis, Vault, Phoenix locally)
winget install Docker.DockerDesktop

# uv — this project's Python package manager AND the SpecKit CLI runner
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```
**Why:** Git versions the repo; Python runs the FastAPI monolith, ingestion, ML, and evals; Node builds
the widget; Docker Desktop provides the four backing services so you don't install them by hand. **`uv`
is this project's only Python package manager — we never use `pip`** — and it also runs the SpecKit CLI.
Restart the terminal after installing so the new commands are on your `PATH`. You'll also work inside an
**AI coding agent that supports SpecKit slash commands** (e.g. Claude Code) — that's where `/speckit.*`
commands run.

---

## Step 1 — Scaffold the repo skeleton

```powershell
# From the folder that should CONTAIN sous-chef/, run the build script
# (paste the script from structure_build_commands.md, or save it as build.ps1):
./build.ps1
```
**Why:** This creates `sous-chef/` with the full monolith layout (`app/`, `ingestion/`, `evals/`,
`widget/`, `dashboard/`, `tests/`, root config files) as empty placeholders. It deliberately does **not**
create `.specify/` or `specs/` — SpecKit owns those (next steps). You're scaffolding the structure
SpecKit will implement *into*.

---

## Step 2 — Enter the repo & bring in the project docs

```powershell
Set-Location sous-chef

# Copy the curated agent rules to the repo root (Claude Code loads CLAUDE.md automatically)
Copy-Item ..\CLAUDE.md .\CLAUDE.md

# Keep the planning/reference docs in the repo so the agent + SpecKit can read them
New-Item -ItemType Directory -Force -Path .\docs\reference | Out-Null
Copy-Item ..\sous-chef-spec-plan.md, ..\dependencies.md, ..\structure.md, `
          ..\model_role.md, ..\how_to_use_embeeding.md .\docs\reference\
```
**Why:** Every later command assumes you're at the repo root. **Use our hand-written
[CLAUDE.md](CLAUDE.md) — do NOT run `/init` to create one.** `/init` scans the repo and auto-generates a
generic CLAUDE.md; at project start the repo is empty placeholders, so it would produce a thin file and
risk diluting the curated golden rules. Copy our `CLAUDE.md` to the root instead. The reference docs go
under `docs/reference/` so the agent (and you) can consult the spec-plan, dependency grouping, structure,
and embeddings guide. *(Later, once real code exists, you may run `/init` to refresh — but review the
diff and keep the golden rules.)* Adjust the `..\` source paths to wherever these planning files live.

---

## Step 3 — Initialize SpecKit in the repo

```powershell
# Install the SpecKit CLI once (via uv)
uv tool install specify-cli --from git+https://github.com/github/spec-kit.git

# Initialize SpecKit INTO the current repo, targeting your AI agent (Claude Code shown)
specify init --here --ai claude --force

# Verify the setup
specify check
```
**Why:** `specify init --here` drops the SpecKit scaffold into the existing repo — the `.specify/` folder
(constitution, templates, helper scripts) and the agent's slash-command definitions (e.g. `.claude/`).
`--force` is needed because the folder already has our skeleton in it. `--ai claude` wires the
`/speckit.*` commands for Claude Code (swap for `copilot`, `cursor`, `gemini`, etc. if you use another
agent). `specify check` confirms the install and that the agent is detected.

---

## Step 4 — Start version control

```powershell
git init
git add .
git commit -m "Initial skeleton + SpecKit scaffold"
```
**Why:** A clean baseline commit before any feature work, so every later change is reviewable as a diff.
(If `specify init` already created a git repo, `git init` is harmless — it just re-confirms it.) Make a
GitHub repo and `git remote add origin <url>` when you're ready to push (needed later for Railway's
auto-deploy).

---

## Step 5 — Create the Python environment with uv

```powershell
# uv creates the virtual environment (.venv) for you
uv venv
```
**Why:** **`uv` is the only Python package manager this project uses — never `pip`.** `uv venv` creates
an isolated `.venv` for the project (reproducibility, P5). You don't manually activate it for normal work:
`uv run …`, `uv add …`, and `uv sync` all use `.venv` automatically. You'll **add dependencies later**
(Step 7/8) with `uv add`, which writes them to `pyproject.toml` and locks them in `uv.lock` — there is no
`requirements.txt`.

> **The uv cheatsheet for this project:**
> - `uv add --optional backend <pkg>` — add to the **backend** image's deps (see [dependencies.md](dependencies.md)).
> - `uv add --optional dashboard <pkg>` — add to the **dashboard** image's deps.
> - `uv add --group <ingestion|ml|evals|dev|test> <pkg>` — add to a non-shipped tooling group.
> - `uv add <pkg>` — shared base (rare; only if EVERY surface needs it).
> - `uv sync` — install from `pyproject.toml`/`uv.lock` (fresh clone). `--extra backend` / `--extra dashboard` for one image.
> - `uv run <cmd>` — run a command inside `.venv` without activating it (e.g. `uv run pytest`).
> - **Do not run `pip install`,** and **never add an ungrouped dependency** unless it's truly shared.

---

## Step 6 — Set the Constitution (once, inside the AI agent)

Open the repo in your AI agent (e.g. Claude Code), then run:

```
/speckit.constitution
<paste the constitution block from sous-chef-spec-plan.md → Section 4 "Run ONCE">
```
**Why:** The Constitution is the highest-priority artifact — the ten engineering principles every spec,
plan, task, and line of code must obey. Setting it first means every later `/speckit.*` command is
checked against it. You only do this once for the whole project.

---

## Step 7 — Build the project phase by phase (the core loop)

Work **one phase at a time**, in order (Phase 1 → 5). For **each phase**, run this loop inside the AI
agent, using that phase's paste-ready blocks from [sous-chef-spec-plan.md](sous-chef-spec-plan.md) §4:

```
# (a) WHAT & WHY — generates specs/NNN-*/spec.md on a new feature branch
/speckit.specify
<paste the phase's /speckit.specify block>

# (b) review the generated spec, then HOW — generates plan.md
/speckit.plan
<paste the phase's /speckit.plan block>

# (c) break it into ordered, reviewable work — generates tasks.md
/speckit.tasks

# (d) generate the code against the tasks (review every change)
/speckit.implement
```
**Why each command:**
- **`/speckit.specify`** captures the requirements/user-stories for *just this phase* and creates its own
  `specs/NNN-*/` folder + feature branch — keeping each phase isolated and reviewable.
- **`/speckit.plan`** turns the "what" into the technical "how" (architecture, stack, data, APIs) for
  that phase, constrained by the Constitution.
- **`/speckit.tasks`** decomposes the plan into small, ordered tasks you can review and check off.
- **`/speckit.implement`** writes the code task-by-task. You **review and own every line** (no vibe
  coding, P9). Commit the generated `spec.md`/`plan.md`/`tasks.md` alongside the code.

The phases (full content in §4): **1 Foundation** → **2 Core (data + the wall + favorites)** → **3 AI
(RAG, classifier/router, agent, guardrails)** → **4 Testing & UI** → **5 Deployment**. Don't start a
phase until the previous one's gates are green (Step 9).

---

## Step 8 — Run the stack locally (from Phase 1 onward)

```powershell
# Bring up backend + postgres(pgvector) + redis + vault + phoenix
docker compose up -d

# Seed secrets into Vault (Groq key, embeddings key, DB creds) per the runbook
./scripts/seed_vault.sh

# Build the recipe corpus (after Phase 2 ingestion is implemented)
uv run python -m ingestion.run_ingest

# Train the intent classifier offline (after Phase 3); outputs ml/artifacts/model.joblib
uv run python ml/train_classifier.py
```
**Why:** `docker compose up` starts the four backing services + the app so you can exercise real
behavior. Secrets go into **Vault, never `.env`** (P6) — `seed_vault.sh` loads them. `uv run` executes
these scripts inside the project's `.venv` (no manual activation). `run_ingest` fetches TheMealDB +
TheCocktailDB + the Kaggle subset, tags categories, extracts ingredients/allergens, and embeds into
pgvector. `train_classifier.py` produces the lean `joblib` model the app serves (no torch in the
container). Run these as the corresponding phase implements them.

> When Phase 1 first needs packages, add them with uv **into the right group** (so each Docker image
> only pulls its own libs — see [dependencies.md](dependencies.md)):
> ```powershell
> uv add --optional backend fastapi "uvicorn[standard]" sqlalchemy alembic pgvector redis hvac groq
> uv add --optional dashboard streamlit streamlit-authenticator
> uv add --group dev ruff mypy ; uv add --group test pytest
> ```
> On a fresh clone, `uv sync` installs from `pyproject.toml` / `uv.lock`. The backend image builds with
> `uv sync --frozen --no-dev --extra backend`; the dashboard with `--extra dashboard`.

---

## Step 9 — Verify: tests and eval gates (definition of done)

```powershell
make lint     # ruff + mypy
make test     # pytest: unit + integration + redteam
make evals    # all eval suites vs eval_thresholds.yaml
```
**Why:** A phase is "done" only when these pass. The **red-team gate** (allergen-override + injection must
all be refused) and the **redaction gate** are hard — never weaken a threshold to go green (P4); fix the
cause. This is what stops the wall or grounding from silently regressing between phases.

---

## Step 10 — Run the two front-end surfaces (Phase 4)

```powershell
# Cook-facing chat widget (React + Vite, plain JS)
Set-Location widget
npm install
npm run dev
Set-Location ..

# Operator dashboard (Streamlit, cookie login)
streamlit run dashboard/app.py
```
**Why:** The widget is the end-user chat surface (category chips → recipe cards → full steps → favorites);
`npm run dev` serves it with hot reload against the backend. The Streamlit dashboard is your
operator/eval console (browse corpus, run evals, view metrics, deep-link to Phoenix) and stays logged in
across refreshes.

---

## Step 11 — Deploy to Railway (Phase 5)

```powershell
# Push to GitHub (Railway auto-deploys the main branch)
git push -u origin main
```
**Why:** Railway's GitHub integration **auto-deploys `main`** — but only after the GitHub Actions gates
(Step 9, run in CI) pass, so a regression can't reach the live URL. In the Railway dashboard you add the
PostgreSQL (pgvector) + Redis plugins, run Phoenix and Vault as services, and set the bootstrap variables;
application secrets stay in Vault. Tag the release when the live demo works:
```powershell
git tag v0.1.0
git push origin v0.1.0
```

---

## From then on — your day-to-day loop

1. **Pick the next phase/feature** (or a fix) and branch if needed.
2. **Spec it first:** `/speckit.specify` → review → `/speckit.plan` → `/speckit.tasks` → `/speckit.implement`.
3. **Verify:** `make lint && make test && make evals` — all green, including the hard gates.
4. **Commit** the code *and* its generated specs together (keeps the spec the source of truth, P8).
5. **Push** → CI runs the gates → green `main` auto-deploys to Railway.
6. **Keep docs current:** update `docs/DECISIONS.md` with any choice backed by a number, and the relevant
   spec if reality changed (fix the spec, then regenerate — never let code and spec drift, P8).

**Golden reminders (from [CLAUDE.md](CLAUDE.md)):** the allergen/diet wall is the grade; never invent
recipes or steps; no torch in any container; secrets only in Vault; redact before logging *and* tracing;
the evals are the grade; you own every line.
