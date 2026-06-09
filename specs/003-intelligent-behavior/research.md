# Phase 0 Research: Intelligent Behavior

Resolves every open decision before design. Each entry: **Decision / Rationale / Alternatives**. All
choices respect the constitution (hosted inference only, lean serving, one agent, no new vector store,
no torch) and reuse Phase 2 where possible.

## 1. Embedding provider, model, dimension & distance

**Decision**: Use the hosted **OpenAI-compatible embeddings provider** (the `openai` dep already in the
`backend` extra and `ingestion` group — Groq is chat-only) with model **`text-embedding-3-small`
(1536 dims)**. Store as a single `recipes.embedding vector(1536)` column (pgvector). Distance =
**cosine** (`vector_cosine_ops`). Make the provider base URL, model name, and dimension **config-driven**
(`app/config.py`) so the provider can be swapped without code changes; the dimension is pinned in the
migration and asserted at startup against config.

**Rationale**: 1536-d small model is cheap, fast, and strong enough for ≤2,000 recipes; cosine is the
standard for normalized text embeddings and is what pgvector's `<=>` operator supports directly. A column
on `recipes` (vs a side table) is the simplest shape (Principle I) since every surfaceable recipe gets
exactly one vector and we already own the row.

**Alternatives considered**: separate `recipe_embeddings` table (rejected — extra join/repo for a 1:1
relationship, no benefit at this scale); larger 3072-d model (rejected — more storage/latency for no
measurable recall gain on a small corpus); a dedicated vector DB (rejected by constitution — pgvector is
mandated).

## 2. ANN index type & build

**Decision**: Add an **HNSW** index `ON recipes USING hnsw (embedding vector_cosine_ops)` in `0003`. The
SQL pre-filter (category/diet/seen) is applied alongside the ANN order-by; at ≤2,000 rows the planner may
even prefer an exact scan, which is acceptable. Set `hnsw.ef_search` modestly via session GUC if tuning
is needed.

**Rationale**: HNSW gives good recall without the IVFFlat requirement to pre-populate before building a
list. At this corpus size recall is effectively exact; the index mostly future-proofs growth.

**Alternatives considered**: IVFFlat (rejected — needs data present at build time and `lists` tuning for a
tiny corpus); no index / brute force (acceptable functionally but the index is cheap and keeps the query
plan stable as the corpus grows).

## 3. Embedding text & ingestion wiring

**Decision**: Embed a compact, deterministic text per recipe: **`"{title}. {cuisine}. {category}.
{first N ingredient names}"`** (title weighted by position first). `ingestion/embed_recipes.py` batches
recipes lacking an up-to-date embedding, calls `infra.embeddings.embed_texts`, and writes vectors via
`repo.recipes`. It is **idempotent**: re-embeds keyed on `(source, source_id)` and only when the
embedding is null or the source text changed (store nothing extra — re-embed all on demand is fine at
this scale). Wired as a stage in `ingestion/run_ingest.py` **after** load, **before** the coverage report.

**Rationale**: Including cuisine + category + key ingredients makes semantic queries like "something Thai"
or "a light vegan breakfast" land on the right rows while staying grounded in stored fields. Idempotency
preserves Principle V (reproducible corpus rebuild).

**Alternatives considered**: embed full step text (rejected — noisy, dilutes the dish signal, costs more);
embed at request time (rejected — recipes are static; precompute once at ingestion).

## 4. Retrieval pre-filtering & ranking (RAG)

**Decision**: `repo.recipes.search_by_vector(session, query_vec, category, diet_flags, exclude_ids, pool)`
issues a single parameterized SQL query: `WHERE is_complete AND embedding IS NOT NULL AND
(category = :cat OR :cat IS NULL) AND <diet flags> AND id <> ALL(:exclude_ids)` ordered by
`embedding <=> :query_vec` limited to an **over-fetched candidate pool** (`pool` =
`retrieval_candidate_pool`, default ~20) — *not* the final 3. `services/user/rag.py` then applies the
**allergen wall** (`constraint_guard.filter`, fail-closed on `allergen_certain`) over that pool and
selects the **top 3** (the clarified results-per-search count `k=3`) to build cards via
`recipe_view.to_cards`. The **LLM ranks/explains only these real rows** (it may reorder for relevance and
write the reply, but cannot add recipes) per `prompts/recipe_explainer.md`.

