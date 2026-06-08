# Feature Specification: Recipe Catalog, the Safety Wall & Favorites

**Feature Branch**: `002-catalog-wall-favorites`

**Created**: 2026-06-08

**Status**: Draft

**Input**: User description: "Deliver Sous-Chef's data layer, the safety wall, and the non-AI product surface."

## User Scenarios & Testing *(mandatory)*

This feature delivers the first cook-facing product on top of the foundation skeleton: a real recipe
corpus, the deterministic safety wall that protects every cook, and the non-AI surface for browsing,
viewing, and saving recipes. There is no chat, agent, or semantic search in this feature — category
browse is a deterministic filter.

### User Story 1 - Browse safe recipes by category (Priority: P1)

A cook sets their diet, allergies, and default servings once. They pick one of five fixed categories
(hot drink, cold drink, breakfast, lunch, dinner) and see a list of **real** recipe cards (title + key
ingredients) drawn from the stored corpus — and **only** recipes that respect their stated allergies
and diet. This is the safe minimum viable product: real retrieved recipes that can never violate the
cook's constraints.

**Why this priority**: The wall is the grade. A cook who browses and sees only compliant, real recipes
already has a usable, trustworthy product. Detail views and favorites have no value — and the wall has
nothing to protect — until this slice exists. Profile + category browse + wall are inseparable: the
wall is meaningless without something to filter, and browsing is unsafe without the wall.

**Independent Test**: Set a nut allergy, pick each of the five categories, and confirm every returned
card is a real corpus recipe and none contains nuts. Set no constraints and confirm all real recipes
in the chosen category are returned. Confirm a category with no compliant recipe returns an explicit
empty result, never a substitute.

**Acceptance Scenarios**:

1. **Given** a cook with a nut allergy, **When** they browse any of the five categories, **Then** no
   recipe containing nuts (or with undetermined nut status) appears in the results.
2. **Given** a cook with no constraints set, **When** they pick the "dinner" category, **Then** they
   receive a list of real stored dinner recipes, each card showing title and key ingredients.
3. **Given** a vegan cook, **When** they browse "breakfast", **Then** only recipes that satisfy a vegan
   diet are shown.
4. **Given** a cook whose constraints exclude every recipe in a category, **When** they browse that
   category, **Then** they see an honest empty result, not a fabricated or constraint-relaxed recipe.
5. **Given** a cook sets diet/allergies/servings, **When** they return in a later session with the same
   profile-ID, **Then** their constraints are still in effect without re-entry.

---

### User Story 2 - Open a recipe for full instructions and nutrition (Priority: P2)

A cook clicks a recipe card and sees the recipe's full stored step-by-step instructions rendered
verbatim, plus derived nutrition scaled to their serving size.

**Why this priority**: Browsing cards proves the corpus is real; the detail view is what lets a cook
actually cook. It depends on US1 (there must be cards to open) and reinforces the grounding invariant —
steps are shown exactly as stored, never invented or rewritten.

**Independent Test**: Open any card surfaced in US1 and confirm the displayed steps match the recipe's
stored steps character-for-character, and that a nutrition summary scaled to the cook's servings is
shown. Confirm a recipe that violates the cook's constraints cannot be opened even by direct link.

**Acceptance Scenarios**:

1. **Given** a recipe card, **When** the cook opens it, **Then** the full stored step-by-step
   instructions are displayed verbatim with no added, removed, or paraphrased steps.
2. **Given** an opened recipe and a cook serving size, **When** the detail renders, **Then** a derived
   nutrition summary scaled to that serving size is shown.
3. **Given** a recipe whose ingredients cannot all be mapped to nutrition data, **When** the detail
   renders, **Then** nutrition is shown as approximate/partial rather than omitted or fabricated.
4. **Given** a recipe that violates the cook's constraints, **When** the cook attempts to open it
   directly (e.g., by its identifier or a stale link), **Then** the system withholds it exactly as a
   listing would (no bypass through the detail path).

---

### User Story 3 - Save and revisit favorites (Priority: P3)

A cook saves recipes to favorites, lists them, opens them, and removes them. Favorites persist per
passwordless profile across sessions and page reloads.

**Why this priority**: Favorites add retention and personal value but are not required for a cook to
discover and cook a recipe. They depend on US1/US2 existing. The wall still applies: a saved recipe
that violates the cook's current constraints is never surfaced.

**Independent Test**: Save a recipe, reload the page and open a new session with the same profile-ID,
and confirm the favorite is still listed and openable. Remove it and confirm it disappears. Add an
allergy that the saved recipe violates and confirm it is no longer surfaced in the favorites list.

**Acceptance Scenarios**:

1. **Given** a recipe detail, **When** the cook saves it to favorites, **Then** it appears in their
   favorites list.
2. **Given** a saved favorite, **When** the cook reloads the page or starts a new session with the same
   profile-ID, **Then** the favorite is still present.
3. **Given** a favorite, **When** the cook removes it, **Then** it no longer appears in their favorites.
4. **Given** the same recipe is saved twice, **When** the cook views favorites, **Then** it appears
   only once (saving is idempotent).
