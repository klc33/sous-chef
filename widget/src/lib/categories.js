// The five fixed browse categories — the single source of truth for the widget (FR-007 clarification, R7).
//
// The backend's catalog endpoints (`GET /recipes?category=`) speak the UNDERSCORED canonical value, while
// `/chat` may echo a category in its SPACED form. The widget standardizes internally on the underscored
// `value`, shows the cook only the human `label`, and `normalize()`s any spaced string back to canonical.
// Categories are fixed (never guessed), so this list is static and ordered for display.

export const CATEGORIES = [
  { value: "hot_drink", label: "Hot Drink" },
  { value: "cold_drink", label: "Cold Drink" },
  { value: "breakfast", label: "Breakfast" },
  { value: "lunch", label: "Lunch" },
  { value: "dinner", label: "Dinner" },
];

// Map a canonical underscored value to its display label; falls back to the raw value if it is unknown
// (so an unexpected category from the backend still renders something honest rather than blank).
export function labelFor(value) {
  const found = CATEGORIES.find((c) => c.value === value);
  return found ? found.label : value;
}

// Normalize any category string to the underscored canonical form. Handles the spaced `/chat` spelling
// ("hot drink" → "hot_drink") and case, so a category arriving from a chat turn can be matched/displayed
// against CATEGORIES consistently.
export function normalize(s) {
  if (!s) return s;
  return s.trim().toLowerCase().replace(/ /g, "_");
}

// When a cook's free-text chat message IS just a category name ("hot drink", "Hot Drinks", "breakfast"),
// return that canonical category value so the caller can constrain retrieval to it; otherwise null.
// Without this, a bare "hot drink" search is a pure cross-category vector match — and once freshness has
// shown the real hot drinks, retrieval leaks into the next-nearest items (e.g. hot lunch dishes). Matching
// is exact-on-normalize (with a lenient trailing-'s' plural) so a genuine discovery phrase like "something
// warm" is NOT forced into a category — only an explicit category name is.
export function detectCategory(message) {
  if (!message) return null;
  const n = normalize(message);
  const hit = CATEGORIES.find((c) => n === c.value || n === `${c.value}s`);
  return hit ? hit.value : null;
}
