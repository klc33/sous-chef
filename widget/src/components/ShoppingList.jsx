// The consolidated shopping list for a meal plan — scaled, deduplicated lines (FR-016).
//
// Renders the backend's ShoppingList { lines:[{ ingredient, quantity?, unit?, from_recipes[] }] }. The
// backend already merged compatible quantities and scaled to servings; the widget just lays it out. A line
// with no quantity/unit (un-parseable source line) shows just the ingredient name — nothing is invented.

// `list` is a ShoppingList DTO.
export default function ShoppingList({ list }) {
  // Format the optional quantity + unit into a short prefix ("200 g", "2", or "" when neither is known).
  function amount(line) {
    const qty = line.quantity != null ? String(line.quantity) : "";
    const unit = line.unit ? ` ${line.unit}` : "";
    return (qty + unit).trim();
  }

  return (
    <section className="shopping">
      <h2>Shopping list</h2>
      {list.lines.length === 0 ? (
        <p className="empty">Nothing to buy.</p>
      ) : (
        <ul className="shopping__lines">
          {list.lines.map((line, i) => (
            <li key={i}>
              <span className="shopping__amt">{amount(line)}</span>{" "}
              <span className="shopping__ing">{line.ingredient}</span>
              {line.from_recipes?.length > 0 && (
                <span className="shopping__from"> — {line.from_recipes.join(", ")}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
