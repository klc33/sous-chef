# CLAUDE.md — SousChef

AI recipe-discovery assistant for home cooks who want to try something new. A cook chats, gets **real
retrieved recipes** (a list of cards → click for full text steps), can build a varied meal plan + a
shopping list, and saves favorites. Solo 2-week project.

Full design: [amer_idea-1.md](projectplanFolderForMd/amer_idea-1.md) · stack:
[amer_idea-1-tech.md](projectplanFolderForMd/amer_idea-1-tech.md) · models:
[model_role.md](projectplanFolderForMd/model_role.md) · file layout:
[structure.md](projectplanFolderForMd/structure.md).

## Golden rules (do not break these)
1. **The wall is the grade.** Never surface a recipe that violates the cook's stated allergy/diet. It's
   enforced in deterministic code (`services/user/constraint_guard.py`), not in a prompt.
2. **Ground everything.** The app never invents recipes or steps. Lists come from retrieval; detail
   views render the recipe's **stored steps verbatim**.
3. **No torch, ever.** LLM + embeddings are hosted-API calls. The classifier is trained offline (`ml/`)
   and served via `joblib`. Nothing torch/transformers in `app/` or any image (keep images < ~500MB).
4. **Secrets live in Vault.** Never put keys in `.env`, code, or an image. `.env.example` holds only
   non-secrets (Vault addr/token, service URLs).
5. **Redact before it leaves.** PII redaction (`app/core/redaction.py`) runs before logging **and**
   before any Phoenix span is emitted.
6. **Evals are the grade.** Don't weaken a threshold to make CI pass. If a gate fails, fix the cause.
7. **No vibe coding.** Build via SpecKit (`/specify → /plan → /tasks → /implement`); you own every line.

## Stack
FastAPI · PostgreSQL + pgvector · Redis · HashiCorp Vault · **Groq** (LLM; embeddings from a *separate*
provider — Groq is chat-only) · scikit-learn + joblib (classifier) · deterministic in-process guardrails
(regex input/output rails — NeMo Guardrails was evaluated and dropped: unused + heavy C++ build dep) ·
Presidio (PII) · **Arize Phoenix** (self-hosted tracing, OpenTelemetry) · React + Vite (**plain JS/JSX,
no TypeScript**) · Streamlit + streamlit-authenticator (cookie login) · Docker/compose · Railway · SpecKit.

## Architecture (monolith)
One FastAPI app. A turn flows:
`guardrails input rail → intent classifier (router) → easy: workflow | hard: bounded agent → constraint guard → guardrails output rail`.

Layering — keep it strict:
- `app/api/` — thin HTTP. Split by audience: `api/user/*` (public, profile-scoped) and `api/admin/*`
  (operator-auth via `admin_deps.py`).
- `app/services/` — business logic, split by audience: `services/user/` (search, rag, freshness, the
  wall, meal_plan, shopping_list, nutrition, favorites) and `services/admin/` (corpus, evals, metrics,
  ingestion, traces). Shared helpers in `services/shared/`.
- `app/repo/` — **the only place that touches the DB.** Parameterized queries / ORM only (injection-safe).
- `app/infra/` — adapters for everything external (Groq, embeddings, Vault, Phoenix, Postgres, Redis,
  TheMealDB/TheCocktailDB/Open Food Facts). Swappable + mockable in tests.
- `app/agent/`, `app/classifier/`, `app/guardrails/`, `app/core/` — in-process modules of the monolith.

Other surfaces (same repo, separate apps): `dashboard/` (Streamlit admin) · `widget/` (React).
Offline-only (never shipped): `ml/` (training) · `ingestion/` (corpus pipeline) · `evals/` (CI gates).

## Commands
**Python deps: use `uv` only — never `pip`.** `uv venv` creates `.venv`; `uv sync` installs from the lock;
`uv run <cmd>` runs in `.venv`. Deps live in `pyproject.toml` + `uv.lock` (no `requirements.txt`).
**Always add deps into the right group** so each image stays lean ([dependencies.md](projectplanFolderForMd/dependencies.md)):
`uv add --optional backend <pkg>`, `uv add --optional dashboard <pkg>`, `uv add --group <dev|test|ingestion|ml|evals> <pkg>`.
Images build with `uv sync --frozen --no-dev --extra backend` (or `--extra dashboard`). No `torch` in any image.
```
make up        # docker-compose: backend + postgres(pgvector) + redis + vault + phoenix
make seed      # seed Vault secrets + a small demo corpus
make ingest    # build the corpus (TheMealDB + TheCocktailDB + Kaggle → categorize → embed)
make train     # train the intent classifier offline → ml/artifacts/model.joblib
make test      # pytest (unit + integration + redteam)
make evals     # run all eval suites vs eval_thresholds.yaml
make lint      # ruff + mypy
```
Widget: `cd widget && npm install && npm run dev`. Dashboard: `uv run streamlit run dashboard/app.py`.

## Conventions
- **Categories are fixed**: `hot drink | cold drink | breakfast | lunch | dinner`. Every recipe gets
  exactly one, tagged at ingestion; it's a pgvector metadata filter, not a runtime guess.
- **Agent tools** are the only way the LLM acts: `search_recipes`, `get_recipe`, `get_nutrition`,
  `build_shopping_list`, `substitute_ingredient`. Every tool input is Pydantic-validated. The loop is
  bounded (capped iterations + tokens).
- **Prompts are code** — they live in `prompts/`, version-controlled. Never hardcode prompts inline.
- **Cook identity** is a passwordless `profile-ID` header (favorites + seen-history only). The
  `tenant`/owner is never taken from the request body.
- **Freshness**: retrieval excludes a profile's seen-history so repeated queries return new recipes;
  favorites are exempt.

## Definition of done (before you say it's finished)
`make lint && make test && make evals` all green — including the **red-team gate** (allergen-override +
injection/jailbreak probes must all be refused) and the **redaction** test. Then verify the change in
the running stack (`make up`).

<!-- SPECKIT START -->
Active feature: **006-corpus-data-quality**. For technologies, project structure, shell commands,
and other context for the current work, read the plan at `specs/006-corpus-data-quality/plan.md`
(with its `research.md`, `data-model.md`, `contracts/`, and `quickstart.md`).
<!-- SPECKIT END -->
