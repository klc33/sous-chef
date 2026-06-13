// The ingredient-substitution result — curated safe swaps, or an honest "no safe substitute" (FR-016, FR-020).
//
// Renders the backend's SubstitutionResult { ingredient, substitutes[], none_safe }. Substitutes are
// curated and allergen-safe (never invented). When `none_safe` is true the card says so plainly rather
// than guessing — the safe answer is sometimes "there isn't one".

// `substitution` is a SubstitutionResult DTO.
export default function SubstitutionCard({ substitution }) {
  const { ingredient, substitutes, none_safe } = substitution;
  return (
    <section className="subst">
      <h2>Substitute for {ingredient}</h2>
      {none_safe || !substitutes || substitutes.length === 0 ? (
        <p className="subst__none">No safe substitute for your constraints.</p>
      ) : (
        <ul className="subst__list">
          {substitutes.map((s, i) => (
            <li key={i}>{s}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
