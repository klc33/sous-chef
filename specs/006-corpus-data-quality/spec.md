# Feature Specification: Corpus Data Quality — Honest Nutrition & Images

**Feature Branch**: `006-corpus-data-quality`

**Created**: 2026-06-13

**Status**: Draft

**Input**: User description: "Raise the data quality of Sous-Chef's corpus so no recipe shows a false or broken fact — nutrition or image — while never inventing one."

## Clarifications

### Session 2026-06-13

- Q: How should Food.com's percent-daily-value (PDV) macros be turned into grams while staying "exact"? → A: Convert PDV → grams using fixed, committed FDA reference daily values (deterministic, authoritative); store as exact (`is_approximate = false`).
- Q: What determines which ingredients/units go into the curated authoritative fallback set? → A: Frequency-driven — curate the most-frequent unmapped ingredient names + count units observed in the corpus (top-N by occurrence) until the coverage gain flattens.
- Q: Does the backfill apply the new fallback (USDA + count-unit weights) to the already-ingested corpus, or only re-run existing computation? → A: Yes — the backfill applies the full new fallback logic to existing recipes (still offline, still never downgrading exact rows).
- Q: Is coverage improvement enforced as a hard CI gate or recorded by the operator? → A: Per-case correctness tests gate CI; coverage rise is measured and recorded via the operator's coverage report — no hard global percentage gate.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Honest nutrition on every recipe (Priority: P1)