**Rationale**: Category/diet/seen are cheap, exact SQL predicates and go in the `WHERE`. Allergen exclusion
keeps its fail-closed `allergen_certain` logic in `constraint_guard` (the single auditable wall), so it
runs **after** retrieval — which means retrieval must **over-fetch** (a pool, not a hard `LIMIT 3`) or it
could return fewer than 3 compliant cards when compliant ones exist deeper in the ranking. The pool (~20)
is tiny at this corpus size, so the extra vector work is negligible. Diet flags map to the existing
`is_vegetarian/is_vegan/is_pescatarian` columns.

**RAG eval (k reconciliation)**: quality is gated as **hit@3** — does an ideal recipe appear in the 3 the
cook actually sees — computed over the post-wall top-3 (set `eval_thresholds.yaml rag.k: 3`). The leftover
`rag.k: 5` placeholder from the foundation phase is corrected to 3.

**Alternatives considered**: hard `LIMIT 3` then filter allergens in Python (rejected — under-returns,
the original analyze finding I1); push allergen array-overlap into SQL too (viable, but splits the wall
across SQL + `constraint_guard` and complicates the fail-closed `allergen_certain` rule — keep one
auditable wall and over-fetch instead); let the LLM pick from a large set (rejected — un-grounded, costlier).

## 5. Freshness model (seen-history) & reset

**Decision**: A **single global seen-set per cook (profile-ID)** stored in the existing `seen_history`
table (`profile_id, recipe_id, shown_at`). On each retrieval, exclude the cook's seen `recipe_id`s in SQL.
After building results, **record** the returned recipe ids as seen. **Reset on exhaustion**: if a query
returns fewer than `k` unseen compliant rows, `freshness.reset_if_exhausted` clears that cook's
seen-history (`repo.seen_history.clear(profile_id)`) and re-queries once, so the cook keeps getting
results instead of an empty list. **Favorites are exempt** — they are never written to seen-history and
the favorites path never applies the exclusion.

**Rationale**: Matches the clarified decision and CLAUDE.md's "a profile's seen-history". Global scope is
the simplest correct model (Principle I) and needs no query-normalization. Reset-on-exhaustion keeps
discovery alive without an empty dead-end.

**Alternatives considered**: per-query-signature history (rejected in clarification — more state +
normalization); time-window expiry (rejected — adds a tuning knob with no requirement behind it,
Principle X).

## 6. Intent classifier: algorithm, labels, data, baseline & gate

**Decision**: **TF-IDF (word 1–2 grams) + multinomial logistic regression** (scikit-learn), trained
offline by `ml/train_classifier.py`, exported to `ml/artifacts/model.joblib`, served by
`app/classifier/predict.py` (model loaded once, cached). **Six labels**:
`find_recipe | plan_meals | nutrition_q | substitution | chitchat | out_of_scope`. Build
`ml/data/intents_labeled.csv` as a hand-authored + lightly-augmented set (~50–100 examples/label) with a
stratified held-out split (no leakage). The notebook/training script **compares the classical model to a
Groq LLM zero-shot baseline** on macro-F1, latency, and cost, and records the decision in
`app/classifier/model_card.md` with the artifact SHA-256. **Gate**: set `classifier.f1_min` in
`eval_thresholds.yaml` to a defensible floor (target macro-F1 ≥ **0.85**; final value set to just below
the achieved held-out score, never weakened later).

