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

## How to obtain it

1. Download from Kaggle (account required):
   - RecipeNLG — <https://www.kaggle.com/datasets/paultimothymooney/recipenlg>, or
   - Food.com Recipes — <https://www.kaggle.com/datasets/shuyangli94/food-com-recipes-and-user-interactions>
2. Take a subset (e.g., the first ~1,500 rows) and save it as `ingestion/data/kaggle_recipes.csv`.
3. Run the pipeline: `make ingest` (→ `uv run python -m ingestion.run_ingest`). Ingestion is
   idempotent — re-running converges to the same corpus without duplicates.

> No Kaggle file? The pipeline still runs on TheMealDB + TheCocktailDB alone, just with a smaller corpus.
