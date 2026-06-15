// The widget's ONLY door to the outside world (FR-016, FR-018, FR-019).
//
// Every call goes to `import.meta.env.VITE_API_BASE` and nowhere else. Since 009 the cook is authenticated:
// every gated call carries the cook-session JWT as `Authorization: Bearer` (login is the one unauthenticated
// call). The widget is "dumb": it renders only what the (already wall-filtered) backend returns and never
// invents content. This module also maps raw HTTP/refusal signals to a small set of TYPED UI states (see
// api/client.js contract in ui-contracts.md) so components branch on intent, not on status codes.

import { clearSession, getToken, setSession } from "../lib/session.js";

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

// Core fetch wrapper: attaches the Bearer token (for authed calls) + JSON content type, prefixes the base
// URL, and turns any non-2xx into a typed ApiError. A thrown fetch (offline/DNS) becomes a "network"
// ApiError too, so callers only ever catch ApiError. `expectEmpty` returns null for 201/204 bodies
// (favorites save/remove); `auth: false` is for the one unauthenticated call (login). On a 401 from an
// authed call the session is invalid (expired or the cook was deactivated): we drop it and signal a return
// to the login screen, so a revoked cook is bounced promptly (FR-009).
async function request(path, { method = "GET", body, expectEmpty = false, auth = true } = {}) {
  const headers = {};
  if (auth) {
    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
  }
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

  if (res.status === 401 && auth) {
    clearSession();
    // App listens for this to return to the login screen without a full reload.
    if (typeof window !== "undefined") window.dispatchEvent(new Event("souschef:logout"));
    throw new ApiError("unauthorized", "Your session has ended. Please sign in again.", 401);
  }

  if (!res.ok) throw await toError(res);
  if (expectEmpty || res.status === 204) return null;
  return res.json();
}

// ── Cook-facing endpoints ─────────────────────────────────────────────────────────────────────────

export const api = {
  // POST /auth/login { username, password } → { access_token, token_type, username }. The ONE
  // unauthenticated call: it persists the returned cook-session token so every later call is authed. A
  // wrong username/password is the backend's generic 401 (no enumeration), which the caller maps to a
  // single "invalid credentials" message.
  login: async (username, password) => {
    const data = await request("/auth/login", {
      method: "POST",
      body: { username, password },
      auth: false,
    });
    setSession(data.access_token, data.username);
    return data;
  },

  // GET /profile → { diet, allergies[], default_servings }. Defaults returned for a never-set cook.
  getProfile: () => request("/profile"),

  // PUT /profile → saved { diet, allergies[], default_servings }. Unknown enum values are a 422 (caller
  // should pre-validate against the known option lists so this stays a happy path).
  putProfile: (profile) => request("/profile", { method: "PUT", body: profile }),

  // GET /recipes?category=<underscored>&page=&page_size= → { items: RecipeCard[], total, page, page_size }.
  // The wall already filtered the list server-side (and `total` counts only survivors), so an empty
  // `items` is the honest "nothing compliant here" answer, not an error. `page` is 1-based.
  listRecipes: (category, page = 1, pageSize = 12) =>
    request(
      `/recipes?category=${encodeURIComponent(category)}&page=${page}&page_size=${pageSize}`,
    ),

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
