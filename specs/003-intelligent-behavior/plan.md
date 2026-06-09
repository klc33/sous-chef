# Implementation Plan: Intelligent Behavior — Smart Retrieval, Freshness, Planning & Guarded Agent

**Branch**: `003-intelligent-behavior` | **Date**: 2026-06-09 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/003-intelligent-behavior/spec.md`

## Summary

Layer Sous-Chef's *intelligence* on top of the Phase 2 corpus + wall. A cook types free text and the
turn flows exactly as the constitution prescribes:
`guardrails input rail → intent classifier (router) → easy: workflow | hard: bounded agent → constraint guard → guardrails output rail`.

Concretely this phase adds: **semantic retrieval** (embed query → pgvector search pre-filtered by
category/diet → wall → ranked cards), **freshness** (a single per-cook seen-history that excludes
already-shown recipes and resets on exhaustion), the **trained intent classifier** (offline scikit-learn
TF-IDF + logistic regression, served via `joblib`) that routes each message, the **deterministic
workflow** for easy intents, the **one bounded tool-calling agent** (five schema-validated tools) for
hard/multi-step intents, the **meal-plan + consolidated shopping list**, **curated allergen-safe
substitutions**, and the **input/output guardrails** that refuse injection/jailbreak/allergen-override.

Technical approach: reuse the Phase 2 wall choke point unchanged — every recipe that reaches a cook on
every new path (search results, agent tool outputs, meal plans) is rendered only through
`services/shared/recipe_view.py`, which requires a `ConstraintProfile` and calls
`services/user/constraint_guard.py`. Add an `embedding` vector column to `recipes` (pgvector, already
enabled) via Alembic `0003`, embed recipes in the offline ingestion pipeline, and add a
`repo/recipes.search_by_vector(...)` that does cosine search with **category + diet + seen-history
pre-filtering in SQL**. The LLM (Groq, chat + agent reasoning) and embeddings (separate hosted provider)
are hosted-API calls behind `infra/` adapters; the classifier is the only model we train, and it serves
lean (no torch). Guardrails screen input before routing and output before the reply leaves; the wall is
re-asserted on the output path as defense in depth. No new runtime dependencies — every library this
phase needs is already in the `backend` extra / `ml` / `ingestion` / `evals` groups.

## Technical Context

**Language/Version**: Python 3.12 (`requires-python >= 3.11`); image base `python:3.12-slim`. Classifier
trained offline (notebook/Colab or `ml/train_classifier.py`), served in-process with scikit-learn +
joblib — **no torch in any image**.

**Primary Dependencies**: All already present (no `uv add` expected):
- Runtime (`backend` extra): `groq` (LLM chat + tool calling), `openai` (embeddings via the separate
  hosted provider — Groq is chat-only), `pgvector` (vector column + search), `scikit-learn` + `joblib`
  (serve the classifier), `nemoguardrails` (rails) / Presidio (already wired for redaction), `slowapi`
  (rate limit), SQLAlchemy/Alembic/`psycopg`.
- Offline `ingestion` group: `openai` (embed recipes), `httpx`. Offline `ml` group: `scikit-learn`,
  `joblib`, `pandas`. `evals` group: `scikit-learn`, `pandas`, `pytest` (+ `ragas` available if needed).

**Storage**: PostgreSQL 16 + pgvector (already enabled in `0001_baseline`). New: one Alembic migration
`0003_embeddings` adds `recipes.embedding vector(N)` (N from the chosen embedding model, see research) and
an ANN index. The existing `seen_history` table (created inert in `0002`) is now wired live. `recipes.cuisine`
already exists (nullable → "unknown"). Redis used for per-turn rate limiting / optional session memory.

**Testing**: pytest. Unit: `freshness` (exclusion + reset, per-cook isolation), `shopping_list`
(dedupe + unit-compatible merge + scaling), `router`/classifier serving (label + confidence), curated
`substitution` (never emits a declared allergen), guardrails (refuse injection/jailbreak/override).
Integration: `test_chat_flow` (search → detail → plan → list end-to-end with the wall holding), the
existing `test_wall_regression` extended to the new recipe paths. Red-team: `tests/redteam/test_attempts.py`
drives `evals/redteam/attempts.yaml` (the hard gate). Eval gates: classifier macro-F1, RAG hit@k,
red-team refusal rate = 1.0.

**Target Platform**: Linux containers via docker-compose locally; Railway for the deployed backend.
Embedding of the corpus runs in the offline `make ingest` job; classifier training runs in `make train`
(offline) → `ml/artifacts/model.joblib` (SHA-pinned).

**Project Type**: Single FastAPI monolith (per `projectplanFolderForMd/structure.md`); the classifier,
guardrails, and agent are in-process modules, not separate services.

**Performance Goals**: Classifier routing < ~50 ms (local joblib predict, keeps the LLM off easy turns).
A conversational search returns within a few seconds p95 (dominated by one embedding call + pgvector
query + one LLM ranking/explanation call); the agent/meal-plan path is multi-tool and longer (target
~15–20 s) but always terminates within its bounds. These are soft targets — no hard latency CI gate
(hosted-API round-trips dominate); the committed gates are correctness/safety/quality (below).

**Constraints**: The wall is deterministic code on EVERY new output path (SC-006) via the single
`recipe_view`→`constraint_guard` choke point; grounding — cards/plans/steps come only from stored rows,
substitutions only from a curated map, never invented (SC-005); same request twice → zero overlap until
pool exhaustion (SC-001); meal plan ≥3 distinct cuisines, all safe, exactly one scaled deduped list
(SC-002); 100% of red-team probes refused (SC-003); agent bounded in iterations + tokens, schema-validated
tool inputs (SC-007); hosted inference only; classifier served via joblib (no torch); profile-ID from
header only.

**Scale/Scope**: ~hundreds–2,000 recipes (corpus from Phase 2). New code: 1 migration; embeddings adapter
+ Groq adapter; 1 ingestion embed stage; vector search repo helper; 6 user services wired
(rag, freshness, router, workflow, meal_plan, shopping_list) + 1 new `substitution` service; the agent
(loop + 5 tools); classifier serving + offline training + dataset; 2 guardrail modules; the `/chat`
endpoint + chat/tools schemas; 4 eval suites populated + 3 thresholds set.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | How this plan complies |
|---|---|---|
| I Simplicity | PASS | Vectors in pgvector (a column on `recipes`), not a vector DB; exactly one agent; routing is a small trained model + deterministic workflow; one migration; reuse the Phase 2 wall/view/repo unchanged. |
| II Build only required | PASS | Only the spec's five stories. Each new module traces to an FR; no speculative features (no conversation memory beyond seen-history, no ratings — listed Future in spec). |
| III Separation of concerns | PASS | `api → services → repo → infra` strict; vector search lives in `repo/recipes.py` (only DB layer); embeddings/Groq behind `infra/` adapters (mockable); agent/classifier/guardrails are in-process packages; services split under `services/user/` + `services/shared/`. |
| IV Testability | PASS | Freshness, shopping-list math, substitution safety, guardrails unit-tested; classifier macro-F1, RAG hit@k, red-team refusal=1.0 are committed CI gates; adapters mocked in tests; the wall regression extended to every new recipe path. |
| V Reproducibility | PASS | Embedding column + index via Alembic `0003`; ingestion embed stage idempotent (re-embeds by `(source, source_id)`); classifier artifact SHA-pinned + F1 gate; deps pinned in `uv.lock`; thresholds committed. |
| VI Security & privacy | PASS | Input rail screens untrusted chat BEFORE routing; output rail + redaction before the reply and before any Phoenix span; ORM/parameterized vector queries (injection-safe); agent loop bounded (iterations + tokens); every tool input Pydantic-validated; profile-ID from header. |
| VII Maintainability | PASS | Small single-purpose files matching `structure.md`; prompts in `prompts/` (never inline); every function commented; lint + mypy. |
| VIII Documentation-first | PASS | This plan + research/data-model/contracts/quickstart precede code; classifier `model_card.md` records the ML-vs-LLM decision on a real number. |
| IX Spec-driven | PASS | Generated through the SpecKit cycle; artifacts committed on-branch; clarifications already resolved. |
| X No unnecessary tech | PASS | **No new runtime deps** — all needed libs already grouped; no torch/transformers in any image; no separate vector store; no auth system; curated substitution map instead of an extra model. |

**Safety invariants**:
- **The wall is the grade** — unchanged single choke point: agent tools, RAG, and meal-plan all emit
  recipes only through `recipe_view`(→`constraint_guard`). The wall-regression test is extended to
  enumerate the new paths; allergen-override is refused by the input rail *and* can never pass the wall.
- **Ground everything** — RAG returns only stored rows; the LLM ranks/explains real retrieved recipes and
  never invents; detail steps render verbatim; substitutions come only from a curated map; no safe match
  → honest empty result.
- **Hosted inference only / lean serving** — LLM + embeddings are hosted-API calls behind `infra/`; the
  classifier is the only trained model and serves via `joblib` (scikit-learn + numpy), no torch.

**Result**: PASS — no violations; Complexity Tracking intentionally empty.

## Project Structure

### Documentation (this feature)

```text
specs/003-intelligent-behavior/
├── plan.md              # This file
├── research.md          # Phase 0 output — resolves the decisions below
├── data-model.md        # Phase 1 output — embedding column, seen-history live, intent/plan/list entities
├── quickstart.md        # Phase 1 output — runnable end-to-end validation of the five stories
├── contracts/
│   ├── chat.openapi.yaml      # POST /chat — the turn endpoint (request/response)
│   ├── agent_tools.md         # the five agent tool input/output schemas (internal contract)
│   └── classifier.md          # intent label set + routing contract + F1 gate
└── checklists/
    └── requirements.md  # spec quality checklist (from /speckit-specify)
