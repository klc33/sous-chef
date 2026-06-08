# SOUSCHEF — TECHNOLOGY REFERENCE

A companion to [amer_idea-1.md](amer_idea-1.md). For every technology in the stack: **what it is**,
**what it's used for in SousChef**, **why it was chosen** (and over what), and **how it fits** the rest
of the system. The guiding rule from the project plan applies here: *every technology must earn its
place by solving a specific problem* — nothing is listed because it's popular.

---

## STACK AT A GLANCE

| Layer | Technology | One-line job in SousChef |
|---|---|---|
| API framework | **FastAPI** | The single backend: routes, validation, the router + agent |
| Data validation | **Pydantic** | Typed request/response + tool-input schemas |
| ORM + migrations | **SQLAlchemy + Alembic** | Database models, versioned schema, all DB access |
| Primary database | **PostgreSQL** | System of record: recipes, ingredients, favorites, history |
| Vector search | **pgvector** | Recipe embeddings + similarity search for RAG |
| Session cache | **Redis** | Ephemeral per-conversation memory with a TTL |
| Secrets | **HashiCorp Vault** (`hvac`) | Stores & serves the Groq key, DB creds, API keys |
| Generation | **Groq** (hosted LLM API) | Understands requests, ranks/explains, drives the agent |
| Embeddings | **Hosted embeddings API** | Turns recipes + queries into vectors for retrieval |
| Classifier | **scikit-learn + joblib** | The trained intent router, served lean (no torch) |
| Guardrails | **NeMo Guardrails / Guardrails.ai** | Input/output rails: injection, jailbreak, topic, PII |
| PII redaction | **Presidio** | Scrubs personal data before logging |
| Tracing / observability | **Arize Phoenix** | Self-hosted per-turn traces + token-cost (no account) |
| RAG evaluation | **RAGAS / frozen judge** | Faithfulness & relevancy metrics in CI |
| User UI | **React + Vite** | The cook's chat widget (categories, cards → steps, favorites) |
| Operator UI | **Streamlit + streamlit-authenticator** | Admin/eval dashboard; cookie login survives refresh |
| Build method | **GitHub SpecKit** | Spec-driven dev: specify → plan → tasks → implement |
| Packaging | **Docker + docker-compose** | One-command local stack from a fresh clone |
| Hosting | **Railway** | Deployed services + managed Postgres/Redis at a public URL |
| CI/CD | **GitHub Actions** | Lint, build, and run the eval gates on every push |
| Data | **TheMealDB · TheCocktailDB · RecipeNLG/Food.com · Open Food Facts** | Recipes + drinks + nutrition/allergen data |

---

## 1. BACKEND CORE

### FastAPI
**What it is.** A modern Python web framework for building HTTP APIs, built on ASGI (async) with
automatic OpenAPI docs and first-class Pydantic integration.

**Used for.** The *one* backend service. Every layer of the app lives here: HTTP routes (`api/`), the
intent-classifier router and the bounded agent loop (`service/`), database access (`repo/`), and the
adapters to external services (`infra/`). It serves the chat endpoint the React widget calls, the
`favorites` CRUD endpoints, and the recipe lookup endpoints.

**Why this and not alternatives.** Over Flask: native async (important when a single chat turn fans out
to an LLM call, an embeddings call, and DB queries) and built-in validation. Over Django: far lighter —
SousChef needs an API, not an admin/templating monolith. FastAPI + Pydantic also means the agent's tool
contracts and the API's request/response shapes are the *same* typed objects, which is exactly the
"specs as code" discipline the project values.

**How it fits.** Sits at the center of the architecture diagram — everything (widget, dashboard, LLM,
classifier, guardrails, databases, Vault) connects through it. The layered structure keeps the
constraint guard and grounding logic in one auditable place.

### Pydantic
**What it is.** A data-validation library that turns Python type hints into runtime-validated models.

**Used for.** (1) Request/response schemas on every endpoint, so malformed input is rejected at the
edge. (2) **The agent's tool-input schemas** — every tool call (`search_recipes`, `get_recipe`, etc.)
is validated against a Pydantic model *before* it touches the repo layer. (3) Loading/validating config.