**Routing of labels**: `find_recipe`, `nutrition_q`, `substitution`, `chitchat` → deterministic
**workflow**; `plan_meals` → **agent**; `out_of_scope` → safe canned refusal/redirect. **Low confidence**
(below a configured threshold, e.g. < 0.55) or `plan_meals`-adjacent ambiguity → escalate to the agent
(the safe, more capable path).

**Rationale**: This is the one model the project is graded on building; classical ML is fast, deterministic,
explainable, and torch-free to serve (model_role.md, Principle on lean serving). Confidence-based
escalation means misrouting degrades cost/quality, never safety (FR-004).

**Alternatives considered**: LLM-only routing (rejected — defeats the cost-control purpose and is
non-deterministic); SVM / linear models (viable; logistic regression chosen for calibrated
probabilities used by the confidence threshold).

## 7. Bounded tool-calling agent

**Decision**: One agent in `app/agent/loop.py` driving **Groq native function/tool calling** via
`infra.llm_groq.chat(messages, tools, max_tokens)`. Bounds: **max_iterations** (default 5) and a
**token budget** (per-call `max_tokens` + cumulative cap), both config-driven. The five tools in
`app/agent/tools.py` — `search_recipes`, `get_recipe`, `get_nutrition`, `build_shopping_list`,
`substitute_ingredient` — each validate input against a Pydantic model in `app/schemas/tools.py`, call the
matching service, and return structured results; any recipe-bearing result is wall-cleared via
`recipe_view`. On hitting a bound, the loop returns the **best safe partial result** (or an honest
failure), never loops unbounded. System prompt in `prompts/agent_system.md`.

**Rationale**: Native tool calling is the production-honest pattern (model_role.md); Pydantic validation +
bounded loop satisfy FR-025–028 and Principle VI. The agent acts *only* through tools, so every action is
auditable and wall-governed.

**Alternatives considered**: a heavyweight agent framework (rejected — Principle I/X; one bounded loop is
enough); unbounded ReAct text parsing (rejected — fragile, unsafe without hard caps).

## 8. Meal plan: cuisine variety algorithm

**Decision**: `services/user/meal_plan.py` produces an N-day plan (default **3 days** when unspecified). It
retrieves compliant candidates (wall + freshness) and selects to **maximize distinct known cuisines**,
guaranteeing **≥3 distinct cuisines** when the compliant corpus allows. Recipes with `cuisine IS NULL`
are eligible but **count as "unknown" and never satisfy a distinct-cuisine slot**. If ≥3 distinct cuisines
or the full length can't be met, return the maximum safe variety and a **shortfall note** (FR-017). The
plan is assembled via the agent's tools on the `plan_meals` route (the agent calls `search_recipes` per
cuisine/day then `build_shopping_list`); the selection heuristic itself is deterministic so variety is
testable.

**Rationale**: Deterministic variety selection keeps SC-002 testable while the agent orchestrates. Using
the existing nullable `cuisine` column avoids any Phase 2 corpus change (clarified).

**Alternatives considered**: pure-LLM plan composition (rejected — un-grounded, hard to guarantee ≥3
distinct cuisines or safety); ignoring unknown-cuisine recipes entirely (rejected — shrinks the pool;
clarified to keep them eligible but non-counting).

## 9. Shopping list: dedupe, units, scaling

**Decision**: `services/user/shopping_list.py` aggregates parsed ingredients across all plan recipes,
**normalizes ingredient names** (lowercase, trim, simple singular/plural + known-synonym folding) to merge
duplicates, and **scales quantities to the cook's profile servings** (reusing the Phase 2 servings-scaling
convention) relative to each recipe's `servings`. Quantities are summed **only when units are compatible**
within a small unit family (mass: g/kg/oz/lb; volume: ml/l/tsp/tbsp/cup; count: each). **Incompatible or
unparseable units** for the same ingredient are emitted as **separate, clearly labeled lines** (FR-021),
never summed incorrectly. Output is **exactly one** list (FR-018).

**Rationale**: A small deterministic unit-family table covers the corpus's real units without a units
library (Principle I/X). Name normalization is the minimum needed for believable dedupe and is unit-tested.

