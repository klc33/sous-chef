// A single browse/list card — title, key ingredients, and a save-to-favorites action (FR-014, FR-017).
//
// The card renders only the fields the backend put on the wall-cleared RecipeCard DTO
// ({ id, title, category, key_ingredients[], image_url? }); it invents nothing. Clicking the card opens
// the full detail; the heart toggles the favorite without opening it.

import { labelFor } from "../lib/categories.js";
import { imageFor, placeholderFor } from "../lib/images.js";

// `recipe` is a RecipeCard DTO. `onOpen(id)` drills into the detail view. `onToggleFavorite(id)` saves or
// removes the favorite; `isFavorite` controls the heart's filled/outline state (may be undefined in browse
// where favorite status isn't loaded — then it shows the neutral "save" affordance).
export default function RecipeCard({ recipe, onOpen, onToggleFavorite, isFavorite }) {
  // Real source photo when present, else the generic category placeholder; `alt` is always the title.
  const { src, alt } = imageFor(recipe);
  return (
    <article className="card">
      {/* A failed source-photo load (404/blocked host) swaps to the category placeholder rather than
          showing a broken-image icon — never a misrepresenting or empty image (FR-013/014/015). */}
      <img
        className="card__img"
        src={src}
        alt={alt}
        loading="lazy"
        onError={(e) => {
          e.currentTarget.src = placeholderFor(recipe.category);
        }}
      />

      <button type="button" className="card__body" onClick={() => onOpen(recipe.id)}>
        <h3 className="card__title">{recipe.title}</h3>
        <span className="card__cat">{labelFor(recipe.category)}</span>
        {recipe.key_ingredients?.length > 0 && (
          <p className="card__ings">{recipe.key_ingredients.join(" · ")}</p>
        )}
      </button>

      {onToggleFavorite && (
        <button
          type="button"
          className={`card__fav${isFavorite ? " card__fav--on" : ""}`}
          aria-label={isFavorite ? "Remove from favorites" : "Save to favorites"}
          aria-pressed={!!isFavorite}
          onClick={() => onToggleFavorite(recipe.id)}
        >
          {isFavorite ? "♥" : "♡"}
        </button>
      )}
    </article>
  );
}