**Why.** It's the validation backbone FastAPI is built on, so it's free. More importantly, schema-
validating tool inputs is a security control in this project: an LLM that emits a malformed or hostile
tool argument is stopped by the schema, not by hope.

### SQLAlchemy + Alembic
**What they are.** SQLAlchemy is Python's standard ORM/SQL toolkit; Alembic is its companion
migration tool (versioned, reversible schema changes).

**Used for.** All database access goes through the SQLAlchemy models in the `repo/` layer — recipes,
parsed ingredients, nutrition cache, meal plans, conversations, and the per-profile `profiles`,
`favorites`, and `seen_history` tables. Alembic versions the schema from Day 1 (a baseline migration),
so the database structure evolves under source control.

**Why.** Centralizing DB access in the ORM is what makes the **freshness rule** ("exclude seen recipe
IDs") and the **constraint queries** expressible and auditable in one place. It's also the project's
**SQL-injection defense**: parameterized queries by construction — the app never string-builds SQL.
Alembic exists because "I changed the schema on my laptop" is not a reproducible stack.

**How it fits.** The `repo/` layer is the only thing that talks to Postgres/pgvector; the service layer
calls repos, never raw SQL.

---

## 2. DATA STORES

### PostgreSQL
**What it is.** A mature, open-source relational database.

**Used for.** The **system of record** for everything durable: the recipe corpus and its parsed
ingredients, the nutrition cache, saved meal plans, conversation records, and the persistent per-profile
state — `profiles` (the client-generated ID), `favorites` (profile ↔ recipe), and `seen_history`
(profile ↔ recipe ↔ timestamp).

**Why.** It's reliable, well-understood, and — crucially — it hosts pgvector as an *extension*, so
SousChef gets vector search without standing up a second database. The favorites feature also needs
real durable, queryable, relational storage (joins between profiles and recipes), which is exactly
Postgres's wheelhouse.

**How it fits.** One database serves both the relational data and (via pgvector) the embeddings, keeping
the infrastructure lean — a deliberate scope decision in the plan.

### pgvector
**What it is.** A PostgreSQL extension that adds a `vector` column type and similarity-search operators
(cosine / inner-product / L2), with indexing (IVFFlat / HNSW).

**Used for.** Stores the **recipe embeddings** alongside metadata columns (diet flags, allergen tags,
cuisine, time). RAG retrieval runs a similarity query here to produce the ranked recipe-option list,
**pre-filtered by metadata** (diet/allergen) and **excluding seen-history IDs** for freshness.

**Why this and not a dedicated vector DB.** A standalone vector store (Pinecone, Weaviate, Qdrant) would
add a service, a second source of truth, and operational overhead a solo two-week project doesn't need.
pgvector keeps recipes and their vectors in the *same* database, so a metadata pre-filter is a normal
`WHERE` clause and there's nothing to keep in sync. It teaches the real lessons (dense retrieval,
metadata filtering) without the infrastructure tax.

**How it fits.** It's the engine behind Feature 1 (search) and the meal-plan agent's `search_recipes`
tool; the allergen pre-filter here is one of the layers of "the wall."

### Redis
**What it is.** An in-memory key-value data store, commonly used for caching and ephemeral state.

**Used for.** **Short-term session memory** — the cook's current constraints (diet/allergies/servings)
and recent conversation turns — keyed per conversation, with an **explicit TTL**.

**Why.** A chat assistant that forgets the last message is useless, but storing an anonymous chat
session forever is a needless liability. Redis with a TTL is the natural fit for "remember for now,
forget later." The plan deliberately contrasts this with Postgres: **ephemeral** state (session) lives
in Redis with a TTL; **durable** state the cook expects to keep (favorites, seen-history) lives in
Postgres *without* one.

**How it fits.** Gives the agent and router conversational context within a session, without polluting
the durable store.

---

## 3. SECURITY & SECRETS

### HashiCorp Vault (`hvac` client)
**What it is.** A secrets-management system that stores, controls access to, and audits secrets;
`hvac` is its Python client.