```

### Source Code (repository root)

Fills the remaining `structure.md` placeholders left empty after Phase 2. Phase 2 files
(`constraint_guard`, `recipe_view`, `nutrition`, `favorites`, recipe/profile/favorites APIs, ingestion
fetch/categorize/extract/nutrition/allergens/load) are reused **unchanged** unless noted.

```text
app/
├── models/
│   └── recipe.py                 # + `embedding: Mapped[list[float] | None]` (pgvector Vector(N)); cuisine already present
├── repo/
│   ├── recipes.py                # + search_by_vector(session, embedding, category, diet_flags, exclude_ids, pool): SQL cosine search, category/diet/seen pre-filtered, returns an OVER-FETCHED pool
│   └── seen_history.py           # REUSED (now wired live): insert(...), list(...), + clear(profile_id) for reset
├── infra/
│   ├── embeddings.py             # NEW IMPL: embed_query(text)->vec, embed_texts(list)->[vec] (separate hosted provider; dim from config)
│   └── llm_groq.py               # NEW IMPL: chat(messages, tools?, max_tokens) with native tool-calling; bounded
├── classifier/
│   └── predict.py                # NEW IMPL: load model.joblib (cached), predict(message)->(intent, confidence)
├── services/
│   ├── user/
│   │   ├── rag.py                # NEW: search(query, cp, profile_id, category, k=3) → embed → repo over-fetch pool → allergen wall (constraint_guard) → top-3 cards; records seen; honest empty
│   │   ├── freshness.py          # NEW: exclude_seen / record_seen / reset_if_exhausted (single global per-cook set; favorites exempt)
│   │   ├── router.py             # NEW: classify(message) → route easy→workflow / hard→agent / out_of_scope→refusal
│   │   ├── workflow.py           # NEW: deterministic handlers for find_recipe, nutrition_q, substitution, chitchat
│   │   ├── meal_plan.py          # NEW: drive the agent (or deterministic planner) → ≥3-cuisine safe plan → shopping_list
│   │   ├── shopping_list.py      # NEW: aggregate+dedupe ingredients across plan, merge compatible units, scale to servings
│   │   └── substitution.py       # NEW: curated ingredient→substitutes map, wall-filtered; honest "no safe substitute"
│   └── shared/
│       ├── recipe_view.py        # REUSED unchanged — the wall choke point for all new paths
│       └── substitutions_data.py # NEW: the curated substitution map (data; grounded, not generated)
├── agent/
│   ├── loop.py                   # NEW: bounded tool-calling loop (cap iterations + tokens); best safe partial on bound
│   └── tools.py                  # NEW: search_recipes, get_recipe, get_nutrition, build_shopping_list, substitute_ingredient (each schema-checked → service)
├── guardrails/
│   ├── input_rails.py            # NEW: refuse prompt-injection / jailbreak / allergen-override BEFORE routing
│   └── output_rails.py           # NEW: leak check + redaction before reply leaves; re-assert wall on any recipe in the response
├── schemas/
│   ├── chat.py                   # NEW: ChatRequest(message, category?), ChatResponse(reply, recipes[], meal_plan?, shopping_list?, intent, refused)
│   └── tools.py                  # NEW: Pydantic input models for each of the five tools
└── api/
    └── user/
        └── chat.py               # NEW: POST /chat — input rail → router → workflow|agent → (wall via views) → output rail

