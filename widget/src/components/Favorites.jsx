// The saved-recipes view — list favorites, open one, remove one (FR-017).
//
// The list comes from GET /favorites (wall-filtered server-side, so a saved recipe that now violates the
// cook's constraints is simply absent). Reuses RecipeCard for each entry; the heart there removes.

import RecipeCard from "./RecipeCard.jsx";

// `favorites` is a RecipeCard[]; `onOpen(id)` drills in; `onRemove(id)` deletes the favorite. An empty
// list renders an honest empty state, never a fabricated suggestion.
export default function Favorites({ favorites, onOpen, onRemove }) {
  if (!favorites || favorites.length === 0) {
    return (
      <div className="empty">
        <p>No favorites yet. Tap the ♡ on a recipe to save it here.</p>
      </div>
    );
  }

  return (
    <div className="grid">
      {favorites.map((recipe) => (
        <RecipeCard
          key={recipe.id}
          recipe={recipe}
          onOpen={onOpen}
          onToggleFavorite={onRemove}
          isFavorite
        />
      ))}
    </div>
  );
}
