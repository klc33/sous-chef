---
description: "Task list for 003-intelligent-behavior implementation"
---

# Tasks: Intelligent Behavior — Smart Retrieval, Freshness, Planning & Guarded Agent

**Input**: Design documents from `specs/003-intelligent-behavior/`

**Prerequisites**: [plan.md](plan.md) (required), [spec.md](spec.md) (user stories), [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/)

**Tests**: Included — the constitution makes the red-team gate, redaction gate, and unit tests for freshness/shopping-list/substitution/guardrails part of "done". Test scope is targeted, not full TDD.

**Organization**: Tasks are grouped by user story (US1, US2, US5, US3, US4 — priority order P1 → P2 → P3). The monolith tree already exists; most intelligent-layer files are **empty stubs** that these tasks fill (Phase 2 wall/view/nutrition/favorites are reused unchanged). Per the repo rule, **every function gets a comment** explaining how it works.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1/US2/US3/US4/US5; Setup/Foundational/Polish carry no story label
- Exact repo-relative file paths are given in each task

## Path Conventions

Single FastAPI monolith at repo root: `app/`, `alembic/`, `ingestion/`, `ml/`, `evals/`, `prompts/`, `tests/`. Paths are repo-relative.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm dependency grouping (no new runtime deps), add config knobs, and seed provider secrets into Vault.

- [X] T001 Verify the `backend` extra already provides every runtime dep this phase needs (`groq`, `openai`, `pgvector`, `scikit-learn`, `joblib`, `nemoguardrails`, `slowapi`) and the `ingestion`/`ml`/`evals` groups provide `openai`/`scikit-learn`/`joblib`/`pandas` — **add nothing new** unless a gap is found; if so use the grouped `uv add` and re-lock (`uv lock`). No torch in any image.
- [X] T002 [P] Extend `app/config.py` (Pydantic settings, non-secrets only) with: `embeddings_base_url`, `embeddings_model` (default `text-embedding-3-small`), `embeddings_dim` (default `1536`); `groq_model` (workflow path, default `llama-3.1-8b-instant`) + `groq_agent_model` (bounded agent, default `llama-3.3-70b-versatile`) — split so each path gets its own Groq rate-limit bucket; `agent_max_iterations` (default `5`) + `agent_token_budget`; `router_confidence_threshold` (default `0.55`); `retrieval_candidate_pool` (default `20`) — the over-fetch size for vector search before the allergen wall trims to 3. Assert `embeddings_dim` matches the migration at startup.
- [X] T003 [P] Seed provider secrets into Vault via `scripts/seed_vault.sh` (+ `make seed`): `GROQ_API_KEY` and `EMBEDDINGS_API_KEY`. `.env.example` documents only the Vault addr/token + service URLs (no keys). **NOTE:** embeddings provider is "decide later" — default is an OpenAI-compatible endpoint; confirm the provider here and seed its key before T010 runs.

---

## Phase 2: Foundational (Blocking Prerequisites — the shared turn pipeline)

**Purpose**: The substrate every user story flows through — embedding storage + adapters, vector search, the corpus embed stage, the trained classifier + serving, the router/workflow scaffolding, chat schemas, and the `/chat` endpoint wiring (input rail → router → dispatch → output rail). The Phase 2-feature wall (`constraint_guard` + `recipe_view`) is reused unchanged.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

### Schema & migration (embedding storage)

- [ ] T004 Add `embedding: Mapped[list[float] | None]` to `Recipe` in `app/models/recipe.py` using `pgvector.sqlalchemy.Vector(settings.embeddings_dim)` (nullable; reuse existing `cuisine`, `is_complete`, diet/allergen columns — no other model change).
- [ ] T005 Create migration `alembic/versions/0003_embeddings.py`: add `recipes.embedding vector(1536)` and `CREATE INDEX ix_recipes_embedding_hnsw ON recipes USING hnsw (embedding vector_cosine_ops)`; downgrade drops the index then the column. Additive + nullable → no backfill (depends T004).

### Infra adapters (hosted, mockable)

