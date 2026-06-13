// The five fixed category chips (FR-014).
//
// Categories are fixed and never guessed, so the chips come straight from lib/categories.js. Selecting a
// chip triggers a category browse in the parent (which calls GET /recipes?category=<value>). The cook only
// ever sees the human label; the underscored value travels on the wire.

import { CATEGORIES } from "../lib/categories.js";

// `selected` is the currently active category value (or null); `onSelect(value)` asks the parent to browse.
export default function CategoryChips({ selected, onSelect }) {
  return (
    <div className="chips" role="group" aria-label="Browse by category">
      {CATEGORIES.map((c) => (
        <button
          key={c.value}
          type="button"
          className={`chip${selected === c.value ? " chip--active" : ""}`}
          aria-pressed={selected === c.value}
          onClick={() => onSelect(c.value)}
        >
          {c.label}
        </button>
      ))}
    </div>
  );
}
