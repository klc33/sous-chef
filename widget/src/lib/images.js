// Image grounding for the cook-facing surfaces — pick the real source photo or an honest category
// placeholder, never a borrowed/stock photo presented as the dish (FR-013/014/015/016, contract C2).
//
// We never fetch images at runtime: the five per-category placeholders are committed static SVGs (one
// per fixed Category), imported here so Vite fingerprints + bundles them. Because every Category has a
// committed asset, placeholder resolution can never fail — the dependency-free guarantee that
// tests/unit/test_image_placeholders.py enforces against the backend enum.

import { normalize } from "./categories.js";

import hotDrink from "../assets/placeholders/hot_drink.svg";
import coldDrink from "../assets/placeholders/cold_drink.svg";
import breakfast from "../assets/placeholders/breakfast.svg";
import lunch from "../assets/placeholders/lunch.svg";
import dinner from "../assets/placeholders/dinner.svg";

// Canonical underscored Category value → its committed placeholder asset URL. Keyed on the same values
// the backend persists (app/models/recipe.py Category), so a category from any surface resolves directly.
const PLACEHOLDERS = {
  hot_drink: hotDrink,
  cold_drink: coldDrink,
  breakfast: breakfast,
  lunch: lunch,
  dinner: dinner,
};

// The default placeholder if a category is somehow unknown/missing — dinner is the most generic "a meal"
// icon, so an unexpected value still renders a plain, honest placeholder rather than a broken image.
const FALLBACK = dinner;

// Resolve the generic placeholder for a recipe's category. `normalize` folds the spaced `/chat` spelling
// ("hot drink") back to the canonical key, so either form resolves; an unknown value falls back generically.
export function placeholderFor(category) {
  return PLACEHOLDERS[normalize(category)] ?? FALLBACK;
}

// The image contract for a card/detail: render the recipe's own source photo when present, otherwise the
// generic category placeholder. `alt` is always the recipe title (never empty, never a stock-photo caption).
// The caller wires `onError` to swap `src` to `placeholderFor(recipe.category)` if the source photo 404s.
export function imageFor(recipe) {
  return {
    src: recipe.image_url || placeholderFor(recipe.category),
    alt: recipe.title,
  };
}