- [ ] T006 [P] Implement `app/infra/embeddings.py`: `embed_query(text) -> list[float]` and `embed_texts(texts) -> list[list[float]]` via an OpenAI-compatible client; base URL/model from config, key from Vault (`app/infra/vault.py`); batch + simple retry. Mockable for tests.
- [ ] T007 [P] Implement `app/infra/llm_groq.py`: `chat(messages, tools=None, max_tokens=None, model=None)` wrapping the Groq client with native tool/function calling; key from Vault; honors `max_tokens`; `model` defaults to `settings.groq_model` (workflow), callers pass `settings.groq_agent_model` for the agent. Mockable for tests. **Free-tier note:** retry with backoff on `429` (honor `retry-after`) so throttling surfaces as a brief wait, not a turn failure.

### Vector search + seen-history reset (repo = only DB layer)

- [ ] T008 Implement `repo/recipes.search_by_vector(session, query_vec, category, diet_flags, exclude_ids, pool)` in `app/repo/recipes.py`: one parameterized query `WHERE is_complete AND embedding IS NOT NULL AND (category = :cat OR :cat IS NULL) AND <diet flags> AND id <> ALL(:exclude_ids)` ordered by `embedding <=> :query_vec` LIMIT `pool` (the over-fetched candidate pool, `retrieval_candidate_pool` — **not** 3; allergens are trimmed afterward by the wall so the pool must be larger than the display count). ORM/parameterized only (depends T004).
- [ ] T009 [P] Add `repo/seen_history.clear(session, profile_id)` to `app/repo/seen_history.py` (delete a cook's rows, for reset-on-exhaustion); keep existing `insert`/`list`.

### Corpus embedding (offline ingestion)

- [ ] T010 Implement `ingestion/embed_recipes.py`: select complete recipes with a null/stale `embedding`, build the embed text (`"{title}. {cuisine}. {category}. {key ingredients}"`), embed via `infra.embeddings.embed_texts`, write vectors via `app/repo/recipes`. Idempotent; requires the embeddings key in Vault (T003) (depends T006, T008).
- [ ] T011 Wire the embed stage into `ingestion/run_ingest.py` **after** load and **before** the coverage report; `make ingest` now also embeds (depends T010).

### Intent classifier (the one trained model)

- [ ] T012 [P] Create `ml/data/intents_labeled.csv` (`text,label`) with ~50–100 examples per label for `find_recipe | plan_meals | nutrition_q | substitution | chitchat | out_of_scope`; stratified, no leakage.
- [ ] T013 Implement `ml/train_classifier.py`: TF-IDF (word 1–2 grams) + logistic regression → `ml/artifacts/model.joblib` + metrics; compare against a Groq LLM zero-shot baseline on macro-F1/latency/cost; write the decision + artifact SHA-256 into `app/classifier/model_card.md` (depends T012).
- [ ] T014 Implement `app/classifier/predict.py`: load `model.joblib` once (process-cached), `predict(message) -> (intent, confidence)` (depends T013).
- [ ] T015 [P] Add a `make train` target → `uv run python -m ml.train_classifier` in the `Makefile`.
- [ ] T016 [P] Populate `evals/classifier/testset.csv` (held-out intents) and set `eval_thresholds.yaml` `classifier.f1_min` to just below the achieved macro-F1 (target ≥ 0.85) — never weakened later.

### Schemas (request/response + tool inputs)

- [ ] T017 [P] Implement `app/schemas/chat.py`: `ChatRequest`, `ChatResponse`, `MealPlan`, `ShoppingList`, `SubstitutionResult` per [contracts/chat.openapi.yaml](contracts/chat.openapi.yaml).
- [ ] T018 [P] Implement `app/schemas/tools.py`: Pydantic input models for the five tools per [contracts/agent_tools.md](contracts/agent_tools.md).

### Router, workflow scaffold, guardrail base, chat endpoint

- [ ] T019 Implement `app/services/user/router.py`: `route(message) -> IntentRoute` using `classifier.predict` + `router_confidence_threshold`; map `plan_meals`/low-confidence → agent, `out_of_scope` → refuse, else workflow (per [contracts/classifier.md](contracts/classifier.md)) (depends T014).
- [ ] T020 Implement `app/services/user/workflow.py` dispatch skeleton: `handle(intent, message, cp, profile_id)` routing to handlers; implement `chitchat` + `out_of_scope` (canned safe replies) now; `find_recipe`/`nutrition_q`/`substitution` delegate to services filled in their stories (depends T019).
- [ ] T021 [P] Create `app/guardrails/input_rails.py` (`screen(message) -> GuardrailDecision`, allow-by-default for now — hardened in US5) and `app/guardrails/output_rails.py` (`screen(response)` runs `core/redaction` + re-asserts the wall on any recipe in the response). Output redaction is required regardless of US5.
- [ ] T022 Implement `app/api/user/chat.py` `POST /chat`: read profile-ID via `api/deps.py` → load `ConstraintProfile` → `input_rails.screen` → `router.route` → `workflow.handle` | `agent.loop` → `output_rails.screen` → `ChatResponse`; register the router in `app/main.py`; per-profile rate limit via `slowapi` (depends T017, T019, T020, T021).
- [ ] T023 [P] Author `prompts/router_system.md` (framing for the optional LLM routing fallback on ambiguous turns).

**Checkpoint**: `alembic upgrade head` applies `0003`; `make ingest` populates `recipes.embedding`; `make train` produces `model.joblib`; `POST /chat` boots and routes a message end-to-end (chitchat/out_of_scope answerable). User stories can begin.

---

## Phase 3: User Story 1 — Conversational ranked discovery (Priority: P1) 🎯 MVP

**Goal**: A cook types free text and gets up to 3 ranked, real, constraint-safe recipe cards.

**Independent Test**: `POST /chat {"message":"something Thai for dinner"}` returns ≤3 ranked real cards honoring category/diet/allergies; a peanut-allergic cook gets zero peanut recipes; no safe match → honest empty.

- [ ] T024 [P] [US1] Author `prompts/recipe_explainer.md`: rank/explain **only** the retrieved recipes; never invent recipes or steps.
- [ ] T025 [US1] Implement `app/services/user/rag.py` `search(query, cp, profile_id, category=None, k=3)`: `infra.embeddings.embed_query` → `repo.recipes.search_by_vector(..., pool=retrieval_candidate_pool)` → `constraint_guard.filter` (allergen wall, fail-closed) over the pool → **take top `k`=3** → `recipe_view.to_cards`; LLM ranks/phrases the (real) cards via `infra.llm_groq` + `recipe_explainer`; honest empty on no match. Over-fetching ensures 3 compliant cards surface whenever they exist (depends T006, T008, T024).
- [ ] T026 [US1] Wire `find_recipe` AND `nutrition_q` in `app/services/user/workflow.py`: `find_recipe` → `rag.search` → `ChatResponse.recipes` + grounded `reply`; `nutrition_q` (FR-034) → `rag.search(k=1)` to resolve the dish to the best-matching real recipe → `services/user/nutrition.scale` for the cook's servings → grounded `reply` (honest "couldn't find that dish" when no match). No recognized intent is left unhandled (depends T020, T025).
- [ ] T027 [P] [US1] Populate `evals/rag/golden.yaml` (query / ideal recipe id(s)); set `eval_thresholds.yaml` `rag.k: 3` (correcting the foundation-phase `5` placeholder so the gate measures **hit@3** — the 3 cards the cook actually sees) and `rag.hit_at_k_min` to just below the achieved hit@3.
- [ ] T028 [P] [US1] Unit test `tests/unit/test_rag.py` (mock embeddings + LLM): results pre-filtered by category/diet, ≤3 cards, ranked, honest empty.
- [ ] T029 [US1] Extend `tests/integration/test_chat_flow.py`: `find_recipe` turn returns ≤3 real wall-cleared cards; a peanut-allergic cook gets none; a `nutrition_q` turn (FR-034) returns the matched recipe's scaled nutrition (grounded), and an unmatched dish yields an honest "couldn't find that".
- [ ] T030 [US1] Extend `tests/integration/test_wall_regression.py` to enumerate the **rag** recipe path (a violating recipe can never be surfaced via search).

**Checkpoint**: MVP — conversational ranked discovery works end-to-end with the wall holding.

---

## Phase 4: User Story 2 — Fresh discovery on repeat (Priority: P2)

**Goal**: Repeating a request returns different recipes; the per-cook seen-history resets on exhaustion; favorites are exempt.

**Independent Test**: Issue the same request twice → zero overlapping recipe ids; keep going until the pool exhausts → history resets and results resume; a favorite is never withheld; a different profile-ID is unaffected.

- [ ] T031 [US2] Implement `app/services/user/freshness.py`: `exclude_seen(session, profile_id) -> ids`, `record_seen(session, profile_id, ids)` (never records favorites), `reset_if_exhausted(session, profile_id)` (calls `repo.seen_history.clear`) — single global per-cook set (depends T009).
- [ ] T032 [US2] Wire freshness into `app/services/user/rag.py`: pass `exclude_ids=exclude_seen(...)` to `search_by_vector`; `record_seen(...)` surfaced ids; when < k unseen compliant rows, `reset_if_exhausted` + re-query once. Favorites path stays exempt (depends T025, T031).
- [ ] T033 [P] [US2] Extend `tests/unit/test_freshness.py`: exclusion, record, reset-on-exhaustion, per-cook isolation, favorites exempt.
- [ ] T034 [US2] Extend `tests/integration/test_chat_flow.py`: same query twice → zero overlap; second profile-ID unaffected by the first's history.

**Checkpoint**: Discovery stays fresh across repeats; US1 + US2 both work independently.

---

## Phase 5: User Story 5 — Refuse manipulation (Priority: P2)

**Goal**: Injection, jailbreak, and allergen-override attempts are refused with a safe message; no unsafe content leaks. (Closes the constitution's red-team gate.)

**Independent Test**: A battery of allergen-override + injection/jailbreak probes each return `refused=true` with no violating recipe / no instruction-abandonment; an injection embedded in a valid request is neutralized while the safe remainder is served.

- [ ] T035 [US5] Implement refusal logic in `app/guardrails/input_rails.py`: deterministic patterns for jailbreak/role-override/system-prompt-leak and allergen/diet-override phrasing → refuse with a safe message; pass through the safe remainder of an otherwise-valid request (hardens T021).
- [ ] T036 [US5] Harden `app/guardrails/output_rails.py`: PII leak check + `core/redaction` + re-assert the wall on every recipe in the response (drop any violator) before the reply leaves and before any Phoenix span (depends T021).
- [ ] T037 [US5] Wire refusal into `app/api/user/chat.py`: a refused input short-circuits before routing → `ChatResponse(refused=true, ...)`; `out_of_scope` returns a safe redirect (depends T022, T035).
- [ ] T038 [P] [US5] Populate `evals/redteam/attempts.yaml` with allergen-override + injection/jailbreak probes and set `eval_thresholds.yaml` `redteam.refusal_rate_min: 1.0`.
- [ ] T039 [P] [US5] Implement `tests/unit/test_guardrails.py`: each probe refused; injection-in-valid-request neutralized with safe remainder served.
- [ ] T040 [US5] Confirm `tests/redteam/test_attempts.py` drives `evals/redteam/attempts.yaml` and passes at refusal rate 1.0 (depends T038).

**Checkpoint**: The hard safety gate is green; manipulation is refused on every path.

---

## Phase 6: User Story 3 — Varied meal plan + one scaled shopping list (Priority: P3)

**Goal**: A multi-day plan spanning ≥3 distinct cuisines, all constraint-safe, with exactly one consolidated, deduplicated, serving-scaled shopping list — produced by the bounded agent.

**Independent Test**: `POST /chat {"message":"plan 3 days of dinners"}` → `meal_plan.distinct_cuisines >= 3`, every recipe safe, one `shopping_list` deduped + scaled; shortfall noted when the corpus can't supply variety; the agent always stays within its bounds.

- [ ] T041 [P] [US3] Author `prompts/agent_system.md`: bounded tool-use rules; act only through tools; never invent recipes/steps.
- [ ] T042 [US3] Implement `app/agent/tools.py` tools `search_recipes`, `get_recipe`, `get_nutrition`, `build_shopping_list`: each validates its `schemas/tools.py` input, calls the matching service, and wall-clears any recipe output via `recipe_view` (depends T018, T025).
- [ ] T043 [US3] Implement `app/agent/loop.py`: bounded tool-calling loop via `infra.llm_groq.chat(tools=..., model=settings.groq_agent_model)` (the stronger model for reliable multi-tool calling); cap `agent_max_iterations` + token budget; return best safe partial (or honest failure) on bound (depends T007, T041, T042).
- [ ] T044 [US3] Implement `app/services/user/shopping_list.py`: aggregate ingredients across plan recipes, name-normalize to dedupe, merge compatible units (mass/volume/count families), scale to the cook's servings, emit incompatible-unit duplicates as separate labeled lines; exactly one list.
- [ ] T045 [US3] Implement `app/services/user/meal_plan.py`: build an N-day plan (default 3) maximizing distinct **known** cuisines (≥3 when the compliant corpus allows; `cuisine IS NULL` never counts), wall + freshness applied, `shortfall_note` when length/variety can't be met, then one `shopping_list` (depends T043, T044).
- [ ] T046 [US3] Wire `plan_meals` → `meal_plan` via `router`/`workflow`/`chat`; populate `ChatResponse.meal_plan` + `shopping_list` (depends T019, T045).
- [ ] T047 [P] [US3] Populate `evals/agent_tool_selection/cases.yaml` (message → expected tool(s)) and confirm it runs in `evals/run_evals.py`.
- [ ] T048 [P] [US3] Extend `tests/unit/test_shopping_list.py`: dedupe + compatible-unit merge + scaling + incompatible-unit split + exactly one list.
- [ ] T049 [P] [US3] Unit test `tests/unit/test_meal_plan.py`: ≥3 distinct cuisines when possible; unknown-cuisine not counted; shortfall note; all recipes wall-safe.
- [ ] T050 [US3] Extend `tests/integration/test_chat_flow.py`: a 3-day plan returns ≥3 cuisines, all safe, one scaled deduped list.
- [ ] T051 [US3] Extend `tests/integration/test_wall_regression.py` to enumerate the **agent tool** and **meal_plan** recipe paths.

**Checkpoint**: Meal planning + shopping list work via the bounded agent; the wall holds on the agent paths.

---

## Phase 7: User Story 4 — Allergen-safe ingredient substitution (Priority: P3)

**Goal**: Substitution suggestions from a curated map that never introduce a declared allergen.

**Independent Test**: `POST /chat {"message":"what can I use instead of butter?"}` → plausible replacements, none containing/may-containing a declared allergen; `none_safe=true` with an honest message when nothing is safe; suggestions are curated (never invented).

- [ ] T052 [P] [US4] Create `app/services/shared/substitutions_data.py`: curated `ingredient → [substitute]` map, each substitute annotated with the allergens it introduces.
- [ ] T053 [US4] Implement `app/services/user/substitution.py`: look up the curated map and filter out any substitute that contains/may-contain a declared allergen (fail-closed); return safe list or `none_safe=true` (depends T052).
- [ ] T054 [US4] Add the `substitute_ingredient` tool to `app/agent/tools.py` (schema-validated → `substitution` service) so the agent can substitute within a plan (depends T042, T053).
- [ ] T055 [US4] Wire the `substitution` route in `app/services/user/workflow.py` → `substitution` service; populate `ChatResponse.substitution`; author `prompts/substitution.md` (phrasing of the curated result only) (depends T020, T053).
- [ ] T056 [P] [US4] Implement `tests/unit/test_substitution.py`: never emits a declared allergen; honest `none_safe`; curated-only (no invention).

**Checkpoint**: All five user stories independently functional.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Finalize gates, docs, and full-stack verification (the constitution's definition of done).

- [ ] T057 [P] Pin final `eval_thresholds.yaml` values (`classifier.f1_min`, `rag.hit_at_k_min`) just below achieved scores; confirm `redteam.refusal_rate_min: 1.0` and `redaction.leak_count_max: 0`. Never weaken to pass.
- [ ] T058 [P] Update `docs/DECISIONS.md` (ML-vs-LLM router decision with the real macro-F1/latency/cost numbers), `docs/SECURITY.md` (guardrails + wall on the new paths), and `docs/EVALS.md` (classifier/rag/agent/redteam suites + numbers).
- [ ] T059 [P] Ensure every new function carries an explanatory comment; run `make lint` (ruff + mypy) clean across the new modules.
- [ ] T060 Run `make test && make evals` all green (incl. red-team + redaction gates); then `make up` and walk [quickstart.md](quickstart.md) stories 1–5 against the live stack.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately.
- **Foundational (Phase 2)**: Depends on Setup — **BLOCKS all user stories**.
- **User Stories (Phases 3–7)**: All depend on Foundational. Recommended order P1 → P2 → P2 → P3 → P3:
  - US1 (P1, MVP) → US2 (P2, builds on rag) → US5 (P2, guardrails) → US3 (P3, agent) → US4 (P3, substitution).
- **Polish (Phase 8)**: Depends on all desired stories being complete.

### User Story Dependencies

- **US1 (P1)**: After Foundational. No dependency on other stories.
- **US2 (P2)**: After Foundational. Extends `rag.py` from US1 (freshness wraps US1's search) — sequence US1 → US2.
- **US5 (P2)**: After Foundational. Independent of US1–US4 (hardens the rails created in T021); can run in parallel with US1/US2.
- **US3 (P3)**: After Foundational. Uses `rag`/`search_recipes`; agent registers 4 tools (substitute added in US4). Independent of US2/US5.
- **US4 (P3)**: After Foundational. Standalone via the workflow route; also registers the 5th agent tool (light touch on US3's `tools.py`).

### Within Each User Story

- Prompts/data before the service that uses them; services before the workflow/agent wiring; wiring before integration tests.
- Models before repos before services before endpoints.

### Parallel Opportunities

- Setup: T002, T003 in parallel (after T001).
- Foundational: the infra adapters (T006, T007), schemas (T017, T018), classifier dataset (T012), and seen-history clear (T009) are parallel; T021/T023 parallel. Migration (T004→T005) and vector search (T008) gate the retrieval tasks.
- Once Foundational completes, **US1/US5 can run in parallel**; US2 follows US1; US3/US4 can run in parallel (mind the shared `tools.py` touch in T054).
- Within stories, [P] tasks (separate test files, prompts, eval files) run in parallel.

---

## Parallel Example: Foundational infra + schemas

```bash
# After the migration pair (T004→T005), launch the independent adapters/schemas together:
Task: "Implement app/infra/embeddings.py (T006)"
Task: "Implement app/infra/llm_groq.py (T007)"
Task: "Implement app/schemas/chat.py (T017)"
Task: "Implement app/schemas/tools.py (T018)"
Task: "Create ml/data/intents_labeled.csv (T012)"
```

## Parallel Example: User Story 1 tests

```bash
Task: "Unit test app/services/user/rag.py in tests/unit/test_rag.py (T028)"
Task: "Populate evals/rag/golden.yaml + set rag.hit_at_k_min (T027)"
Task: "Author prompts/recipe_explainer.md (T024)"
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1: Setup.
2. Phase 2: Foundational (CRITICAL — the whole turn pipeline; blocks all stories).
3. Phase 3: US1 — conversational ranked discovery.
4. **STOP and VALIDATE**: quickstart Story 1; the wall holds; results are real and ranked.
5. Demo the MVP.

### Incremental Delivery

1. Setup + Foundational → pipeline boots and routes.
2. US1 → ranked discovery (MVP) → demo.
3. US2 → freshness on repeat → demo.
4. US5 → manipulation refused (red-team gate green) → demo.
5. US3 → meal plan + shopping list (the agent) → demo.
6. US4 → allergen-safe substitution → demo.
7. Polish → finalize gates + docs + full-stack quickstart.

### Safety-first note

The deterministic wall (`constraint_guard` via `recipe_view`) protects allergies on every path from US1 onward — it does **not** wait for US5. US5 adds injection/jailbreak refusal and output-rail hardening. "Done" (constitution) requires the red-team + redaction gates green, i.e. through US5 and Phase 8.

---

## Notes

- [P] = different files, no dependency on an incomplete task.
- [Story] label maps each task to its user story for traceability.
- Every new function gets a comment explaining how it works (repo rule).
- Verify targeted tests fail before implementing the behavior they cover.
- Commit after each task or logical group; keep the wall-regression test green at every step.
- Hosted inference only; classifier serves via joblib (no torch); secrets from Vault; never weaken a threshold to pass a gate.
