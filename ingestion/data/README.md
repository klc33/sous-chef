# Ingestion data (local, not committed)

This directory holds **manually downloaded** corpus inputs. Everything here except this README is
gitignored (see the repo `.gitignore`) — the datasets are large and licensed at their source, so they
are never committed.

## What goes here

The Kaggle subset that gives the corpus volume (per
[../../specs/002-catalog-wall-favorites/research.md](../../specs/002-catalog-wall-favorites/research.md) §1).
`ingestion/fetch_kaggle.py` reads a CSV placed here. TheMealDB and TheCocktailDB are pulled live from
their free APIs and do **not** need any file here.

### Expected file

- `kaggle_recipes.csv` — a subset of **RecipeNLG** or the **Food.com (RAW_recipes)** dataset.

Either dataset works; `fetch_kaggle.py` normalizes whichever columns are present into the common
raw-recipe shape (title, ingredients, steps). Keep the subset modest — the corpus target is roughly a
few hundred to ~2,000 recipes total across all sources.

**Nutrition**: if the **Food.com** `nutrition` column is present (a per-serving list
`[calories, total fat PDV, sugar PDV, sodium PDV, protein PDV, saturated fat PDV, carbohydrates PDV]`),
ingestion uses it directly as **authoritative** nutrition (`is_approximate = false`). Sources without a
nutrition column (RecipeNLG, TheMealDB, TheCocktailDB) fall back to an Open Food Facts estimate, which is
flagged `is_approximate = true`. So keep the `nutrition` column in your Food.com subset for exact macros.

When Open Food Facts can't match an ingredient name, or a line uses a count unit with no mass ("2 cloves
garlic", "1 egg"), ingestion consults a curated **USDA FoodData Central** fallback table
([`ingestion/ingredient_nutrition_data.py`](../ingredient_nutrition_data.py)) for average per-100g macros
and per-item weights — so common ingredients no longer collapse a recipe's nutrition to all-zeros. These
are averages, so any recipe relying on them stays `is_approximate = true`. Extend that table to raise
coverage; never put fabricated numbers in it (golden rule #2).

## How to obtain it

1. Download from Kaggle (account required):
   - RecipeNLG — <https://www.kaggle.com/datasets/paultimothymooney/recipenlg>, or
   - Food.com Recipes — <https://www.kaggle.com/datasets/shuyangli94/food-com-recipes-and-user-interactions>
2. Take a subset (e.g., the first ~1,500 rows) and save it as `ingestion/data/kaggle_recipes.csv`.
3. Run the pipeline: `make ingest` (→ `uv run python -m ingestion.run_ingest`). Ingestion is
   idempotent — re-running converges to the same corpus without duplicates.

> No Kaggle file? The pipeline still runs on TheMealDB + TheCocktailDB alone, just with a smaller corpus.
