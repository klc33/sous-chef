# `seeds/corpus/` — committed seed corpus

A pre-built, **categorized + embedded** recipe subset that is committed to the repo and loaded
identically in **local, CI, and prod** so the demo never hits a cold corpus and `local == prod` data
(FR-013, SC-006). Production never runs the ingestion pipeline — it loads these files.

## Contents (see [contracts/seed-corpus.md](../../specs/007-ship-public-deploy/contracts/seed-corpus.md))

- `recipes.jsonl` — one recipe per line: `source_id` (stable upsert key), `title`,
  `category` ∈ {`hot drink`, `cold drink`, `breakfast`, `lunch`, `dinner`}, `ingredients[]`, `steps`,
  `diet_tags[]`, `allergen_tags[]`, plus the remaining columns the `recipes` table requires.
- `embeddings.npy` — float32 `[N, D]`; row *i* is the embedding of recipe *i* in `recipes.jsonl`.
  Tracked via **Git LFS** (see root `.gitattributes`) so the vector matrix doesn't bloat the base repo.
- `manifest.json` — `{ embedding_model, dim, count, exported_at, git_sha }`. `embedding_model` is the
  model that produced the vectors; the loader **fails fast** if it doesn't match the runtime embeddings
  model (no silent vector-space mismatch).

## Pipeline

- Built offline by `scripts/export_seed_corpus.py` against a populated dev DB (operator-run).
- Loaded by `scripts/load_seed_corpus.py` at deploy + in CI + locally — network-free, idempotent on
  `source_id`, writing **through the repo/ORM layer** (the only DB toucher).