5. **Given** a cook adds an allergy that a previously saved favorite violates, **When** they view
   favorites, **Then** that favorite is not surfaced (consistent with the wall applying to every path).

---

### Edge Cases

- **No constraints set**: nothing is filtered; the cook sees all real recipes in the chosen category.
- **Compounding constraints**: a vegan cook who is also allergic to soy must have BOTH constraints
  satisfied simultaneously; a recipe failing either is excluded.
- **Undetermined allergen status**: if a recipe's allergen status relative to a cook's allergy cannot
  be positively established as safe, it is treated as violating and excluded (fail closed).
- **Empty-but-honest results**: a category (or favorites list) with zero compliant recipes returns an
  explicit empty result, never a relaxed-constraint or invented substitute.
- **Unmappable nutrition**: a recipe with one or more ingredients that cannot be mapped shows partial /
  approximate nutrition rather than a fabricated total.
- **Stale favorite / direct link**: opening a recipe by identifier is subject to the same wall as
  listings; a now-violating recipe cannot be reached this way.
- **Profile edits**: changing diet/allergies/servings applies to all subsequent results immediately.
- **Recipe ineligible for surfacing**: a corpus recipe missing a category, parsed ingredients, allergen
  tags, or nutrition is never shown to a cook.

## Clarifications

### Session 2026-06-08

- Q: Which allergen and diet set should the wall enforce? → A: Nine major allergens — peanuts, tree
  nuts, milk/dairy, eggs, wheat/gluten, soy, fish, shellfish, sesame — and four diets: none,
  vegetarian, vegan, pescatarian.
- Q: Which public/free sources build the corpus, and roughly how large? → A: TheMealDB (food) +
  TheCocktailDB (drinks) + one free Kaggle recipe dataset subset; target a few hundred to ~2,000
  recipes.
- Q: What nutrition should the recipe detail show, and from where? → A: Map parsed ingredients to Open
  Food Facts; show calories + protein/carbs/fat per serving; mark approximate when ingredients are
  unmapped.

## Requirements *(mandatory)*

### Functional Requirements

**Cook profile & identity**

- **FR-001**: A cook MUST be able to set and later update a dietary profile consisting of a diet
  preference, zero or more allergies, and a default serving size.
- **FR-002**: Cook identity MUST be a passwordless profile-ID supplied with the request; all profile
  data (constraints and favorites) is scoped to that ID and is never taken from request body owner
  fields.
- **FR-003**: Profile constraints MUST persist keyed to the profile-ID so they remain in effect in any
  later session presenting the same ID, with no re-entry.

**The safety wall (constraint guard)**

- **FR-004**: A deterministic constraint guard MUST remove every recipe that violates the cook's stated
  allergies or diet, applied on EVERY output path that returns recipes to a cook — category browse,
  recipe detail access, and favorites listing/opening — and any path added later.
- **FR-005**: The wall MUST be enforced in deterministic code, never by a prompt or a model decision.
- **FR-006**: The wall MUST fail closed: when a recipe's allergen or diet status relative to a cook's
  constraint cannot be positively determined as safe, the recipe MUST be treated as violating and
  excluded.
- **FR-007**: When no recipe satisfies the cook's constraints for the requested path, the system MUST
  return an honest empty result — never a fabricated recipe and never a result with constraints relaxed.
- **FR-008**: Direct access to a specific recipe by identifier MUST be subject to the same wall as
  listings; a violating recipe MUST NOT be reachable through the detail path.

**Category browse & grounding**

- **FR-009**: Each recipe MUST be tagged to exactly one of the five fixed categories — hot drink, cold
  drink, breakfast, lunch, dinner — at ingestion.
- **FR-010**: A cook MUST be able to select exactly one category and receive a list of real recipe
  cards whose recipes are tagged to that category (a deterministic metadata filter, not a runtime
  guess and not semantic search).
- **FR-011**: Each recipe card MUST display at least the recipe title and its key ingredients, where
  "key ingredients" is the recipe's first up-to-four parsed ingredients in stored order.
- **FR-012**: All surfaced recipes and cards MUST correspond to real stored corpus recipes; the system
  MUST NEVER invent a recipe or card.

**Recipe detail & nutrition**

- **FR-013**: Opening a card MUST display the recipe's full stored step-by-step instructions rendered
  verbatim — no steps added, removed, summarized, or paraphrased.
- **FR-014**: Recipe detail MUST display derived nutrition for the recipe, scaled to the cook's serving
  size.
- **FR-015**: Nutrition MUST be derived from the recipe's parsed ingredients by mapping them to Open
  Food Facts data, presenting calories plus protein, carbohydrates, and fat per serving; when one or
  more ingredients cannot be mapped, nutrition MUST be presented as approximate/partial rather than
  omitted or fabricated.

**Favorites**

- **FR-016**: A cook MUST be able to save a recipe to favorites, list their favorites, open a favorite
  to its detail, and remove a favorite.