prompts/                          # AUTHORED this phase (files exist; fill them)
├── router_system.md              # framing for ambiguous routing (optional LLM fallback)
├── agent_system.md               # agent system prompt + tool-use rules (bounded, grounded)
├── recipe_explainer.md           # how the LLM ranks/explains ONLY retrieved recipes (never invents)
└── substitution.md               # (optional) phrasing around the curated substitution result

alembic/versions/0003_embeddings.py   # add recipes.embedding vector(N) + ANN index

ingestion/
└── embed_recipes.py              # NEW IMPL: embed each recipe's text via infra.embeddings → store vector; idempotent; wired into run_ingest.py

ml/
├── data/intents_labeled.csv      # NEW: labeled intents (find_recipe|plan_meals|nutrition_q|substitution|chitchat|out_of_scope), held-out split
├── train_classifier.py           # NEW IMPL: TF-IDF + logistic regression → model.joblib + metrics; compare vs LLM zero-shot baseline
└── (app/classifier/model_card.md)# decision record: ML vs LLM on macro-F1/latency/cost; artifact SHA-256

evals/
├── classifier/testset.csv        # POPULATE: held-out intents → macro-F1 gate
├── rag/golden.yaml               # POPULATE: query / ideal recipe(s) → hit@k gate
├── agent_tool_selection/cases.yaml # POPULATE: message → expected tool(s)
└── redteam/attempts.yaml         # POPULATE: allergen-override + injection/jailbreak probes (ALL must be refused)

