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
from sqlalchemy.orm import Session, selectinload

# A representative allergic cook used to gauge surfaceability (two common allergens).
_REPRESENTATIVE = ConstraintProfile(
    diet=constraint_guard.Diet.NONE,
    allergies=frozenset({Allergen.MILK.value, Allergen.PEANUTS.value}),
)


def _has_usable_nutrition(recipe: Recipe) -> bool:
    """True when the recipe carries at least one non-zero macro (the 'usable nutrition' surface state).

    Mirrors the cook-facing contract C1: a nutrition row whose calories/protein/carbs/fat are all zero (or
    absent) renders as "not available", so it does NOT count as usable coverage here.
    """
    n = recipe.nutrition
    if n is None:
        return False
    return any(float(getattr(n, f)) > 0.0 for f in ("calories", "protein_g", "carbs_g", "fat_g"))


@dataclass
class CoverageReport:
    """The computed coverage numbers, suitable for printing or asserting in a test."""

    total_complete: int
    per_category: dict[str, int]
    pct_allergen_certain: float
    surfaceable_for_representative: int
    # Nutrition coverage (006, SC-001): how many recipes show real macros vs. "not available", and how
    # many of those are exact (authoritative source nutrition) rather than approximate estimates.
    usable_nutrition: int
    exact_nutrition: int
    nutrition_unavailable: int
    # Image coverage (006, US3 acceptance #4): real source photo vs. generic category placeholder.
    with_image: int
    placeholder_image: int


def compute(session: Session) -> CoverageReport:
    """Compute the coverage report over all complete recipes currently in the corpus.

    Counts complete recipes per category, the share that are allergen-certain, how many survive the
    wall for the representative allergic profile, plus the 006 data-quality coverage: usable/exact/absent
    nutrition and source-image vs. placeholder share. Nutrition rows are eager-loaded to avoid an N+1.
    """
    complete = list(
        session.execute(
            select(Recipe)
            .where(Recipe.is_complete.is_(True))
            .options(selectinload(Recipe.nutrition))
        ).scalars()
    )
    total = len(complete)

    per_category = {cat.value: 0 for cat in Category}
    certain_count = 0
    usable_nutrition = exact_nutrition = with_image = 0
    for recipe in complete:
        per_category[recipe.category] = per_category.get(recipe.category, 0) + 1
        if recipe.allergen_certain:
            certain_count += 1
        if _has_usable_nutrition(recipe):
            usable_nutrition += 1
        if recipe.nutrition is not None and not recipe.nutrition.is_approximate:
            exact_nutrition += 1
        if recipe.image_url:
            with_image += 1

    pct_certain = (certain_count / total * 100.0) if total else 0.0
    surfaceable = len(constraint_guard.filter(complete, _REPRESENTATIVE))

    return CoverageReport(
        total_complete=total,
        per_category=per_category,
        pct_allergen_certain=round(pct_certain, 1),
        surfaceable_for_representative=surfaceable,
        usable_nutrition=usable_nutrition,
        exact_nutrition=exact_nutrition,
        nutrition_unavailable=total - usable_nutrition,
        with_image=with_image,
        placeholder_image=total - with_image,
    )


def format_report(report: CoverageReport) -> str:
    """Render the coverage report as a short human-readable block for the ingestion log."""
    # ASCII-only separators: the report is printed to the console, which on Windows is cp1252 and
    # cannot encode Unicode box-drawing glyphs (it raised UnicodeEncodeError mid-run).
    rule = "-" * 52
    total = report.total_complete

    def _pct(n: int) -> str:
        """Format a count as a percentage of the complete corpus (0% when the corpus is empty)."""
        return f"{(n / total * 100.0):.1f}%" if total else "0.0%"

    lines = [
        rule,
        "Ingestion coverage",
        rule,
        f"  complete recipes: {report.total_complete}",
        "  per category:",
        *[f"    {cat:<11} {count}" for cat, count in report.per_category.items()],
        f"  allergen_certain: {report.pct_allergen_certain}%",
        f"  surfaceable to a milk+peanut-allergic cook: {report.surfaceable_for_representative}",
        "  nutrition:",
        f"    usable (real macros) : {report.usable_nutrition} ({_pct(report.usable_nutrition)})",
        f"    of which exact       : {report.exact_nutrition} ({_pct(report.exact_nutrition)})",
        f"    not available        : {report.nutrition_unavailable} ({_pct(report.nutrition_unavailable)})",
        "  images:",
        f"    source photo         : {report.with_image} ({_pct(report.with_image)})",
        f"    category placeholder : {report.placeholder_image} ({_pct(report.placeholder_image)})",
        rule,
    ]
    return "\n".join(lines)
