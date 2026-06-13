// The full recipe view — verbatim steps, parsed ingredients, scaled nutrition, favorite toggle (FR-015, FR-017).
//
// Steps are rendered EXACTLY as the backend stored them (golden rule #2 — never rewritten or summarized).
// The component is presentational: it shows the RecipeDetail DTO and reports favorite/close intents upward.

import { labelFor } from "../lib/categories.js";
import { imageFor, placeholderFor } from "../lib/images.js";

// `recipe` is a RecipeDetail DTO. `onBack()` returns to the previous list. `onToggleFavorite(id)` saves or
// removes; `recipe.is_favorite` seeds the heart state.
export default function RecipeDetail({ recipe, onBack, onToggleFavorite }) {
  const n = recipe.nutrition;
  // Real source photo when present, else the generic category placeholder; `alt` is always the title.
  const { src, alt } = imageFor(recipe);
  return (
    <section className="detail">
      <div className="detail__top">
        <button type="button" className="link" onClick={onBack}>
          ← Back
        </button>
        <button
          type="button"
          className={`card__fav${recipe.is_favorite ? " card__fav--on" : ""}`}
          aria-label={recipe.is_favorite ? "Remove from favorites" : "Save to favorites"}
          aria-pressed={recipe.is_favorite}
          onClick={() => onToggleFavorite(recipe.id)}
        >
          {recipe.is_favorite ? "♥" : "♡"}
        </button>
      </div>

      {/* A failed source-photo load falls back to the category placeholder — same grounding contract as
          the card: only this recipe's photo or a generic placeholder, never a borrowed one (FR-013/014/015). */}
      <img
        className="detail__img"
        src={src}
        alt={alt}
        onError={(e) => {
          e.currentTarget.src = placeholderFor(recipe.category);
        }}
      />

      <h2 className="detail__title">{recipe.title}</h2>
      <p className="detail__meta">
        {labelFor(recipe.category)}
        {recipe.cuisine ? ` · ${recipe.cuisine}` : ""}
        {recipe.total_time_minutes ? ` · ${recipe.total_time_minutes} min` : ""}
        {` · serves ${recipe.servings}`}
      </p>

      {recipe.allergens?.length > 0 && (
        <p className="detail__allergens">Contains: {recipe.allergens.join(", ")}</p>
      )}

      <h3>Ingredients</h3>
      <ul className="detail__ings">
        {recipe.ingredients.map((ing, i) => (
          <li key={i}>{ing.raw_text}</li>
        ))}
      </ul>

      <h3>Steps</h3>
      <ol className="detail__steps">
        {recipe.steps.map((step, i) => (
          <li key={i}>{step}</li>
        ))}
      </ol>

      {n && (
        <div className="detail__nutrition">
          {n.calories || n.protein_g || n.carbs_g || n.fat_g ? (
            <>
              <h3>Nutrition {n.is_approximate ? "(approximate)" : ""}</h3>
              <p>
                Per {n.servings} {n.servings === 1 ? "serving" : "servings"}: {Math.round(n.calories)} kcal ·{" "}
                {Math.round(n.protein_g)}g protein · {Math.round(n.carbs_g)}g carbs · {Math.round(n.fat_g)}g fat
              </p>
              {/* Partial coverage: some ingredients couldn't be measured into the totals. Say how many
                  contributed rather than implying the estimate is complete (golden rule #2 — be honest). */}
              {n.unmapped_ingredient_count > 0 && (
                <p className="detail__nutrition--coverage">
                  Estimated from {recipe.ingredients.length - n.unmapped_ingredient_count} of{" "}
                  {recipe.ingredients.length} ingredients — the rest couldn’t be measured.
                </p>
              )}
            </>
          ) : (
            // All macros zero = nutrition was never computed for this recipe (its ingredient quantities
            // couldn't be mapped at ingestion). Showing "0 kcal" would assert a false fact, so we say so
            // honestly instead of inventing a number (golden rule #2 — ground everything).
            <>
              <h3>Nutrition</h3>
              <p className="detail__nutrition--missing">Nutrition data isn’t available for this recipe.</p>
            </>
          )}
        </div>
      )}
    </section>
  );
}
