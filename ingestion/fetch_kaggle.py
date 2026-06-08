"""Load + normalize a manually-downloaded Kaggle recipe subset into the common raw-recipe shape.

Offline ingestion stage 1 (volume). Reads `ingestion/data/kaggle_recipes.csv` (gitignored; see
ingestion/data/README.md) with pandas and normalizes whichever schema is present — RecipeNLG
(`title`/`ingredients`/`directions`) or Food.com RAW_recipes (`name`/`ingredients`/`steps`/`id`) — into
the same dict the rest of the pipeline consumes. If the file is absent the pipeline still runs on the
API sources alone, so a missing file returns [] rather than raising.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pandas as pd

# Default subset location (gitignored). run_ingest may override the path.
_DEFAULT_CSV = Path("ingestion/data/kaggle_recipes.csv")

# Cap rows read from the CSV so the corpus stays near the ≤~2,000 target even when the full Food.com /
# RecipeNLG dump (hundreds of thousands of rows) is present. Bounding it here keeps ingestion fast and
# the Open Food Facts lookups manageable, and makes the run reproducible regardless of file size.
_DEFAULT_LIMIT = 1500


def _as_list(value: Any) -> list[str]:
    """Coerce a cell that may be a Python-list-literal string (or NaN) into a list of clean strings.

    RecipeNLG/Food.com store list columns as stringified Python lists (e.g. "['a', 'b']"). We parse
    with ast.literal_eval and fall back to a single-item list (or []) when the cell is not list-shaped.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return [text]
    if isinstance(parsed, (list, tuple)):
        return [str(v).strip() for v in parsed if str(v).strip()]
    return [str(parsed).strip()]


def _normalize(row: pd.Series, index: int) -> dict[str, Any]:
    """Flatten one CSV row into the common raw-recipe dict, tolerant of either dataset's columns."""
    # Title: RecipeNLG uses `title`, Food.com uses `name`.
    title = str(row.get("title") or row.get("name") or "").strip()
    # Steps: RecipeNLG `directions`, Food.com `steps`.
    steps = _as_list(row.get("directions") if "directions" in row else row.get("steps"))
    ingredients = _as_list(row.get("ingredients"))
    # A stable source_id: prefer the dataset's own id, else the row index.
    raw_id = row.get("id")
    source_id = str(raw_id) if raw_id is not None and not pd.isna(raw_id) else f"row-{index}"
    minutes = row.get("minutes")
    return {
        "source": "kaggle",
        "source_id": source_id,
        "title": title,
        "kind": "food",
        "source_category": None,  # no reliable category column → categorize uses keywords/default
        "cuisine": None,
        "image_url": None,
        "servings": None,
        "total_time_minutes": int(minutes) if minutes is not None and not pd.isna(minutes) else None,
        "raw_ingredients": ingredients,
        "steps": steps,
        "title_blob": " ".join([title, *steps]),
    }


def fetch(
    csv_path: Path | str = _DEFAULT_CSV, limit: int | None = _DEFAULT_LIMIT
) -> list[dict[str, Any]]:
    """Read up to `limit` rows of the Kaggle CSV as normalized raw-recipe dicts; [] when absent.

    `nrows=limit` makes pandas read only the first N rows, so a multi-hundred-MB dump never gets fully
    loaded. Pass `limit=None` to read the whole file when a smaller, pre-subset CSV is provided.
    """
    path = Path(csv_path)
    if not path.exists():
        return []
    frame = pd.read_csv(path, nrows=limit)
    return [_normalize(row, i) for i, (_, row) in enumerate(frame.iterrows())]
