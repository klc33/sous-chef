"""Offline exporter — freeze a populated dev DB into the committed seed corpus (`seeds/corpus/`).

Operator-run, once, AFTER `make up` + `make ingest` have built and embedded the corpus. Reads every
complete + embedded recipe through the repo layer (the only DB toucher) and writes the three artifact
files the contract pins (specs/007-ship-public-deploy/contracts/seed-corpus.md):

  * `recipes.jsonl`   — one recipe per line, carrying EVERY column a faithful `upsert_recipe` needs
                        (steps verbatim, ingredients, nutrition, allergen/diet flags), so the loader can
                        rebuild the exact rows with zero invention.
  * `embeddings.npy`  — float32 `[N, D]`; row *i* is recipe *i*'s vector, aligned line-for-line to the
                        jsonl by the SAME deterministic (source, source_id) ordering.
  * `manifest.json`   — `{ embedding_model, dim, count, exported_at, git_sha }`; `embedding_model` is the
                        model that produced the vectors, so the loader can fail fast on a space mismatch.

Determinism: the repo orders by (source, source_id), so the same DB always yields byte-identical files.
This never embeds anything — it copies the vectors already stored by ingestion — so it makes zero
provider calls. Production never runs this; it runs `load_seed_corpus.py` over the committed output.

Run on the host (full dev env + current code), pointing at the mapped Postgres port, e.g. (PowerShell):
    $env:POSTGRES_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/souschef"
    uv run python -m scripts.export_seed_corpus
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
from app.config import get_settings
from app.models.recipe import Recipe
from app.repo import recipes as recipes_repo
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

# Repo root from this file: scripts/export_seed_corpus.py -> parents[1]; the artifact lives under seeds/.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_CORPUS_DIR = _REPO_ROOT / "seeds" / "corpus"


def _num(value: Any) -> Any:
    """Coerce a SQL Numeric (Decimal) to a JSON-safe float, leaving None and ints/strs untouched.

    `quantity` and the nutrition macros are `Numeric` columns that come back as `Decimal`, which the stdlib
    JSON encoder cannot serialize. Converting to `float` keeps the values round-trippable through the loader
    (which feeds them straight back into the same Numeric columns) without dragging Decimal into the file.
    """
    return float(value) if isinstance(value, Decimal) else value


def _recipe_to_row(recipe: Recipe) -> dict[str, Any]:
    """Render one ORM Recipe as the loader-shaped JSON object (every field `upsert_recipe` consumes).

    Mirrors the `upsert_recipe` keyword surface exactly — scalar columns, the verbatim `steps`, the ordered
    `ingredients` children, and the optional `nutrition` row — so a re-upsert reconstructs the identical
    row. `is_complete` is exported as-is (every exported row is complete by construction) so the loader
    never recomputes it. The embedding is NOT in this object — it travels in the aligned `embeddings.npy`.
    """
    nutrition = recipe.nutrition
    return {
        "source": recipe.source,
        "source_id": recipe.source_id,
        "title": recipe.title,
        "category": recipe.category,
        "cuisine": recipe.cuisine,
        "total_time_minutes": recipe.total_time_minutes,
        "servings": recipe.servings,
        "steps": list(recipe.steps),
        "image_url": recipe.image_url,
        "allergens": list(recipe.allergens),
        "allergen_certain": recipe.allergen_certain,
        "is_vegetarian": recipe.is_vegetarian,
        "is_vegan": recipe.is_vegan,
        "is_pescatarian": recipe.is_pescatarian,
        "is_complete": recipe.is_complete,
        "ingredients": [
            {
                "position": ing.position,
                "name": ing.name,
                "quantity": _num(ing.quantity),
                "unit": ing.unit,
                "raw_text": ing.raw_text,
                "allergen_tags": list(ing.allergen_tags),
            }
            for ing in recipe.ingredients
        ],
        "nutrition": (
            {
                "basis_servings": nutrition.basis_servings,
                "calories": _num(nutrition.calories),
                "protein_g": _num(nutrition.protein_g),
                "carbs_g": _num(nutrition.carbs_g),
                "fat_g": _num(nutrition.fat_g),
                "is_approximate": nutrition.is_approximate,
                "unmapped_ingredient_count": nutrition.unmapped_ingredient_count,
            }
            if nutrition is not None
            else None
        ),
    }


def _git_sha() -> str:
    """Return the current HEAD short SHA for provenance, or "unknown" when git is unavailable.

    Best-effort: the SHA records which commit produced the corpus, but a missing/again-detached git must
    not abort an export, so any failure degrades to "unknown" rather than raising.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except Exception:  # noqa: BLE001 — provenance is best-effort; never fail the export over it
        return "unknown"


def run() -> None:
    """Export the populated DB's complete+embedded corpus into the three committed seed files.

    Reads the rows through the repo (deterministic order), writes `recipes.jsonl` and the row-aligned
    `embeddings.npy`, then a `manifest.json` pinning the embedding model/dim/count. Asserts
    `count == len(recipes) == embeddings.shape[0]` and that every vector matches the configured dim before
    writing the manifest, so a malformed export fails here rather than at load time.
    """
    settings = get_settings()
    engine = create_engine(settings.postgres_url, future=True)
    _CORPUS_DIR.mkdir(parents=True, exist_ok=True)

    with Session(engine) as session:
        recipes = recipes_repo.iter_complete_embedded(session)

    if not recipes:
        raise SystemExit(
            "export aborted: no complete + embedded recipes found — run `make up` + `make ingest` first."
        )

    rows = [_recipe_to_row(r) for r in recipes]
    # Vectors in the SAME order as the rows; float32 to halve the on-disk/LFS size vs float64 with no loss
    # of retrieval fidelity (cosine distance is unaffected at this precision).
    vectors = np.asarray([list(r.embedding) for r in recipes], dtype=np.float32)

    count = len(rows)
    dim = int(vectors.shape[1])
    if dim != settings.embeddings_dim:
        raise SystemExit(
            f"export aborted: stored vector dim {dim} != configured embeddings_dim "
            f"{settings.embeddings_dim} — the DB and config disagree on the embedding space."
        )
    if vectors.shape[0] != count:
        raise SystemExit(
            f"export aborted: {vectors.shape[0]} vectors for {count} recipes — rows and vectors must align."
        )

    # Write the recipe rows as JSON Lines (one object per line, sorted keys for a stable diff).
    recipes_path = _CORPUS_DIR / "recipes.jsonl"
    with recipes_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    # Write the aligned vector matrix (allow_pickle stays off — it is a plain float32 array).
    np.save(_CORPUS_DIR / "embeddings.npy", vectors)

    manifest = {
        "embedding_model": settings.embeddings_model,
        "dim": dim,
        "count": count,
        "exported_at": datetime.now(UTC).isoformat(),
        "git_sha": _git_sha(),
    }
    (_CORPUS_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    engine.dispose()
    print(
        "seed corpus exported:\n"
        f"  recipes.jsonl   : {count} rows\n"
        f"  embeddings.npy  : {vectors.shape[0]}x{dim} float32\n"
        f"  manifest.json   : model={settings.embeddings_model} sha={manifest['git_sha']}"
    )


if __name__ == "__main__":
    run()
