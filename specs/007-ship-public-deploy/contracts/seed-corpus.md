# Contract: Seed Corpus (export + load)

Defines the committed corpus artifact and its two scripts. Verifies FR-013, SC-001, SC-006, and the
RAG/agent eval gates' ability to run in CI (Q5).

## Artifact (`seeds/corpus/`)
- `recipes.jsonl` — one JSON object per recipe: `source_id` (stable key), `title`, `category` ∈
  {`hot drink`,`cold drink`,`breakfast`,`lunch`,`dinner`}, `ingredients[]`, `steps`, `diet_tags[]`,
  `allergen_tags[]`, plus the remaining columns the `recipes` table requires.
- `embeddings.npy` — float32 `[N, D]`; row *i* is the embedding of recipe *i* in `recipes.jsonl`.
- `manifest.json` — `{ embedding_model, dim, count, exported_at, git_sha }`.

## `scripts/export_seed_corpus.py` (offline, operator-run)
- **Input**: a populated dev/ingested database.
- **Output**: the three files above, for a curated subset sufficient for the demo + RAG golden set.
- **Contract**: deterministic given the same DB; writes `manifest.embedding_model` = the model that
  produced the vectors; `count == len(recipes.jsonl) == embeddings.shape[0]`.

## `scripts/load_seed_corpus.py` (deploy + CI + local)
- **Input**: the committed `seeds/corpus/` files.
- **Behavior**: validate (`count`/`dim` consistency; `manifest.embedding_model` == runtime embeddings
  model — else **fail fast**, no silent vector-space mismatch); upsert rows + vectors into Postgres
  **through the repo/ORM layer** (the only DB toucher), **idempotent on `source_id`**.
- **No network**: loads pre-computed vectors; makes **zero** provider calls (deterministic, fast, free).
- **Idempotent**: re-running loads the same data; safe at every deploy/CI run.

## Guarantees
- Local, CI, and prod hold **identical** corpus rows + vectors (FR-013, SC-006).
- Seeded vectors and live query-time embeddings share one vector space (same pinned model), so retrieval
  behaves identically across environments.
- The wall/grounding still run at query time over the loaded rows — a seeded corpus cannot surface an
  unsafe recipe.
