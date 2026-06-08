# Quickstart: Recipe Catalog, the Safety Wall & Favorites

Validates the feature end-to-end against the running stack. See [data-model.md](data-model.md) and
[contracts/](contracts/) for shapes; this guide is run/verify only.

## Prerequisites

- Foundation stack healthy: `make up` (backend + postgres + redis + vault + phoenix), `GET /health` 200.
- Migration applied: `uv run alembic upgrade head` includes `0002_catalog` (the six new tables).
- A Kaggle subset CSV placed in `ingestion/data/` (gitignored) if exercising Kaggle volume; TheMealDB /
  TheCocktailDB / Open Food Facts need no key for the dev run.

## Build the corpus (offline, idempotent)

```bash
make ingest          # → uv run python -m ingestion.run_ingest
```

Expected: recipes from TheMealDB (breakfast/lunch/dinner), TheCocktailDB non-alcoholic (hot/cold drink),
and the Kaggle subset are categorized, ingredient-parsed, allergen-tagged, nutrition-derived, and
upserted. **Re-running produces no duplicates** (upsert on `(source, source_id)`).

Corpus sanity (every surfaceable recipe is complete — SC-002):

```sql
-- 0 rows expected: a surfaceable recipe missing any required data
SELECT id FROM recipes
WHERE is_complete = true
  AND (category IS NULL
       OR id NOT IN (SELECT recipe_id FROM ingredients)
       OR id NOT IN (SELECT recipe_id FROM nutrition_cache));
```

## Scenario A — Browse safe recipes by category (US1)

```bash
PID=cook-demo-001
# Set a nut allergy
curl -X PUT localhost:8000/profile -H "X-Profile-ID: $PID" \
  -H 'content-type: application/json' \
  -d '{"diet":"none","allergies":["peanuts","tree_nuts"],"default_servings":2}'

# Browse dinner — every card is real and nut-free
curl localhost:8000/recipes?category=dinner -H "X-Profile-ID: $PID"
```

Expected: a JSON array of `RecipeCard`s, **0** containing peanuts/tree nuts (and none with
`allergen_certain = false`). With no allergies set, the same call returns more cards (nothing filtered).
A category with no compliant recipe returns `[]` — never a substitute (SC-007).

## Scenario B — Detail with verbatim steps + nutrition (US2)

```bash
RID=<id from a card above>
curl localhost:8000/recipes/$RID -H "X-Profile-ID: $PID"
```

Expected: `RecipeDetail` whose `steps` match the stored steps **verbatim** (SC-004) and a `nutrition`
summary scaled to `default_servings` (2), with `is_approximate` set when ingredients were unmapped.
Requesting a recipe that violates the cook's constraints (e.g., a nut recipe) returns **404** — the
detail path cannot bypass the wall (FR-008).

## Scenario C — Favorites persist across sessions (US3)

```bash
curl -X POST localhost:8000/favorites -H "X-Profile-ID: $PID" \
  -H 'content-type: application/json' -d "{\"recipe_id\":\"$RID\"}"      # 201
curl -X POST localhost:8000/favorites -H "X-Profile-ID: $PID" \
  -H 'content-type: application/json' -d "{\"recipe_id\":\"$RID\"}"      # 201, still one entry (idempotent)
curl localhost:8000/favorites -H "X-Profile-ID: $PID"                    # contains $RID
# New "session" = same profile-ID, fresh client
curl localhost:8000/favorites -H "X-Profile-ID: $PID"                    # still contains $RID (SC-003)
curl -X DELETE localhost:8000/favorites/$RID -H "X-Profile-ID: $PID"     # 204
```

Wall-on-favorites check: save a recipe, then `PUT /profile` adding an allergy that recipe violates;
`GET /favorites` no longer surfaces it (FR-019).

## Automated checks (the grade)

```bash
make test     # unit + integration
make lint     # ruff + mypy
```

Must pass, including:
- `tests/unit/test_constraint_guard.py` — the wall predicate (allergen hit, fail-closed on
  `allergen_certain = false`, each diet) **and the new-output-path regression**: a parametrized test
  over every cook-facing recipe path (`GET /recipes`, `GET /recipes/{id}`, `GET /favorites`) feeding a
  nut-allergic profile and asserting **0** violating recipes — adding a path that skips the guard fails.
- `tests/unit/test_nutrition_scaling.py` — servings scaling math + `is_approximate` passthrough.
- `tests/integration/test_recipes_flow.py` — browse → detail; verbatim steps; honest empty.
- `tests/integration/test_favorites.py` — save/list/remove, persistence, idempotency, wall-on-list.

## Done When

- All four `make test` suites + `make lint` green.
- Scenarios A–C behave as above against `make up`.
- Corpus sanity query returns 0 rows; a nut-allergic cook sees 0 nut recipes anywhere (SC-001).