A cook opens a recipe and wants to know its calories and macros. Today many recipes read
"Nutrition data isn't available" even when the ingredients are perfectly ordinary, because the
approximate aggregation could not map common ingredient names or count-based lines (e.g. "2 cloves
garlic", "1 egg") to any mass, collapsing totals to zero. After this change the cook sees one of three
honest states: **complete** real numbers, a **partial** estimate clearly labelled "estimated from N of
M ingredients", or — only when nothing at all could be measured — "not available". A cook never sees a
fabricated or misleading number.

**Why this priority**: This is the core of the feature and the most directly felt by cooks. It upholds
golden rule #2 (ground everything / never invent a fact) and the Trust principle. A wrong calorie count
is a fabricated fact a cook may act on; honesty about coverage is the entire point.

**Independent Test**: Ingest/backfill the corpus, then open recipes representing each case: one whose
ingredients all map (complete), one with some unmappable lines (partial, shows the "N of M" note), and
one with nothing measurable (absent). Verify the displayed state and that no recipe shows an invented
number. Fully testable on its own and delivers immediate cook value.

**Acceptance Scenarios**:

1. **Given** a recipe whose ingredients all resolve to nutrition, **When** the cook opens its detail
   view, **Then** complete calories and macros are shown with no "estimated" qualifier.
2. **Given** a recipe where some ingredient lines cannot be measured into the totals, **When** the cook
   opens its detail view, **Then** totals are shown alongside "estimated from N of M ingredients", and a
   chat reply that surfaces its nutrition mirrors that same partial-coverage note.
3. **Given** a recipe where no ingredient line could be measured, **When** the cook opens its detail
   view, **Then** it states nutrition is "not available" and shows no numbers.
4. **Given** any recipe, **When** its nutrition is displayed anywhere (detail or chat), **Then** the
   values originate only from an authoritative reference or the recipe's own source data — never from a
   model or a guess.

---

### User Story 2 - Every recipe shows a truthful image (Priority: P1)

A cook browses recipe cards and opens details. Today many recipes render an empty or broken image
because only some sources ship image URLs and others do not. After this change every card and detail
shows a picture: the recipe's real source photo when one exists, otherwise a tasteful, clearly-generic
per-category placeholder with descriptive alt text. A failed image load falls back to the same
placeholder. No surface ever shows a borrowed/stock/AI photo of a *different* dish as if it were this
recipe.

**Why this priority**: A wall of broken images makes a real product look broken (Trust/usability), and
showing a photo of a different dish would be a fabricated fact about *this* recipe (grounding). Equal in
importance to nutrition honesty for the cook's first impression.

**Independent Test**: Browse and open recipes from a source with images and from a source without
images; verify the first shows its real photo and the second shows the category placeholder. Simulate a
broken/unreachable image URL and verify it falls back to the placeholder. Confirm alt text names the
recipe. No code from User Story 1 is required.

**Acceptance Scenarios**:

1. **Given** a recipe whose source provides an image, **When** it appears on a card or detail view,
   **Then** that real source image is shown with alt text naming the recipe.
2. **Given** a recipe with no source image, **When** it appears on a card or detail view, **Then** a
   neutral per-category placeholder is shown (the placeholder matching its fixed category) with
   descriptive alt text, and it does not look like a real photo of the specific dish.
3. **Given** a recipe whose image URL fails to load, **When** the cook views it, **Then** the view
   falls back to the same category placeholder rather than a broken-image icon.
4. **Given** any recipe surface, **When** an image is shown, **Then** it is either this recipe's own
   source photo or a generic placeholder — never a third-party photo presented as this dish.

---

### User Story 3 - Operator raises coverage reproducibly (Priority: P2)

The operator wants the "no nutrition" and "no image" rates to drop and to prove it. They swap the
weak data source for one that carries quantities and per-serving nutrition, run a clean ingestion, and
optionally run a backfill that recomputes nutrition for the *existing* corpus from already-stored
ingredients (using the on-disk reference cache, with no live network calls). Coverage rises, and the
run is reproducible from a clean start without mutating unrelated fields or downgrading recipes that
already had exact nutrition.

**Why this priority**: This is what makes the cook-facing wins real and durable, and it is required for
the Reproducibility principle. It is P2 because the cook-facing surfaces (P1) can be demonstrated on a
seeded corpus before the full pipeline swap lands.

**Independent Test**: From a clean state, ingest the new subset plus the authoritative fallback and
record coverage; run the backfill a second time and confirm it is idempotent (no field churn, exact
rows unchanged). Verify reported coverage rose versus the prior corpus.

**Acceptance Scenarios**:

1. **Given** the new quantity-and-nutrition-bearing subset, **When** the operator runs a clean
   ingestion, **Then** recipes carrying a per-serving nutrition column store **exact** nutrition
   (`is_approximate = false`) and their quantity-bearing lines parse for scaling.
2. **Given** the existing corpus, **When** the operator runs the backfill from stored ingredients,
   **Then** nutrition is recomputed from the on-disk reference cache with no live calls, no other field
   is modified, and no authoritative (exact) row is downgraded to approximate.
3. **Given** a completed run, **When** the operator runs it again from a clean start, **Then** the
   resulting corpus is the same (ingestion and backfill are idempotent).
4. **Given** before/after corpus snapshots, **When** the operator compares them, **Then** the share of
   recipes with usable nutrition and with a displayable image has measurably risen.

---

### Edge Cases

- **Count units with no inherent mass** ("2 cloves garlic", "1 egg", "3 slices bacon"): resolve to
  grams via average per-item / per-count-unit weights for the common cases; they contribute to the
  totals and do not collapse them to zero.
- **Genuinely variable units** ("1 can", "1 package", "1 piece", "to taste", "a handful"): these stay
  unmapped and are reported as partial coverage — counted toward the M, excluded from the measured N —
  never guessed.
- **A recipe with its own per-serving nutrition AND unmappable lines**: the authoritative source
  nutrition is used and stays exact (`is_approximate = false`); the per-ingredient fallback never
  overrides it.
- **An ingredient the authoritative reference also lacks**: it remains unmapped and is reported as
  partial coverage, never approximated from a near-match guess.
- **All ingredients unmappable**: the recipe shows "not available" rather than a zeroed-out total.
- **Image URL present but unreachable / 404 / slow**: the view falls back to the category placeholder.
- **A recipe whose category is ambiguous for placeholder selection**: it still receives exactly one
  placeholder, because every recipe already carries exactly one fixed category.
- **Backfill encountering a recipe with no stored ingredients**: it leaves the recipe untouched rather
  than zeroing it.

## Requirements *(mandatory)*

### Functional Requirements

**Nutrition coverage & honesty**

- **FR-001**: The system MUST provide an authoritative per-ingredient nutrition fallback (per-100g
  macros plus average per-item / per-count-unit gram weights for common ingredients), sourced from an
  authoritative nutrition reference, used to widen coverage. The curated set MUST be **frequency-driven**:
  populated from the most-frequent unmapped ingredient names and count units observed in the corpus
  (top-N by occurrence), expanded until the marginal coverage gain flattens — not an exhaustive food
  database.
- **FR-002**: The fallback MUST be **additive**: it contributes a value only where the primary
  ingredient-nutrition source returned nothing, and it MUST NOT override a recipe's authoritative source
  nutrition.
- **FR-003**: Count-based and unit-less ingredient lines for the common cases MUST resolve to grams via
  average per-item / per-count-unit weights so they contribute to the totals instead of collapsing them
  to zero.
- **FR-004**: Genuinely variable units (e.g. can / package / piece / "to taste") MUST remain unmapped
  and be reported as partial coverage; the system MUST NOT guess a value for them.
- **FR-005**: Any aggregated total that includes a fallback-derived or per-ingredient-estimated value
  MUST be flagged approximate (`is_approximate = true`).
- **FR-006**: A recipe that carries its own per-serving nutrition (from its source) MUST store **exact**
  nutrition flagged not-approximate (`is_approximate = false`). Where the source encodes macros as
  percent-of-daily-value (as Food.com does), the system MUST convert PDV to grams using a fixed,
  committed set of FDA reference daily values (a deterministic, authoritative conversion — never a
  guess); the result remains exact.
- **FR-007**: All nutrition values MUST originate only from an authoritative reference or the recipe's
  own source data — never from an LLM, embedding, or heuristic guess.

**Data source**

- **FR-008**: The system MUST replace the current quantity-less Kaggle subset (RecipeNLG) with the
  Food.com RAW_recipes subset, which provides per-line ingredient quantities and a per-serving nutrition
  column, and ingest it through the existing pipeline.
- **FR-009**: Quantity-bearing ingredient lines from the new subset MUST parse into structured
  quantity + unit + name so they support serving-scaling and per-ingredient aggregation.

**Cook-facing presentation**

- **FR-010**: The cook-facing nutrition view MUST distinguish three states — **complete** (all measured),
  **partial** ("estimated from N of M ingredients"), and **absent** ("not available") — and show numbers
  only in the complete and partial states.
- **FR-011**: A chat reply that surfaces a recipe's nutrition MUST mirror the partial-coverage note when
  the recipe's nutrition is partial.
- **FR-012**: The system MUST NOT display a fabricated or misleading nutrition number on any surface.

**Images**

- **FR-013**: Every recipe surface (card and detail) MUST render an image: the recipe's source image
  when present, otherwise a per-category placeholder.
- **FR-014**: A failed image load MUST fall back to the same per-category placeholder rather than a
  broken-image state.
- **FR-015**: Every displayed image MUST carry alt text that names the recipe.
- **FR-016**: The system MUST NOT fetch or display a third-party / stock / AI photo as if it were the
  actual recipe; placeholders MUST read as clearly generic, never as a real photo of the specific dish.

**Ingestion & backfill**

- **FR-017**: Ingestion MUST remain idempotent and offline (no live nutrition/image network calls during
  the fallback path); reproducible from a clean run.
- **FR-018**: The system MUST provide a backfill that recomputes nutrition for the existing corpus from
  **stored** ingredients using the on-disk reference cache, with no live calls. The backfill MUST apply
  the full new fallback logic (the authoritative USDA fallback and count-unit gram weights) so that
  already-ingested recipes gain coverage without a fresh re-ingest.
- **FR-019**: The backfill MUST NOT modify any field other than the nutrition fields and MUST NOT
  downgrade an authoritative (exact) row to approximate.

**Safety invariants (unchanged, must stay intact)**

- **FR-020**: The allergy/diet wall and grounding MUST remain deterministic and behaviourally unchanged
  by this feature; nutrition and image fallbacks MUST NOT introduce any model-driven decision into
  either path.

### Key Entities *(include if data involved)*

- **Recipe nutrition summary**: per-recipe totals scaled to servings (calories, protein, carbs, fat),
  an `is_approximate` flag, and a count of unmapped ingredients used to express "N of M" partial
  coverage.
- **Authoritative nutrition reference**: a committed, static dataset of per-100g macros for common
  ingredients plus average per-item / per-count-unit gram weights; the source of fallback values.
- **Per-serving source nutrition**: nutrition shipped with a source recipe (Food.com), stored as exact.
- **Ingredient line**: a parsed line with optional quantity + unit + name plus the original raw text;
  the unit of aggregation and the unit of scaling.
- **Recipe image reference**: the recipe's source image URL (may be absent) plus its fixed category,
  which selects the placeholder when no usable source image exists.
- **Category placeholder asset**: a committed, generic image per fixed category (hot drink, cold drink,
  breakfast, lunch, dinner).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After ingesting the Food.com subset plus the fallback, the share of recipes showing
  usable nutrition (complete or partial) rises substantially versus the prior corpus, and the "nutrition
  not available" rate drops correspondingly. This is verified by comparing the operator's coverage
  report before and after; it is **not** enforced as a hard CI percentage gate (the absolute baseline
  depends on corpus mix). CI instead gates the per-case correctness in SC-002/SC-003 below.
- **SC-002**: 100% of recipes that carry source per-serving nutrition display **exact** (non-approximate)
  macros.
- **SC-003**: A recipe with some unmappable ingredients shows totals plus an accurate "estimated from N
  of M ingredients" note; a recipe with nothing measurable shows "not available"; **0** recipes show an
  invented macro value.
- **SC-004**: 100% of cards and detail views render a non-broken image — a real source photo where one
  exists, otherwise a category placeholder — and **0** displayed images misrepresent a different dish as
  the recipe.
- **SC-005**: Running ingestion and the backfill a second time from a clean start produces an identical
  corpus (idempotent), and the backfill modifies no field outside nutrition and downgrades no exact row.
- **SC-006**: All existing quality gates remain green and unchanged in behaviour — the allergy/diet
  wall, grounding, redaction, and red-team gates — and the new nutrition-fallback and image-fallback
  coverage tests pass.

## Assumptions

- The existing nutrition data model already supports the needed signals (`is_approximate`,
  `unmapped_ingredient_count`, and a per-recipe `image_url`); this feature widens the data feeding them
  and adds the presentation fallback rather than changing the schema's shape.
- "Authoritative reference" means USDA FoodData Central per-100g macros and standard average gram
  weights, committed as static assets in the repo — not a runtime API.
- "Common ingredients / common count units" is the frequency-driven curated set defined in FR-001
  (top-N most-frequent unmapped items from the corpus, expanded until coverage gain flattens), not an
  exhaustive food database; uncommon items remain honestly unmapped.
- The Food.com RAW_recipes subset is acquired and committed/cached by the operator the same way the
  prior Kaggle subset was; sizing of the subset is an ingestion-config detail, not a cook-facing concern.
- Category placeholders are a small set of committed image assets, one per fixed category, neutral
  enough to never be mistaken for the specific dish.
- "Rises sharply / substantially" is left as a relative improvement against the current corpus rather
  than a fixed percentage, because the absolute baseline depends on the corpus mix at ingestion time;
  the operator records before/after rates from `ingestion/coverage` reporting.
