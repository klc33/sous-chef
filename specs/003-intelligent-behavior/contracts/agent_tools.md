# Contract: Agent Tools (internal)

The bounded agent (`app/agent/loop.py`) acts **only** through these five tools (`app/agent/tools.py`).
Every tool input is validated against a Pydantic model in `app/schemas/tools.py` **before** execution;
invalid input is rejected, not run (FR-027). Every recipe-bearing output is produced via
`services/shared/recipe_view.py` → `constraint_guard` (the wall) (FR-028). Each call counts against the
agent's iteration bound; the loop also enforces a token budget (FR-026).

All tools receive the cook's resolved `ConstraintProfile` and `profile_id` from the loop context (not from
the LLM), so the model can never widen the cook's constraints or impersonate another profile.

## search_recipes
- **Input**: `{ query: str (1..200), category?: one of the five | null, k?: int (1..3, default 3) }`
- **Behavior**: embed query → `repo.recipes.search_by_vector` pre-filtered by category + diet +
  seen-history → wall → cards; records surfaced ids as seen.
- **Output**: `{ cards: RecipeCard[≤3] }` (empty when no compliant match — honest, never fabricated).
- **Safety**: pre-filtered + wall-cleared + freshness-excluded. Grounded in stored rows only.

## get_recipe
- **Input**: `{ recipe_id: uuid }`
- **Behavior**: `repo.recipes.get_by_id` → `recipe_view.to_detail` (verbatim stored steps + scaled nutrition).
- **Output**: `RecipeDetail` — or refusal/empty if the recipe violates the cook's constraints (the wall
  governs the detail path exactly as in 002; no bypass).

## get_nutrition
- **Input**: `{ recipe_id: uuid }`
- **Behavior**: `services/user/nutrition.scale` on the cached nutrition for the cook's servings.
- **Output**: `NutritionSummary` (calories + protein/carbs/fat; `is_approximate` passthrough).

## build_shopping_list
- **Input**: `{ recipe_ids: uuid[] (1..14) }`
- **Behavior**: `services/user/shopping_list` aggregates + dedupes (name-normalized) + scales to the cook's
  servings; merges compatible units; splits incompatible units into separate lines.
- **Output**: exactly one `ShoppingList`.

## substitute_ingredient
- **Input**: `{ ingredient: str (1..60), recipe_id?: uuid | null }`
- **Behavior**: `services/user/substitution` looks up the curated map and filters out any substitute that
  contains/may-contain a declared allergen (fail-closed).
- **Output**: `SubstitutionResult` (`substitutes[]` safe, or `none_safe=true`). Never invents a substitute.

## Bounds & failure
- `max_iterations` (default 5) and a cumulative token budget, both config-driven.
- On reaching a bound, the loop returns the **best safe partial result** (e.g., a plan with the days it
  completed + a shortfall note) or an honest failure message — never an unbounded loop, never an unsafe
  shortcut (FR-026, edge case "agent hits its bound").
