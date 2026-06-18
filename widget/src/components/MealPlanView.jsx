// The multi-day meal plan view — each day shows breakfast, lunch, and dinner cards (FR-014..017).
//
// Renders the backend's MealPlan { days:[{day, breakfast, lunch, dinner}], distinct_cuisines, shortfall_note? }.
// Every meal is a wall-cleared RecipeCard (or null when the corpus couldn't fill that slot), so the plan can
// drill into detail and save favorites like any card. When the backend could not meet the requested length
// or fill a meal, `shortfall_note` is shown honestly. An unfilled slot renders a calm placeholder, never a
// fabricated dish.

import RecipeCard from "./RecipeCard.jsx";

// The three meal slots, in display order, paired with the label shown above each card.
const SLOTS = [
  ["breakfast", "Breakfast"],
  ["lunch", "Lunch"],
  ["dinner", "Dinner"],
];

// `plan` is a MealPlan DTO; `onOpen`/`onToggleFavorite` thread through to each meal's card. `favoriteIds`
// is the set of saved recipe ids so each meal's heart reflects (and instantly flips) its favorite state —
// without it the plan's hearts would never fill. Defaults to an empty set so the component is safe alone.
export default function MealPlanView({ plan, onOpen, onToggleFavorite, favoriteIds = new Set() }) {
  return (
    <section className="plan">
      <header className="plan__head">
        <h2>Your meal plan</h2>
        <p className="plan__variety">
          {plan.distinct_cuisines} distinct cuisine{plan.distinct_cuisines === 1 ? "" : "s"}
        </p>
        {plan.shortfall_note && <p className="plan__shortfall">{plan.shortfall_note}</p>}
      </header>

      <div className="plan__days">
        {plan.days.map((d) => (
          <div className="plan__day" key={d.day}>
            <span className="plan__daynum">Day {d.day}</span>
            <div className="plan__meals">
              {SLOTS.map(([key, label]) => (
                <div className="plan__meal" key={key}>
                  <span className="plan__meal-label">{label}</span>
                  {d[key] ? (
                    <RecipeCard
                      recipe={d[key]}
                      onOpen={onOpen}
                      onToggleFavorite={onToggleFavorite}
                      isFavorite={favoriteIds.has(d[key].id)}
                    />
                  ) : (
                    <p className="plan__meal-empty">No {label.toLowerCase()} recipe available.</p>
                  )}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