- **FR-017**: Favorites MUST persist per profile-ID across page reloads and new sessions.
- **FR-018**: Saving a recipe already in favorites MUST be idempotent (no duplicate entries).
- **FR-019**: The favorites list and open paths MUST be subject to the wall, so a saved recipe that
  violates the cook's current constraints is never surfaced.

**Corpus / data quality**

- **FR-020**: Every recipe in the corpus MUST have exactly one category, parsed ingredients (name, and
  quantity/unit where the source provides them), allergen tags, and nutrition data. A recipe missing
  any of these MUST NOT be eligible to be surfaced to a cook.
- **FR-021**: Allergen tags MUST be derived deterministically at ingestion and stored per recipe for
  the nine supported allergens (peanuts, tree nuts, milk/dairy, eggs, wheat/gluten, soy, fish,
  shellfish, sesame), so the wall can filter without any runtime model inference.
- **FR-022**: Diet classification MUST be derived deterministically at ingestion and stored per recipe
  for the four supported diets (none, vegetarian, vegan, pescatarian).
- **FR-023**: The corpus MUST be built only from public/free data sources — specifically TheMealDB
  (food), TheCocktailDB (drinks), and a free Kaggle recipe dataset subset — targeting roughly a few
  hundred to ~2,000 recipes.

### Key Entities *(include if feature involves data)*

- **Cook Profile**: a passwordless cook identity (profile-ID) and their constraints — diet preference,
  set of allergies, and default serving size. Owns favorites.
- **Recipe**: a real stored recipe — title, exactly one category, source attribution, and an ordered
  list of stored steps shown verbatim. Has parsed ingredients, allergen tags, diet classification, and
  nutrition.
- **Ingredient (parsed)**: a structured ingredient line for a recipe — name, and quantity/unit where
  available — used for nutrition derivation and allergen detection.
- **Allergen Tag**: a deterministic per-recipe marker for each of the nine supported allergens
  (peanuts, tree nuts, milk/dairy, eggs, wheat/gluten, soy, fish, shellfish, sesame) the recipe
  contains or may contain (the latter triggers fail-closed exclusion).
- **Diet Classification**: a per-recipe marker of which of the four supported diets (none, vegetarian,
  vegan, pescatarian) the recipe satisfies.
- **Nutrition**: per-recipe derived values — calories plus protein, carbohydrates, and fat — aggregated
  from parsed ingredients mapped to Open Food Facts, scalable to a serving size; may be partial when
  ingredients are unmapped.
- **Category**: the fixed enumeration {hot drink, cold drink, breakfast, lunch, dinner}; each recipe
  belongs to exactly one.
- **Favorite**: a saved association between a Cook Profile and a Recipe.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For a cook with a nut allergy, **0** recipes containing nuts or with undetermined nut
  status appear across category browse, recipe detail, and favorites — verified against the entire
  corpus.
- **SC-002**: **100%** of recipes in the corpus have a category, parsed ingredients, allergen tags, and
  nutrition; recipes missing any of these are never surfaced.
- **SC-003**: A favorite saved in one session is present after a page reload and in a new session with
  the same profile-ID **100%** of the time.
- **SC-004**: **0** invented recipes or steps — every card and detail corresponds to a real stored
  recipe, and displayed steps match the stored steps verbatim.
- **SC-005**: Each recipe belongs to exactly one category, and browsing a category returns **0**
  recipes from any other category.
- **SC-006**: A cook can set their constraints and retrieve a compliant category listing in under one
  minute and no more than three interactions.
- **SC-007**: When no compliant recipe exists for a requested category or for favorites, the cook
  receives an explicit empty result in **100%** of such cases, with **0** non-compliant recipes shown.

## Assumptions

- **Supported allergens** (locked, see Clarifications) are the nine major allergens: peanuts, tree
  nuts, milk/dairy, eggs, wheat/gluten, soy, fish, shellfish, and sesame. **Supported diets** are:
  none, vegetarian, vegan, and pescatarian.
- Allergen tags are derived at ingestion by mapping parsed ingredients against a curated
  allergen→ingredient map, supplemented by any source-provided tags; genuine uncertainty resolves to
  "may contain", which the wall treats as a violation (fail closed).
- Nutrition is derived by mapping parsed ingredients to Open Food Facts and aggregating calories +
  protein/carbs/fat, scaled to the cook's servings; unmapped ingredients yield approximate/partial
  nutrition.
- Corpus data comes from TheMealDB (food), TheCocktailDB (hot/cold drinks), and a free Kaggle recipe
  dataset subset, targeting roughly a few hundred to ~2,000 recipes; paid or license-restricted sources
  are out of scope.
- Serving scaling applies to displayed nutrition and to ingredient quantities where the source provides
  them.
- One profile equals one cook; there is no password, login, or multi-user account system — only the
  passwordless profile-ID.
- **Out of scope for this feature (Future):** chat/agent, semantic or vector search, intent
  classification, meal planning, shopping lists, ingredient substitution, and freshness/seen-history.
  Category browse here is a deterministic metadata filter only.
