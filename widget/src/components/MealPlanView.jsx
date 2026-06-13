// The multi-day meal plan view — days × recipe cards, with the variety summary and any shortfall note (FR-016).
//
// Renders the backend's MealPlan { days:[{day, recipe}], distinct_cuisines, shortfall_note? }. Each day's
// recipe is a wall-cleared RecipeCard, so the plan can drill into detail and save favorites like any card.
// When the backend could not meet the requested length or variety, `shortfall_note` is shown honestly.

import RecipeCard from "./RecipeCard.jsx";

// `plan` is a MealPlan DTO; `onOpen`/`onToggleFavorite` thread through to each day's card.
export default function MealPlanView({ plan, onOpen, onToggleFavorite }) {
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
            <RecipeCard recipe={d.recipe} onOpen={onOpen} onToggleFavorite={onToggleFavorite} />
          </div>
        ))}
      </div>
    </section>
  );
}
