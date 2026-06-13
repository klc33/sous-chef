# Quickstart: Verifying Corpus Data Quality

Runnable validation that this feature behaves per spec. Assumes the stack is up (`make up`) and deps are
synced (`uv sync`). Commands are PowerShell-friendly.

## 1. Unit tests (the CI-gated correctness)

```powershell
uv run pytest tests/unit/test_nutrition_fallback.py -q     # fallback tables, _grams, USDA fallback, always-approximate
uv run pytest tests/unit/test_nutrition_scaling.py -q       # scale() carries is_approximate + unmapped_count verbatim
uv run pytest tests/unit/test_image_placeholders.py -q      # NEW: every Category has a committed placeholder asset (resolution can't fail)
uv run pytest tests/unit/test_foodcom_parsing.py -q         # NEW: a Food.com ingredient line parses into quantity+unit+name (FR-009)
```

Expected: all green. These prove contract C1 (nutrition states feed from coverage) and the C2
placeholder-availability guarantee. The widget has no JS unit-test runner (and we add none), so the
`imageFor` selection + `onError` *rendering* is checked manually in §3 below — not by pytest.

## 2. Nutrition states in the running app (C1)

Open the widget (`cd widget; npm run dev`) and check three recipes:

- **complete** — a Food.com-sourced recipe: shows totals, **no** "estimated" note, heading has no
  "(approximate)" (it is exact). Confirms SC-002.
- **partial** — a recipe with an unmappable line (e.g. "1 package …"): shows totals **and** "Estimated
  from N of M ingredients". Ask the same dish in chat → reply mirrors the note. Confirms FR-010/FR-011.
- **absent** — a recipe whose ingredients were all unmappable: shows "Nutrition data isn't available",
  no numbers. Confirms no fabricated value.

## 3. Images on every surface (C2)

In the widget:

- A TheMealDB/TheCocktailDB recipe (has `image_url`) shows its **real photo**; `alt` is the title.
- A Food.com/RecipeNLG recipe (no `image_url`) shows the **category placeholder** matching its category.
- Temporarily point a recipe's `image_url` at a broken URL (or block the host) → the view falls back to
  the **same placeholder**, not a broken-image icon.
- Confirm no surface shows a stock/borrowed photo as the dish (only source photo or generic placeholder).

## 4. Backfill — offline, idempotent, non-downgrading (C3, SC-005)

```powershell
$env:POSTGRES_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/souschef"
uv run python -m scripts.backfill_nutrition    # run 1: reports all-zero before/after + newly fixed
uv run python -m scripts.backfill_nutrition    # run 2: after-count unchanged, authoritative rows untouched
```

Expected: run 1 reduces the all-zero count; run 2 is a no-op on coverage (idempotent). Authoritative
(`is_approximate = false`) rows are reported as skipped in both runs.

## 5. Coverage improvement (SC-001 — operator-reported, not a CI gate)

With a Food.com subset placed at `ingestion/data/kaggle_recipes.csv` (see
[ingestion/data/README.md](../../ingestion/data/README.md)):

```powershell
uv run python -m ingestion.run_ingest          # canonical, source-aware refresh (make ingest)
```

Compare the `ingestion/coverage` report before vs. after and record:
- **Nutrition**: the usable-nutrition share rises and the "not available" rate drops (SC-001).
- **Images**: the share of recipes with a source `image_url` vs. those served the category placeholder
  (spec US3 acceptance #4) — so the operator sees both rates, not just nutrition.

The report now prints both blocks directly (`ingestion/coverage.py` → `nutrition:` and `images:`).

#### Recorded run (2026-06-13, 2224-recipe corpus incl. a 1,500-row Food.com subset)

| Metric | Before (stale rows, pre Food.com path) | After (`make ingest` reprocess) |
|---|---|---|
| Usable nutrition (real macros) | 690 (31.0%) | **2190 (98.5%)** |
| …of which exact (authoritative) | 0 (0.0%) | **1500 (67.4%)** |
| Nutrition "not available" | 1534 (69.0%) | **34 (1.5%)** |
| Source photo | 724 (32.6%) | 724 (32.6%) |
| Category placeholder | 1500 (67.4%) | 1500 (67.4%) |

The "not available" drop (69% → 1.5%) and the rise of exact rows (0 → 1,500) come from the Food.com
per-serving nutrition path; the 34 remaining are TheMealDB/TheCocktailDB recipes whose ingredient names
neither Open Food Facts nor the curated USDA fallback can map — honestly absent, never fabricated. Image
coverage is unaffected by the nutrition reprocess (it derives from each source's own `image_url`).

## 6. Safety gates stay green and unchanged

```powershell
make lint
make test        # incl. wall regression + redaction + red-team
make evals
```

Expected: all green; the wall / grounding / redaction / red-team behaviour is byte-for-byte unchanged
(this feature never touches the deterministic wall or guardrails).