**Used for.** Holds **all project secrets** — the LLM API key, database credentials, and external API
keys. The app resolves them at runtime through a small `infra` secrets adapter. Nothing sensitive lives
in `.env`, in code, or baked into a Docker image; `.env.example` carries only the Vault address and a
bootstrap token. Vault runs as a container in the compose stack (dev mode locally).

**Why.** A leaked `.env` is the most common way a project's API keys end up on someone else's bill.
Centralizing secrets gives one place to rotate, audit, and revoke — the honest pattern for anything
that might face real users. (Per the user's explicit requirement.)

**How it fits.** It's a cross-cutting dependency: the LLM adapter, embeddings adapter, and DB connection
all pull credentials from Vault rather than the environment.

### NeMo Guardrails / Guardrails.ai
**What they are.** Open-source frameworks for putting programmable "rails" around an LLM application.
**NeMo Guardrails** (NVIDIA) specializes in conversational/topical rails and jailbreak/injection
detection; **Guardrails.ai** is validation-first (composable validators for output structure and PII).

**Used for.** The **guardrails layer** — pick one as the primary. **Input rails** screen every inbound
message for **prompt injection and jailbreak** attempts ("ignore previous instructions," "reveal your
system prompt") and obvious off-topic/abuse before it reaches the router. **Output rails** run a final
check (no system-prompt or secret leakage, plus PII redaction) before a reply reaches the cook.

**Why.** The chat endpoint is public, untrusted input. "The model will probably behave" is not a
control. Rails make the boundary explicit and testable — and the project gates them in CI (the red-team
suite), so a future change can't silently reopen the hole. NeMo is the default because injection +
topic control is its strength; Guardrails.ai is the alternative if PII/output validation is the part
you'd rather a library own.

**How it fits.** Wraps the message handler on both sides in the architecture diagram; its behavior is
part of the hard red-team CI gate.

### Presidio
**What it is.** Microsoft's open-source PII detection-and-anonymization library.

**Used for.** **PII redaction** before anything is logged or traced. A committed test proves a fake
email/phone/API key pasted into chat never appears unredacted in logs.

**Why.** Cooks paste personal details into chat boxes ("save my preferences, here's my email").
Hand-rolled redaction regex misses cases; Presidio is purpose-built. (Guardrails.ai's PII validator is
an acceptable substitute if it's already the chosen guardrails library.)

**How it fits.** Used by the output rail and the logging filter; backs the redaction CI gate.

---

## 4. AI & MACHINE LEARNING

### Groq (hosted LLM API)
**What it is.** An inference provider that serves open-weight LLMs (e.g. Llama 3.x) over an HTTP API on
custom hardware (its LPU) at very high speed — no local weights, no GPU on your side. Its API is
OpenAI-compatible and it ships a `groq` Python SDK.

**Used for.** Three jobs: (1) **understand** the cook's natural-language request; (2) **rank and explain**
the retrieved recipes (it does *not* write recipes — it works only with retrieved, real ones); (3) act
as the **reasoning engine of the bounded agent**, choosing which tools to call for multi-step planning.

**Why Groq, and why API not local.** "API-only inference" is a core project rule: it keeps containers
small, `docker-compose up` fast, and removes the entire torch/CUDA dependency-hell class of problems.
Groq specifically gives **very low latency**, which matters in an interactive chat where a turn already
fans out to retrieval + an LLM call; its OpenAI-compatible interface and generous free tier make it an
easy, cheap fit for a junior project. (Per the user's explicit choice of Groq over Anthropic.)

**How it fits.** Called through an `infra` LLM adapter (swappable, mockable in tests), with its key
pulled from Vault. Bounded by the agent's iteration/token caps for cost and safety.

### Hosted Embeddings API
**What it is.** A model (also API-hosted) that converts text into fixed-length vectors capturing meaning.

> **Note — embeddings are a separate provider.** Groq is **chat/completions only**; it does not offer an
> embeddings endpoint. So embeddings come from a separate hosted embeddings API (e.g. OpenAI
> `text-embedding-3-small`, Cohere, or Jina), behind its own `infra` adapter with its own Vault-stored
> key. Groq for *generation*, the embeddings provider for *retrieval* — two adapters, two keys.

**Used for.** Embedding the recipe corpus at ingestion time (into pgvector) and embedding each search
query at request time, so retrieval can find semantically similar recipes — "something Thai I haven't
made" matches dishes that don't contain those literal words.

**Why.** API embeddings are why the build is seconds, not 30 minutes — and why this is a realistic SaaS
stack rather than a shortcut. Semantic retrieval is what makes the discovery experience work.

**How it fits.** The bridge between raw recipe text and pgvector; the quality of these vectors directly
drives the RAG retrieval numbers (hit@k, MRR) the project gates on.

### scikit-learn + joblib (the intent classifier)
**What they are.** scikit-learn is the standard classical-ML library (TF-IDF, logistic regression,
metrics); joblib serializes a trained model to a file.

**Used for.** SousChef's **own trained model**: an **intent classifier** (`find_recipe | plan_meals |
nutrition_q | substitution | chitchat | out_of_scope`) that acts as the **router**. Trained offline
(notebook/Colab) on a labeled set, exported with joblib, and served lean behind the model adapter.

**Why.** The router needs a cheap, deterministic signal on *every* message — burning an LLM call just to
classify intent is wasteful. TF-IDF + logistic regression is fast, tiny, explainable, and trains in
seconds. It's compared head-to-head against an **LLM zero-shot baseline** (macro-F1, latency, cost); the
winner on F1 isn't always the winner on cost, and defending that tradeoff is a core learning goal.
joblib serving means the container needs only scikit-learn + numpy — **no torch, no transformers** — so
the image stays small and fast (the "train heavy, serve lean" rule).

**How it fits.** The classifier's output drives the hybrid router: easy intents handled by the
deterministic workflow, hard/ambiguous ones escalated to the agent. Its macro-F1 is a committed CI gate.

> **Optional stretch — ONNX + onnxruntime.** If a small deep-learning classifier is attempted, it would
> be trained offline and exported to **ONNX**, then served with **onnxruntime** (not torch) to keep the
> container lean. The plan marks this as optional, not required.

### RAGAS / a frozen judge model
**What it is.** RAGAS is a library for evaluating RAG systems (faithfulness, answer relevancy, context
precision/recall); a "frozen judge" is a pinned LLM used as an automated grader.

**Used for.** The **RAG evaluation gate** — scoring retrieval (hit@k, MRR) and generation (faithfulness:
does the answer stick to retrieved recipes? answer relevancy?) on a golden set of ~15 question/
ideal-answer/ground-truth-recipe triples.

**Why.** "It looks good in the demo" is not evaluation. Grounding is the whole point of the RAG layer,
and faithfulness scores are how you *prove* the assistant isn't drifting from real recipes. Hand-labeling
a few and reporting agreement with the judge keeps the automated metric honest.

**How it fits.** Runs in CI against committed thresholds in `eval_thresholds.yaml`; a regression blocks
merge.

### Arize Phoenix (LLM tracing & cost)
**What it is.** An open-source LLM-observability tool built on **OpenTelemetry / OpenInference**. It
captures **traces** (the nested steps of an LLM request), tracks **token usage and cost**, and ships its
own UI to inspect, filter, and debug them. It runs **fully self-hosted with no account** — `uv add
arize-phoenix` for local, or the `arizephoenix/phoenix` container for the deployed service.

**Used for.** **End-to-end tracing of every chat turn.** A turn becomes one trace whose spans are the
router decision, the retrieval call, and the Groq LLM/agent tool calls — each with latency and **token
cost** attached. That's what answers "what did this turn cost, and where did the time go?", powers the
per-turn cost the operator reviews, and makes a misrouted or slow turn debuggable after the fact. The
backend is instrumented with OpenInference and exports OTel spans to Phoenix.

**Why this and not a hosted tracer.** You can't improve (or defend) what you can't see, and hand-rolling
trace storage + a viewer is a project in itself. Phoenix gives a junior engineer production-grade LLM
observability that is **free, requires no signup or API key, and reuses infrastructure already in the
stack** — it persists traces to the **same Postgres** via `PHOENIX_SQL_DATABASE_URL`, so it adds a
service but **no new datastore**. (Chosen over Langfuse, whose Cloud needs an account and whose
self-host pulls in ClickHouse; and over pure OTel+Jaeger, which is more wiring and less LLM-aware.)
Because it's OpenTelemetry under the hood, the instrumentation is portable — you could repoint it at any
OTel backend later without rewriting the app.

**How it fits — and the safety boundary.** Tracing sits across the request path, but **PII redaction
runs *before* any span is emitted**, so a secret pasted into chat never lands in a trace — the same
redaction the logging filter uses, applied to the span payload. The operator views deep traces in
Phoenix's own UI; the Streamlit dashboard links out to it rather than duplicating the viewer.

---

## 5. FRONTEND

### React + Vite (plain JavaScript / JSX)
**What they are.** React is the component-based UI library; Vite is a fast build tool/dev server for it.
The widget is written in **plain JavaScript (`.jsx`), not TypeScript** — a deliberate choice to keep the
toolchain minimal for a small solo widget (no type-compilation step, no `.ts` config). Component
contracts are simple enough that runtime checks (e.g. PropTypes) can be added later if ever needed.

**Used for.** The **cook-facing chat widget** — the real conversational surface. It renders assistant
replies as **recipe-option cards** (title + key ingredients), expands a selected card into the **full
text instructions + nutrition**, and provides the **save-to-favorites** button and **Favorites view**.
It generates and stores the **profile ID** in `localStorage` and sends it as a request header.

**Why.** The browse-then-drill loop (a list of options, click one for steps) is genuinely interactive
state — exactly what React is for, and more natural than forcing it into Streamlit. Vite keeps the dev
loop and the production bundle fast/small.

**How it fits.** Talks only to the FastAPI backend over the documented response shapes (`recipes[]` with
`is_favorite`, `get_recipe(id)`, `favorites` endpoints). It stays "dumb" — all grounding and
constraint logic is server-side.

### Streamlit + streamlit-authenticator
**What they are.** Streamlit turns Python scripts into data apps with almost no frontend code.
`streamlit-authenticator` adds login to a Streamlit app and — crucially — **persists the session in a
signed cookie**.

**Used for.** The **operator (you) dashboard**: browse the ingested recipe corpus, run the eval suites on
demand, read classifier metrics and CI gate status, and **deep-link to Phoenix** for per-turn traces
and cost (Phoenix owns trace storage and its viewer; the dashboard doesn't rebuild it). It's
**login-protected** (it exposes operational data), and the login **survives a browser refresh**.

**Why the authenticator matters.** Streamlit re-runs the whole script on every interaction and **clears
`st.session_state` on a full page reload** — so naïve auth logs you out every refresh, which is the
exact annoyance the project rules out. `streamlit-authenticator` stores a signed session cookie, so a
refresh re-hydrates the session and keeps you logged in. (Equivalent alternatives:
`streamlit-cookies-manager` or `extra-streamlit-components`' CookieManager.)

**Why Streamlit at all.** Evals need a home, and Streamlit gives one engineer a working dashboard in
hours instead of days (deep observability is delegated to Phoenix, not rebuilt). Keeping it separate
from the React widget keeps each surface small — the user gets a polished chat UI; you get a no-frills
control panel.

**How it fits.** A read/operate surface over the same backend and database; it's where the "the evals
are the grade" story is made visible — and now usable through refreshes during a live demo.

---

## 6. INFRASTRUCTURE & DEV WORKFLOW

### Docker + docker-compose
**What they are.** Docker packages each service into a container; docker-compose orchestrates the
multi-container stack with one command.

**Used for.** Defining and running the whole local stack — FastAPI, Postgres+pgvector, Redis, Vault, and
the model/guardrails processes — so the project comes up cleanly with `docker-compose up` from a fresh
clone (after seeding secrets into Vault).

**Why.** Reproducibility is a submission requirement: a grader must be able to clone and run it. Compose
is the right scope — **no Kubernetes, no service mesh, no event bus**, which the plan explicitly
excludes as overkill for a solo project. The "API-only inference, no torch" rule keeps every image small
and fast to build.

**How it fits.** It's the single entry point to the running system and the substrate the smoke-test CI
gate exercises. Compose is for **local dev**; the **deployed** target is Railway (below).

### Railway
**What it is.** A Git-driven platform-as-a-service (PaaS): connect a repo, and Railway builds and hosts
your services, offering managed database plugins, injected environment variables, and HTTPS URLs.

**Used for.** The **public deployment**. Each container becomes a Railway **service** in one project —
the FastAPI backend, the Streamlit dashboard, and the model/guardrails process — plus Railway's managed
**PostgreSQL (with pgvector enabled)** and **Redis** plugins. The React widget ships as a static
service (or a static host like Vercel/Netlify) pointed at the backend URL. Railway's **GitHub
integration auto-deploys `main`**, so the flow is: push → GitHub Actions runs the eval gates → a green
`main` redeploys to Railway.

**Secrets on Railway.** Railway's encrypted service variables hold only the *bootstrap* secrets — the
Vault address/token and the managed Postgres/Redis connection strings Railway generates. **Application
secrets** (LLM/embeddings/API keys) still live in **Vault**, resolved via the `infra` adapter, so the
Vault discipline carries into production rather than being bypassed by the platform's env vars.

**Why this and not alternatives.** Over raw VMs / Kubernetes / Terraform: Railway gives a junior engineer
a real, shareable HTTPS deployment in an afternoon, with managed Postgres+Redis, and no orchestration to
operate — which is exactly the lean, no-Kubernetes scope the project mandates. Over Heroku: first-class
Postgres+pgvector and Redis plugins and a simpler multi-service model. Over a static-only host (Vercel/
Netlify): those can't run the stateful FastAPI + managed databases the backend needs, so they're at most
the home for the static React bundle.

**How it fits.** Railway is the delivery half of the CI/CD story: **GitHub Actions is the gate, Railway
is the deploy.** It turns the project from "runs on my laptop" into a live URL anyone can open — and a
meal-planning assistant nobody can open isn't a product.

### GitHub Actions
**What it is.** GitHub's built-in CI/CD; workflows run on every push.

**Used for.** On every push: lint, type-check, build the images, then run the **gates** — classifier
macro-F1, agent tool-selection golden set, RAG golden set, the **constraint/injection red-team set
(hard gate)**, the redaction test, and a stack smoke test. Thresholds live in `eval_thresholds.yaml`;
any regression blocks merge.

**Why.** "CI that doesn't gate on the assistant's behavior is theater." Making the wall, the grounding,
and the classifier into merge gates is what stops the system from quietly getting worse between Day 1
and Day 10. It's also the project's CI/CD learning objective.

**How it fits.** The enforcement mechanism for "the evals are the grade" — the same eval suites the
Streamlit dashboard surfaces are the ones that gate the pipeline. It also guards delivery: only a green
`main` triggers Railway's auto-deploy, so a regression can't reach the live URL.

### GitHub SpecKit
**What it is.** An open-source toolkit (the `specify` CLI) for **spec-driven development**: you write a
specification and let an AI coding agent generate the plan, the task breakdown, and the implementation
*from* that spec — keeping the spec, not the code, as the source of truth. It scaffolds a repo and
provides the slash-commands `/specify`, `/plan`, `/tasks`, `/implement` (plus a project "constitution").

**Used for.** The **build methodology** for the whole project. Each major component is taken through the
loop: **`/specify`** (what + why) → **`/plan`** (technical approach) → **`/tasks`** (reviewable steps) →
**`/implement`** (generate code). The tool contracts, the allergen/constraint rule, the five-category
taxonomy, the freshness rule, the favorites CRUD, and the eval thresholds are all specified before being
coded; the generated `spec.md` / `plan.md` / `tasks.md` are committed artifacts.

**Why.** The project's process grade is "specify, scaffold, review," not "write every line by hand."
SpecKit makes that explicit and auditable: when the spec and the code disagree, the spec wins and the
code is regenerated, so the system can't quietly drift from a definition you can point to. **"No vibe
coding" still holds** — you review and own every generated line. (Per the user's choice to build with
SpecKit.)

**How it fits.** It's the wrapper around the *entire* development workflow — it produces the specs that
the rest of the stack (and the CI gates) are built to satisfy. Runs with an AI coding agent locally;
nothing SpecKit-related ships in a container.

---

## 7. DATA SOURCES *(verified public & free — see [amer_idea-1.md](amer_idea-1.md) Data Sources)*

### TheMealDB API
**What it is.** A free public recipe API (test key `1`). Each record carries `strMeal` (title),
`strCategory`, `strArea` (cuisine), `strInstructions`, `strIngredient1–20` + `strMeasure1–20`, and
`strTags`.

**Used for.** The clean, structured **demo/seed corpus** for the **food** categories (breakfast / lunch
/ dinner) and a reference for the ingredient-parsing pipeline. Its structured ingredient+measure fields
make it ideal for the browse-then-drill demo path.

**Why.** Free, structured, and well-formed — the lowest-friction way to get real recipes with real steps
into the system. (Free tier caps list results, so it's the curated demo set, not the bulk corpus.)

### TheCocktailDB API
**What it is.** TheMealDB's sibling API for **drinks** (free test key `1`), with the same record shape —
`strDrink`, `strInstructions`, `strIngredientN`/`strMeasureN`, a `strCategory` (Coffee/Tea, Cocoa,
Shake, Soft Drink…), and an `strAlcoholic` flag.

**Used for.** The **drink** half of the five-category taxonomy. Filtered to **non-alcoholic** and mapped
to **hot drink** (Coffee/Tea, Cocoa, hot-served) vs **cold drink** (Shake, Soft Drink, iced).

**Why.** SousChef's categories include hot and cold drinks, which a food-only corpus can't supply.
TheCocktailDB provides them from the *same* clean, free, structured source family — same parsing code,
no new integration shape — and the alcoholic flag lets us keep the corpus non-alcoholic.

### RecipeNLG *or* Food.com Recipes (Kaggle)
**What they are.** Large public recipe datasets. **RecipeNLG** ≈ 2.2M recipes (ingredients, directions).
**Food.com Recipes & Interactions** ≈ 230k recipes with ingredients, steps, **tags (including dietary
tags), nutrition, and cooking time**.

**Used for.** The **richer RAG corpus** (ingest a few-thousand-recipe subset) and the **labeled set** for
the intent classifier, with a held-out split. **Pick one on Day 1** — Food.com if you want diet/cuisine/
time tags out of the box (helpful for the router and variety logic); RecipeNLG for sheer volume.

**Why.** TheMealDB alone is too small for non-trivial retrieval. A Kaggle dataset gives RAG enough
material to be meaningful without scraping.

### Open Food Facts
**What it is.** An open, free, collaborative food-product database with a JSON API and bulk exports
(CSV/Parquet/MongoDB), under the Open Database License.

**Used for.** Mapping parsed ingredients to **nutrition facts and allergen information** — backing the
per-recipe nutrition feature and feeding the **allergen detection** behind the wall.

**Why.** Recipe datasets are *not* reliably allergen-labeled, so allergens must be **derived**:
extraction parses ingredients, normalizes names, and maps them to allergen classes via Open Food Facts
(and/or a known allergen list). This is exactly why **extraction earns its place as an AI component**
rather than being a trivial join — and it's what makes the safety wall possible.

**How it fits.** Consumed at ingestion/enrichment time to tag recipes; those tags drive both the
nutrition summaries and the deterministic constraint guard.

---

## NOTE ON THINGS DELIBERATELY *NOT* USED

Per the project's scope rules, these are intentionally absent — listing them is part of justifying the
stack:

- **No torch / transformers in any container** — LLM + embeddings are API calls; the classifier is
  classical (or ONNX if a DL model is attempted). Keeps images < ~500MB and builds fast.
- **No dedicated vector database** — pgvector inside Postgres covers it.
- **No Kubernetes, service mesh, or event bus** — docker-compose is the right scale for one developer.
- **No blob store (e.g. MinIO/S3)** — there are no large user-uploaded artifacts at this scope.
- **No full end-user auth system** — cooks get a passwordless profile ID for favorites/history; real
  end-user accounts are a documented future improvement. (The *operator* dashboard does have a simple
  cookie-based login, since it exposes traces and cost — that's a different, internal surface.)

Every inclusion above solves a concrete problem in SousChef; every exclusion avoids complexity the
project doesn't need.