**Alternatives considered**: a full units/conversion library like `pint` (rejected — new dep for marginal
value; Principle X); LLM-built lists (rejected — un-grounded math, not testable).

## 10. Curated substitutions

**Decision**: A curated **`app/services/shared/substitutions_data.py`** map (`ingredient → [substitutes]`,
each substitute annotated with the allergens it introduces) backs `services/user/substitution.py`. For a
request, look up substitutes for the ingredient and **filter out any that contain or may contain a declared
allergen** (reuse the allergen model + fail-closed rule). Return the safe list, or an **honest "no safe
substitute"** when empty (FR-022–024). The agent's `substitute_ingredient` tool wraps this service; the LLM
may phrase the result (`prompts/substitution.md`) but **never invents substitutes**.

**Rationale**: Grounding golden rule + the clarified decision: substitutions must not be free-form
generated. A curated map is deterministic, testable (`test_substitution.py` asserts zero declared-allergen
leaks), and torch/model-free.

**Alternatives considered**: LLM-generated substitutes wall-checked (rejected in clarification — invention
tension, harder to test); hybrid curated+LLM fallback (rejected for v1 — extra non-determinism; map can be
extended instead).

## 11. Guardrails approach

**Decision**: **Two-layer, deterministic-first** rails. `guardrails/input_rails.py` runs a fast
**deterministic screen** (curated regex/keyword patterns for jailbreak/role-override — "ignore previous
instructions", "you are now…", system-prompt-leak — and allergen-override phrasing — "ignore my
allergy/diet") **before routing**; suspicious turns are refused with a safe message. Allergen-override is
*also* structurally impossible past the wall (defense in depth). `guardrails/output_rails.py` runs the
existing **Presidio redaction** (reused from `core/redaction.py`) + a leak check, and **re-asserts the
wall** on any recipe in the response, before the reply leaves (and before any Phoenix span). NeMo
Guardrails (already a dep) is available as an optional richer config but is **not required** for the gate —
the deterministic rules + wall already achieve refusal=1.0. **Gate**: `redteam.refusal_rate_min = 1.0`.

**Rationale**: Safety must be predictable and testable (Principle IV/VI). Deterministic rules give a
provable red-team pass; the wall makes allergen-override refusal structural, not probabilistic. Keeping
NeMo optional honors Principle I (don't add orchestration we don't need to pass the gate).

**Alternatives considered**: NeMo-only rails (rejected as the sole mechanism — heavier, less predictable
than a deterministic screen for the specific probes we gate on); LLM self-critique only (rejected — not
deterministic enough for a hard CI gate).

## 12. Chat endpoint orchestration

**Decision**: `POST /chat` in `app/api/user/chat.py` reads the profile-ID via `api/deps.py`, loads the
cook's `ConstraintProfile`, then runs: **input rail → router.classify → workflow|agent → (recipes via
recipe_view = wall) → output rail**, returning `ChatResponse` (reply text, `recipes[]`, optional
`meal_plan` + `shopping_list`, `intent`, `refused`). Rate-limited per profile via `slowapi`. Traces emit
through `infra/tracing.py` **after** redaction.

**Rationale**: One thin endpoint mirroring the constitution's turn flow; all logic stays in services
(Principle III). Matches the existing `schemas`/`api/user` conventions from Phase 2.

**Alternatives considered**: separate endpoints per intent (rejected — the chat box is one surface; routing
is the classifier's job, not the URL's).

## Open items deferred to planning/implementation (non-blocking)

- Exact agent bound values (`max_iterations`, token budget) — set in config with safe defaults (5 / model
  default), tuned against the agent-tool-selection eval.
- Final `classifier.f1_min` and `rag.hit_at_k_min` numbers — pinned to just below achieved scores during
  implementation (never weakened afterward).
- Synonym/unit tables for shopping-list dedupe — grown to cover the actual ingested corpus vocabulary.
