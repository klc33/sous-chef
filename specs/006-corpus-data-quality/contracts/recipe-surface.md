# Contract: Recipe Surface — Nutrition States & Image Fallback

This feature introduces **no API/schema change**. The HTTP contract is unchanged from
[contracts/recipes.openapi.yaml](../../../contracts/recipes.openapi.yaml), which already exposes
`NutritionSummary.unmapped_ingredient_count` (integer, minimum 0) and a nullable `image_url`. The
contract that changes is the **rendering contract** of the cook-facing surfaces — testable, deterministic
behaviour that the implementation and acceptance tests must honour.

## C1 — Nutrition rendering contract (detail view + chat)

Given a `NutritionSummary` and the rendered ingredient list (length `M`):

| Condition | State | Required output |
|-----------|-------|-----------------|
| all of calories/protein/carbs/fat == 0 | **absent** | "Nutrition data isn't available for this recipe." — no numbers shown |
| any macro > 0 and `unmapped_ingredient_count == 0` | **complete** | totals (`Per S servings: …kcal · …g protein · …g carbs · …g fat`); `(approximate)` heading qualifier iff `is_approximate` |
| any macro > 0 and `unmapped_ingredient_count > 0` | **partial** | totals **plus** "Estimated from N of M ingredients" where `N = M − unmapped_ingredient_count` |

- The chat `nutrition_q` reply MUST mirror the **partial** note when `unmapped_ingredient_count > 0`.
- `is_approximate` and `unmapped_ingredient_count` MUST survive serving rescaling unchanged.
- A number is shown ONLY in the complete/partial states; the absent state asserts no value.

**Status: implemented** in `widget/src/components/RecipeDetail.jsx` and
`app/services/user/workflow.py::_nutrition_q`. Acceptance = verify against this table.

## C2 — Image rendering contract (card + detail)

`imageFor(recipe) → { src, alt }` and the rendered `<img>` MUST satisfy:

| Condition | Required output |
|-----------|-----------------|
| `recipe.image_url` present and loads | `<img src={image_url} alt={recipe.title}>` (the real source photo) |
| `recipe.image_url` absent | `<img src={placeholder(recipe.category)} alt={recipe.title}>` |
| `<img>` load fails (`onError`) | `src` swapped to `placeholder(recipe.category)`; never a broken-image state |
| any case | `alt` == `recipe.title` (non-empty); `src` is EITHER this recipe's source photo OR a generic category placeholder — never a third-party/stock photo presented as the dish |

- `placeholder(category)` resolves to exactly one committed SVG per fixed category
  (`hot_drink`, `cold_drink`, `breakfast`, `lunch`, `dinner`).
- No runtime image fetching; placeholders are committed static assets.

**Status: to implement** (card currently renders a blank div with `alt=""`; detail renders no image).

## C3 — Backfill operational contract

`scripts/backfill_nutrition.py` MUST:
- read each recipe's **stored** ingredients and recompute via `ingestion.nutrition.aggregate` (on-disk
  OFF cache + new fallback; **no live calls**);
- write only the `nutrition_cache` row (no other field);
- **skip** rows where `is_approximate == false` (never downgrade authoritative data);
- be idempotent (a second run over an unchanged corpus produces no semantic change);
- print a before/after all-zero count report.

**Status: implemented.** Acceptance = run twice; second run reports the same after-count and changes no
exact row.
