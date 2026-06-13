// The cook's constraints panel — read and edit diet, allergies, and default servings (FR-013).
//
// Mirrors GET/PUT /profile. The backend is the source of truth and the wall is server-side; this form
// only caches for display and writes the cook's choices back. The option lists below MUST match the
// backend Diet/Allergen enums (app/models/recipe.py) so a save never trips a 422.

import { useState } from "react";

// Diet values — exactly the backend Diet StrEnum (none never filters).
const DIETS = [
  { value: "none", label: "No restriction" },
  { value: "vegetarian", label: "Vegetarian" },
  { value: "vegan", label: "Vegan" },
  { value: "pescatarian", label: "Pescatarian" },
];

// Allergen values — exactly the backend Allergen StrEnum (the wall enforces these server-side).
const ALLERGENS = [
  { value: "peanuts", label: "Peanuts" },
  { value: "tree_nuts", label: "Tree nuts" },
  { value: "milk", label: "Milk" },
  { value: "eggs", label: "Eggs" },
  { value: "wheat_gluten", label: "Wheat / gluten" },
  { value: "soy", label: "Soy" },
  { value: "fish", label: "Fish" },
  { value: "shellfish", label: "Shellfish" },
  { value: "sesame", label: "Sesame" },
];

// Render the editable constraints. `profile` is the current { diet, allergies[], default_servings };
// `onSave(next)` persists via the parent (which calls PUT /profile). Local state lets the cook edit
// before committing; `saving` disables the button during the round-trip.
export default function ConstraintsForm({ profile, onSave, saving }) {
  const [diet, setDiet] = useState(profile?.diet ?? "none");
  const [allergies, setAllergies] = useState(profile?.allergies ?? []);
  const [servings, setServings] = useState(profile?.default_servings ?? 2);

  // Toggle one allergen in/out of the selected set (checkbox change).
  function toggleAllergen(value) {
    setAllergies((cur) =>
      cur.includes(value) ? cur.filter((a) => a !== value) : [...cur, value]
    );
  }

  // Commit the edited constraints upward; servings is clamped to >= 1 to match the backend bound.
  function handleSubmit(e) {
    e.preventDefault();
    onSave({ diet, allergies, default_servings: Math.max(1, Number(servings) || 1) });
  }

  return (
    <form className="constraints" onSubmit={handleSubmit}>
      <h2>Your kitchen</h2>

      <label className="field">
        <span>Diet</span>
        <select value={diet} onChange={(e) => setDiet(e.target.value)}>
          {DIETS.map((d) => (
            <option key={d.value} value={d.value}>
              {d.label}
            </option>
          ))}
        </select>
      </label>

      <fieldset className="field">
        <legend>Allergies</legend>
        <div className="allergen-grid">
          {ALLERGENS.map((a) => (
            <label key={a.value} className="checkbox">
              <input
                type="checkbox"
                checked={allergies.includes(a.value)}
                onChange={() => toggleAllergen(a.value)}
              />
              {a.label}
            </label>
          ))}
        </div>
      </fieldset>

      <label className="field">
        <span>Default servings</span>
        <input
          type="number"
          min="1"
          value={servings}
          onChange={(e) => setServings(e.target.value)}
        />
      </label>

      <button type="submit" disabled={saving}>
        {saving ? "Saving…" : "Save constraints"}
      </button>
    </form>
  );
}