eval_thresholds.yaml              # SET: classifier.f1_min, rag.hit_at_k_min, redteam.refusal_rate_min = 1.0

tests/
├── unit/
│   ├── test_freshness.py         # EXTEND: exclusion + reset + per-cook isolation + favorites exempt
│   ├── test_shopping_list.py     # EXTEND: dedupe + compatible-unit merge + scaling + incompatible-units split
│   ├── test_router.py            # NEW: classifier serving → correct route; confidence escalation
│   ├── test_substitution.py      # NEW: never emits a declared allergen; honest empty
│   └── test_guardrails.py        # NEW: injection/jailbreak/override refused; safe remainder served
├── integration/
│   ├── test_chat_flow.py         # EXTEND: search → detail → plan → list; wall holds end-to-end; freshness across repeats
│   └── test_wall_regression.py   # EXTEND: enumerate new recipe paths (rag, agent tools, meal_plan)
└── redteam/
    └── test_attempts.py          # REUSED: drives evals/redteam/attempts.yaml (hard gate, refusal=1.0)
```

**Structure Decision**: Single FastAPI monolith exactly as `projectplanFolderForMd/structure.md`. Two
decisive choices carry the phase:

1. **The wall choke point is reused, not reinvented.** Every new recipe surface (RAG, each agent tool that
   returns recipes, the meal plan) produces cook-facing DTOs *only* through `recipe_view.to_cards/to_detail`,
   which require a `ConstraintProfile` and call `constraint_guard`. The Phase 2 `test_wall_regression` is
   extended to enumerate the new paths, so adding an intelligent path that forgets the wall fails CI. The
   output rail re-asserts the wall as belt-and-suspenders.

2. **Retrieval over-fetches, the wall trims, the LLM only explains.** `search_by_vector` applies category
   + diet + seen-history as SQL `WHERE` clauses and orders by cosine distance, but returns an
   **over-fetched candidate pool** (config `retrieval_candidate_pool`, default ~20), *not* the final 3.
   `rag` then applies the allergen wall (`constraint_guard`, fail-closed) over that pool and selects the
   **top 3** to display — so we still surface 3 compliant cards whenever they exist (allergen filtering
   after a hard `LIMIT 3` could under-return). The Groq LLM orders/explains *only* those real rows
   (grounding). Freshness is one global per-cook seen-set; when the pool yields fewer than `k` unseen
   compliant rows, `freshness.reset_if_exhausted` clears that cook's history and re-queries so results keep
   flowing (favorites are never added to / suppressed by seen-history). RAG quality is gated as **hit@3**
   (what the cook actually sees), reconciled with the over-fetch pool.

## Complexity Tracking

> No constitution violations; this section is intentionally empty.
