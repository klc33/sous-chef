# Phase 1 Data Model: Intelligent Behavior

This phase adds **one schema change** (an embedding vector on `recipes`) and **activates** the
already-created `seen_history` table. Most "entities" here are in-process/runtime objects (intent route,
ranked result, meal plan, shopping list, tool call, guardrail decision), not new tables — they are
computed per turn and not persisted (no requirement to store them; Principle II/X). Phase 2 tables
(`recipes`, `ingredients`, `nutrition_cache`, `profiles`, `favorites`, `seen_history`) are reused as-is
except where noted.

## Schema changes (Alembic `0003_embeddings`)

### `recipes` — add embedding (NEW column)

| Column | Type | Null | Notes |
|---|---|---|---|
| `embedding` | `vector(1536)` (pgvector) | YES | Recipe embedding from the hosted provider (model in config). Null until embedded by ingestion; a null-embedding recipe is simply not returned by vector search. Dimension pinned here; asserted against config at startup. |

- **Index**: `CREATE INDEX ix_recipes_embedding_hnsw ON recipes USING hnsw (embedding vector_cosine_ops);`
- **Reused existing columns** (no change): `category` (one of five), `cuisine` (nullable → "unknown" for
  the variety rule), `is_complete` (only complete recipes are surfaceable), `allergens` + `allergen_certain`
  (wall, fail-closed), `is_vegetarian/is_vegan/is_pescatarian` (diet pre-filter), `servings` (scaling),
  `steps` (verbatim detail), `ingredients` relationship (shopping list + key ingredients).
- **Migration safety**: additive, nullable column + index → no backfill required; existing rows get
  embeddings on the next `make ingest`. Downgrade drops the index then the column.

### `seen_history` — activate (NO schema change)

Created inert in `0002` (`id, profile_id → profiles.profile_id, recipe_id → recipes.id, shown_at`, index
`ix_seen_history_profile_shown (profile_id, shown_at)`). This phase wires it live:

- **Writes**: `repo.seen_history.insert(profile_id, recipe_id)` after a recipe is surfaced in a search
  result or meal plan.
- **Reads**: exclusion set for `search_by_vector` (`id <> ALL(seen ids)`), scoped to `profile_id`.
- **NEW repo function**: `repo.seen_history.clear(profile_id)` — deletes a cook's rows for reset-on-exhaustion.
- **Invariant**: favorites are never inserted into `seen_history`, and the favorites path never reads it.

## Reused entities (Phase 2 — unchanged)

- **Recipe / Ingredient / NutritionCache** — as in `002-catalog-wall-favorites/data-model.md`; this phase
  only reads them (+ writes `recipes.embedding` at ingestion).
- **Profile** — passwordless `profile_id`, `diet`, `allergies`, `servings`; source of the
  `ConstraintProfile` and the scaling target. Read-only here.
- **Favorite** — read-only here; exempt from freshness.
- **ConstraintProfile** (`services/user/constraint_guard.ConstraintProfile`) — the resolved diet +
  allergy set passed into every `recipe_view` call. Reused unchanged; it is the wall's input on every new
  path.

## Runtime (non-persisted) entities

These are Pydantic/dataclass objects created per turn. Validation rules trace to the FRs.

### IntentRoute (classifier output)
- `intent: Literal["find_recipe","plan_meals","nutrition_q","substitution","chitchat","out_of_scope"]`
- `confidence: float` (0–1)
- `route: Literal["workflow","agent","refuse"]` — derived: `plan_meals` or low-confidence/ambiguous →
  `agent`; `out_of_scope` → `refuse`; else `workflow`.
- Rules: FR-001..004. Produced by `app/classifier/predict.py`; routing decided in `services/user/router.py`.

### RankedRetrievalResult (RAG output)
- `cards: list[RecipeCard]` (≤ **3**, the clarified page size; each a real stored recipe)
- `query: str`, `category: str | None`
- Invariants: category + diet + seen pre-filtered in SQL over an **over-fetched candidate pool**
  (`retrieval_candidate_pool` ~20), then allergen/wall-filtered via `constraint_guard`/`recipe_view`, then
  **top-3 selected** — so 3 compliant cards surface whenever they exist; honest empty when no compliant
  match (FR-005..013). Surfaced ids are recorded to `seen_history`.

### MealPlan (agent output)
- `days: list[MealPlanDay]` where `MealPlanDay = { day: int, recipe: RecipeCard }`
- `distinct_cuisines: int` (≥3 when corpus allows; `null`/"unknown" cuisines don't count)
- `shortfall_note: str | None` (set when length or ≥3-cuisine variety can't be met — FR-017)
- Invariants: every recipe wall-safe; freshness-aware; exactly one `ShoppingList`. (FR-014..017)

### ShoppingList (meal-plan output)
- `lines: list[ShoppingLine]` where `ShoppingLine = { ingredient: str, quantity: float | None,
  unit: str | None, from_recipes: list[str] }`
- Invariants: exactly one list per plan; duplicates merged when units compatible; quantities scaled to the
  cook's servings; incompatible-unit duplicates kept as separate labeled lines (FR-018..021).

### SubstitutionResult
- `ingredient: str`, `substitutes: list[str]` (curated, allergen-safe), `none_safe: bool`
- Invariants: no substitute contains/may-contain a declared allergen (fail-closed); honest empty
  (`none_safe=true`) when nothing is safe (FR-022..024). Source = `substitutions_data.py` (never invented).

### AgentToolCall
- One schema-validated invocation of `search_recipes | get_recipe | get_nutrition | build_shopping_list |
  substitute_ingredient`; counts against the iteration bound. Input model in `app/schemas/tools.py`
  (Pydantic, validated before execution); recipe-bearing outputs pass through `recipe_view`. (FR-025..028)

### GuardrailDecision
- `stage: Literal["input","output"]`, `action: Literal["allow","sanitize","refuse"]`, `reason: str | None`
- Input rail decides allow/refuse before routing; output rail sanitizes (redaction) + re-asserts the wall
  before the reply leaves. (FR-029..033)

### ChatResponse (`app/schemas/chat.py`)
- `reply: str`, `recipes: list[RecipeCard]`, `meal_plan: MealPlan | None`,
  `shopping_list: ShoppingList | None`, `intent: str`, `refused: bool`
- The single response shape returned by `POST /chat`; recipes are always wall-cleared cards.

## Data-flow summary (one turn)

```
ChatRequest(message, category?) + profile-ID header
  → load Profile → ConstraintProfile
  → input_rails (refuse injection/jailbreak/override)        [GuardrailDecision]
  → classifier.predict → router                              [IntentRoute]
  → workflow (find_recipe→rag / nutrition_q / substitution / chitchat)
     | agent.loop (plan_meals / ambiguous) → tools → services [AgentToolCall*]
  → recipes rendered ONLY via recipe_view → constraint_guard  [the WALL]
  → freshness.record_seen (non-favorites)                     [seen_history write]
  → output_rails (redaction + leak check + wall re-assert)    [GuardrailDecision]
  → ChatResponse
```
