# Technologies Used — SousChef

A complete list of the technologies in this project, grouped by area.

## Backend Core
- **FastAPI** — the single backend service (HTTP routes, the router, the bounded agent loop)
- **Pydantic** — request/response validation and agent tool-input schemas
- **SQLAlchemy** — ORM; the only layer that touches the database
- **Alembic** — versioned, reversible database migrations
- **uv** — Python dependency management and virtualenvs (never pip)

## Data Stores
- **PostgreSQL** — system of record (recipes, ingredients, nutrition, favorites, seen-history, cook accounts)
- **pgvector** — PostgreSQL extension for recipe embeddings + similarity search (RAG)
- **Redis** — ephemeral per-conversation session memory with a TTL

## Security & Secrets
- **HashiCorp Vault** (`hvac` client) — stores all secrets (LLM/embeddings keys, DB creds, JWT signing keys)
- **JWT (cook-session + operator auth)** — admin-provisioned cook accounts gate the app (`typ:"cook"`, Bearer); operator auth for admin surfaces (features 008/009)
- **Deterministic in-process guardrails** — regex input/output rails for injection/jailbreak (NeMo Guardrails was evaluated and dropped — unused + heavy C++ build dep)
- **Presidio** — PII redaction before logging and before any Phoenix span is emitted

## AI & Machine Learning
- **Groq** — hosted LLM API (chat/completions only); understands requests, ranks/explains recipes, drives the agent
- **Hosted Embeddings API** — separate provider (Groq is chat-only) for embedding the corpus and queries
- **scikit-learn + joblib** — the trained intent classifier (router), served lean (no torch)
- **RAGAS / frozen judge** — RAG evaluation (faithfulness, relevancy) in CI
- **Arize Phoenix** — self-hosted LLM tracing and token-cost observability (OpenTelemetry / OpenInference)

## Frontend
- **React + Vite** (plain JavaScript / JSX, no TypeScript) — the cook-facing chat widget
- **Streamlit + streamlit-authenticator** — operator/admin dashboard with cookie-based login

## Infrastructure & Dev Workflow
- **Docker + docker-compose** — the one-command local stack
- **Railway** — public deployment (services + managed Postgres/pgvector + Redis)
- **GitHub Actions** — CI/CD; runs lint, build, and the eval gates on every push
- **GitHub SpecKit** — spec-driven build methodology (`/specify → /plan → /tasks → /implement`)
- **Make** — task runner (`make up`, `make seed`, `make ingest`, `make train`, `make test`, `make evals`, `make lint`)

## Data Sources
- **TheMealDB API** — structured food recipes (breakfast / lunch / dinner) for the seed corpus
- **TheCocktailDB API** — non-alcoholic drinks (hot drink / cold drink)
- **RecipeNLG / Food.com Recipes (Kaggle)** — richer RAG corpus + labeled set for the classifier
- **Open Food Facts** — nutrition facts and allergen data behind "the wall"

## Deliberately NOT Used
- **No torch / transformers** in any container — LLM + embeddings are API calls; classifier is classical
- **No dedicated vector database** — pgvector inside Postgres covers it
- **No Kubernetes, service mesh, or event bus** — docker-compose is the right scale
- **No blob store (MinIO/S3)** — no large user-uploaded artifacts at this scope
