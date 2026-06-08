"""Post-ingest coverage report — makes the fail-closed cost observable without weakening the wall.

Fail-closed means uncertain recipes are hidden from allergic cooks; that cost is otherwise silent. This
diagnostic surfaces it: per-category complete counts, the overall `% allergen_certain`, and how many
recipes a representative allergic profile can actually see (the wall applied). Improving the
`allergens.py` keyword map should move these numbers up — but tests still assert ZERO violations, never
a minimum surfaced count. Offline reporting only; not part of any request path.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.recipe import Allergen, Category, Recipe
from app.services.user import constraint_guard
from app.services.user.constraint_guard import ConstraintProfile
from sqlalchemy import select
from sqlalchemy.orm import Session

# A representative allergic cook used to gauge surfaceability (two common allergens).
_REPRESENTATIVE = ConstraintProfile(
    diet=constraint_guard.Diet.NONE,
    allergies=frozenset({Allergen.MILK.value, Allergen.PEANUTS.value}),
)


@dataclass
class CoverageReport:
    """The computed coverage numbers, suitable for printing or asserting in a test."""

    total_complete: int
    per_category: dict[str, int]
    pct_allergen_certain: float
    surfaceable_for_representative: int


def compute(session: Session) -> CoverageReport:
    """Compute the coverage report over all complete recipes currently in the corpus.

    Counts complete recipes per category, the share that are allergen-certain, and how many survive the
    wall for the representative allergic profile.
    """
    complete = list(
        session.execute(select(Recipe).where(Recipe.is_complete.is_(True))).scalars()
    )
    total = len(complete)

    per_category = {cat.value: 0 for cat in Category}
    certain_count = 0
    for recipe in complete:
        per_category[recipe.category] = per_category.get(recipe.category, 0) + 1
        if recipe.allergen_certain:
            certain_count += 1

    pct_certain = (certain_count / total * 100.0) if total else 0.0
    surfaceable = len(constraint_guard.filter(complete, _REPRESENTATIVE))

    return CoverageReport(
        total_complete=total,
        per_category=per_category,
        pct_allergen_certain=round(pct_certain, 1),
        surfaceable_for_representative=surfaceable,
    )


def format_report(report: CoverageReport) -> str:
    """Render the coverage report as a short human-readable block for the ingestion log."""
    # ASCII-only separators: the report is printed to the console, which on Windows is cp1252 and
    # cannot encode Unicode box-drawing glyphs (it raised UnicodeEncodeError mid-run).
    rule = "-" * 52
    lines = [
        rule,
        "Ingestion coverage",
        rule,
        f"  complete recipes: {report.total_complete}",
        "  per category:",
        *[f"    {cat:<11} {count}" for cat, count in report.per_category.items()],
        f"  allergen_certain: {report.pct_allergen_certain}%",
        f"  surfaceable to a milk+peanut-allergic cook: {report.surfaceable_for_representative}",
        rule,
    ]
    return "\n".join(lines)
