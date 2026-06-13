"""Load the committed seed corpus into Postgres — the deploy + CI + local data path (network-free).

Consumes `seeds/corpus/` (produced offline by `export_seed_corpus.py`) and upserts every recipe + its
pre-computed vector into Postgres **through the repo layer** (the only DB toucher), idempotent on
(source, source_id). It makes **zero** provider calls — the vectors are already computed — so it is
deterministic, fast, and free, and runs identically at deploy, in CI, and locally (FR-013). Production
never runs the ingestion pipeline; it runs this.

Fail-fast validation (contract seed-corpus.md) BEFORE any write:
  * `manifest.count == len(recipes.jsonl) == embeddings.shape[0]` — the three files must agree.
  * `embeddings.shape[1] == manifest.dim == settings.embeddings_dim` — the vector width matches the column.
  * `manifest.embedding_model == settings.embeddings_model` — seeded vectors and live query-time vectors
    MUST share one embedding space, or retrieval is silently garbage. A mismatch aborts the load (no
    silent vector-space mismatch) rather than poisoning the corpus.

Idempotent: re-running converges on the same rows + vectors (safe at every deploy/CI run).

Run in the backend image (deploy/CI), or on the host against the mapped Postgres port locally, e.g.:
    $env:POSTGRES_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/souschef"
    uv run python -m scripts.load_seed_corpus
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from app.config import get_settings
from app.repo import recipes as recipes_repo
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

# Repo root from this file: scripts/load_seed_corpus.py -> parents[1]; the committed artifact lives here.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_CORPUS_DIR = _REPO_ROOT / "seeds" / "corpus"


def _read_rows(path: Path) -> list[dict[str, Any]]:
    """Parse `recipes.jsonl` into a list of recipe dicts (one per non-blank line, in file order).

    File order is the contract's alignment key: row *i* pairs with embedding row *i*, so the list order
    must be preserved exactly as written by the exporter.
    """
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _validate(manifest: dict[str, Any], rows: list[dict[str, Any]], vectors: np.ndarray) -> None:
    """Fail fast on any count/dim/model disagreement BEFORE writing a single row (contract guarantees).

    A `SystemExit` here aborts the load with a legible message rather than letting a mismatched corpus or a
    cross-space vector reach the DB, where it would surface only as opaque retrieval garbage at query time.
    """
    settings = get_settings()
    count = int(manifest["count"])
    dim = int(manifest["dim"])

    if not (count == len(rows) == vectors.shape[0]):
        raise SystemExit(
            "seed-corpus load aborted: count mismatch — "
            f"manifest.count={count}, recipes.jsonl={len(rows)}, embeddings={vectors.shape[0]}."
        )
    if not (dim == vectors.shape[1] == settings.embeddings_dim):
        raise SystemExit(
            "seed-corpus load aborted: dim mismatch — "
            f"manifest.dim={dim}, embeddings={vectors.shape[1]}, embeddings_dim={settings.embeddings_dim}."
        )
    if manifest["embedding_model"] != settings.embeddings_model:
        raise SystemExit(
            "seed-corpus load aborted: embedding-model mismatch — corpus was built with "
            f"'{manifest['embedding_model']}' but the runtime model is '{settings.embeddings_model}'. "
            "Seeded vectors and live query vectors must share one space; rebuild the seed or fix the config."
        )


def _upsert_row(session: Session, row: dict[str, Any], vector: np.ndarray) -> None:
    """Upsert one recipe and its pre-computed vector through the repo, idempotent on (source, source_id).

    Replays the stored fields straight into `upsert_recipe` (no recompute, no invention — `is_complete`
    and the allergen/diet flags are carried verbatim from the export), then writes the aligned vector via
    `set_embedding`. Both calls go through the repo layer so this script never touches the DB directly.
    The vector is converted to plain Python floats so pgvector binds it as a parameter.
    """
    recipe = recipes_repo.upsert_recipe(
        session,
        source=row["source"],
        source_id=row["source_id"],
        title=row["title"],
        category=row["category"],
        cuisine=row.get("cuisine"),
        total_time_minutes=row.get("total_time_minutes"),
        servings=row["servings"],
        steps=row["steps"],
        image_url=row.get("image_url"),
        allergens=row["allergens"],
        allergen_certain=row["allergen_certain"],
        is_vegetarian=row["is_vegetarian"],
        is_vegan=row["is_vegan"],
        is_pescatarian=row["is_pescatarian"],
        is_complete=row["is_complete"],
        ingredients=row["ingredients"],
        nutrition=row.get("nutrition"),
    )
    recipes_repo.set_embedding(session, recipe.id, vector.tolist())


def run() -> None:
    """Validate the committed seed corpus, then idempotently upsert every recipe + vector into Postgres.

    Loads the three artifact files, runs the fail-fast validation, and upserts each row through the repo in
    a single transaction (committed once at the end). Makes zero provider calls. Prints how many rows were
    loaded so deploy/CI logs show the corpus landed.
    """
    manifest = json.loads((_CORPUS_DIR / "manifest.json").read_text(encoding="utf-8"))
    rows = _read_rows(_CORPUS_DIR / "recipes.jsonl")
    vectors = np.load(_CORPUS_DIR / "embeddings.npy")

    _validate(manifest, rows, vectors)

    settings = get_settings()
    engine = create_engine(settings.postgres_url, future=True)
    with Session(engine) as session:
        for row, vector in zip(rows, vectors, strict=True):
            _upsert_row(session, row, vector)
        session.commit()
    engine.dispose()

    print(
        f"seed corpus loaded: {len(rows)} recipes + {vectors.shape[0]}x{vectors.shape[1]} vectors "
        f"(model={manifest['embedding_model']}, sha={manifest.get('git_sha', 'unknown')})."
    )


if __name__ == "__main__":
    run()
