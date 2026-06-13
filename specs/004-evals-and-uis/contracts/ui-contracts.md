# UI Contracts: Cook Widget → Backend, Dashboard → Admin

This documents the client-side contracts for the two surfaces. The cook widget consumes the **existing**
002/003 backend endpoints unchanged — there is no new cook-facing endpoint. The dashboard consumes the new
admin endpoints in [admin.openapi.yaml](admin.openapi.yaml).

## 1. Cook widget → backend (existing endpoints, reused)

The widget is "dumb": it only renders what the backend (already wall-filtered) returns and never invents
content. Every request attaches the identity header.

### Common request rules
- **Header on every request**: `X-Profile-ID: <uuid>` from `lib/profile.js` (generated once via
  `crypto.randomUUID()`, persisted in `localStorage`). (FR-018)
- **Base URL**: `import.meta.env.VITE_API_BASE`. The widget calls **only** this origin (FR-019).
- **Category values are underscored** on the wire for catalog endpoints; the widget maps to display
  labels and normalizes any spaced category from `/chat` (FR-007 clarification; see `lib/categories.js`).

### Endpoints consumed
| Action | Method + path | Source contract | Widget render |
|---|---|---|---|
| Read constraints | `GET /profile` | 002 profile.openapi.yaml | ConstraintsForm (prefill) |
| Set constraints | `PUT /profile` | 002 profile.openapi.yaml | ConstraintsForm (save) |
| Browse a category | `GET /recipes?category=<underscored>` | 002 recipes.openapi.yaml | CategoryChips → RecipeCard grid |
| Open full recipe | `GET /recipes/{id}` | 002 recipes.openapi.yaml | RecipeDetail (verbatim steps + nutrition) |
| Converse | `POST /chat` (`{message, category?}`) | 003 chat.openapi.yaml | ChatTurnView branch (see below) |
| Save favorite | `POST /favorites` (`{recipe_id}`) | 002 favorites.openapi.yaml | RecipeCard/Detail ❤ |
| List favorites | `GET /favorites` | 002 favorites.openapi.yaml | Favorites view |
| Remove favorite | `DELETE /favorites/{recipe_id}` | 002 favorites.openapi.yaml | Favorites view |

### `POST /chat` response → render branches (ChatTurnView)
Given `ChatResponse { reply, intent, refused, recipes[], meal_plan?, shopping_list?, substitution? }`:
- `refused === true` → **RefusalNotice** (calm, distinct from a system error). (FR-020)
- `recipes.length > 0` → **RecipeCard grid** (+ `reply` as thin glue text).
- `recipes.length === 0` and no plan/list/substitution → **honest empty state** (no fabricated content). (FR-021)
- `meal_plan` present → **MealPlanView** (days × recipe cards, distinct-cuisine count, shortfall note).
- `shopping_list` present → **ShoppingList** (consolidated, scaled lines).
- `substitution` present → **SubstitutionCard** (curated substitutes or honest "no safe substitute").

### Client error/state mapping (`api/client.js`)
| Backend signal | Widget state |
|---|---|
| `200` normal | render the turn/result |
| `200` with `refused=true` | RefusalNotice (safety), **not** an error |
| `[]` empty list | honest empty state |
| `404` on `GET /recipes/{id}` | generic "not available" (wall-withheld is indistinguishable; do not leak existence) |
| `400` missing/invalid profile id | regenerate id / re-send (should not happen in normal flow) |
| `429` rate limited | gentle "slow down a moment" notice |
| network error / 5xx | recoverable error state with retry (distinct from a refusal) |
| pending (search) | skeleton cards / spinner |
| pending (planning, ~15–20s) | distinct "Planning your week…" progress (FR-023) |

## 2. Dashboard → admin (new endpoints)

The Streamlit dashboard authenticates the **human** via `streamlit-authenticator` (cookie survives
refresh; password hash + cookie key from Vault). It then calls the backend admin API with the shared
**admin token** (from Vault) as `Authorization: Bearer <ADMIN_API_TOKEN>`.

| Page | Calls | Renders |
|---|---|---|
| `1_corpus.py` | `GET /admin/corpus?page&page_size&category` | paged table of corpus rows (incl. allergen/diet tags) |
| `2_evals.py` | `POST /admin/evals/run` | gate table: name, PASS/FAIL/SKIP, measured-vs-threshold |
| `3_metrics.py` | `GET /admin/metrics` | classifier macro-F1, routing split, gate status, Phoenix deep-link + recent cost |

### Auth contract
- Unauthenticated dashboard visitor → Streamlit shows the login form only; no admin call is made. (FR-029)
- Authenticated operator → every admin call carries the bearer token; a `401/403` from the backend surfaces
  as "admin token rejected — check Vault seeding".
- The public cook widget has no admin token and no admin UI; it cannot reach `/admin/*`.

## 3. Category canonical map (shared reference)

| Canonical value (wire, catalog) | `/chat` spelling (normalized on input) | Display label |
|---|---|---|
| `hot_drink` | `hot drink` | Hot Drink |
| `cold_drink` | `cold drink` | Cold Drink |
| `breakfast` | `breakfast` | Breakfast |
| `lunch` | `lunch` | Lunch |
| `dinner` | `dinner` | Dinner |

The widget standardizes internally on the **underscored** value, normalizes the spaced `/chat` form via
`categories.normalize()`, and shows only the **Display label** to the cook.
