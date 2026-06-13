// The widget's ONLY door to the outside world (FR-016, FR-018, FR-019).
//
// Every call goes to `import.meta.env.VITE_API_BASE` and nowhere else, and every call carries the
// `X-Profile-ID` header so the backend can scope favorites/history. The widget is "dumb": it renders only
// what the (already wall-filtered) backend returns and never invents content. This module also maps raw
// HTTP/refusal signals to a small set of TYPED UI states (see api/client.js contract in ui-contracts.md)
// so components branch on intent, not on status codes.

import { getProfileId } from "../lib/profile.js";

// Base origin for every request, baked at Vite build time. Trailing slash trimmed so path concatenation
// is predictable.
const BASE = (import.meta.env.VITE_API_BASE || "").replace(/\/$/, "");

// A typed error the UI can branch on. `kind` is one of the states from the client contract:
//   "rate_limited" (429) | "not_found" (404) | "bad_profile" (400 profile) | "network" (5xx/offline).
// Components show a calm, recoverable message per kind — never raw error text.
export class ApiError extends Error {
  constructor(kind, message, status) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
  }
}

// Translate a non-OK response into a typed ApiError. A 404 stays generic on purpose — a wall-withheld
// recipe is indistinguishable from a missing one, so we never leak existence (ui-contracts.md).
async function toError(res) {
  if (res.status === 429) return new ApiError("rate_limited", "Slow down a moment.", 429);
  if (res.status === 404) return new ApiError("not_found", "Not available.", 404);
  if (res.status === 400) return new ApiError("bad_profile", "Could not identify you — retrying.", 400);
  return new ApiError("network", "Something went wrong. Please try again.", res.status);
}

// Core fetch wrapper: attaches the identity header + JSON content type, prefixes the base URL, and turns
// any non-2xx into a typed ApiError. A thrown fetch (offline/DNS) becomes a "network" ApiError too, so
// callers only ever catch ApiError. `expectEmpty` returns null for 201/204 bodies (favorites save/remove).
async function request(path, { method = "GET", body, expectEmpty = false } = {}) {
  const headers = { "X-Profile-ID": getProfileId() };
  if (body !== undefined) headers["Content-Type"] = "application/json";

  let res;
  try {
    res = await fetch(`${BASE}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    // Network failure / CORS / offline — surface as a recoverable network state.
    throw new ApiError("network", "Cannot reach the kitchen. Check your connection.", 0);
  }

  if (!res.ok) throw await toError(res);
  if (expectEmpty || res.status === 204) return null;
  return res.json();
}

// ── Cook-facing endpoints (existing 002/003 backend, reused unchanged) ──────────────────────────────

export const api = {
  // GET /profile → { diet, allergies[], default_servings }. Defaults returned for a never-set cook.
  getProfile: () => request("/profile"),

  // PUT /profile → saved { diet, allergies[], default_servings }. Unknown enum values are a 422 (caller
  // should pre-validate against the known option lists so this stays a happy path).
  putProfile: (profile) => request("/profile", { method: "PUT", body: profile }),

  // GET /recipes?category=<underscored> → RecipeCard[]. The wall already filtered the list server-side;
  // an empty array is the honest "nothing compliant here" answer, not an error.
  listRecipes: (category) =>
    request(`/recipes?category=${encodeURIComponent(category)}`),

  // GET /recipes/{id} → RecipeDetail (verbatim steps). 404 → "not_found" (no existence leak).
  getRecipe: (id) => request(`/recipes/${encodeURIComponent(id)}`),

  // POST /chat { message, category? } → ChatResponse. The single conversational turn; the caller routes
  // the response to the correct render branch (refusal / cards / plan / list / substitution / empty).
  chat: (message, category) =>
    request("/chat", { method: "POST", body: category ? { message, category } : { message } }),

  // GET /favorites → RecipeCard[] (wall-filtered; a now-violating saved recipe is omitted).
  listFavorites: () => request("/favorites"),

  // POST /favorites { recipe_id } → 201, no body (idempotent). 404 if the id is unknown.
  saveFavorite: (recipeId) =>
    request("/favorites", { method: "POST", body: { recipe_id: recipeId }, expectEmpty: true }),

  // DELETE /favorites/{id} → 204, no body (idempotent — removing a not-saved recipe still succeeds).
  removeFavorite: (recipeId) =>
    request(`/favorites/${encodeURIComponent(recipeId)}`, { method: "DELETE", expectEmpty: true }),
};
