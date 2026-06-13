"""Every fixed Category has a committed placeholder SVG (US2 / FR-013/016, SC-004, contract C2).

The widget guarantees that a recipe without an `image_url` always resolves to a generic per-category
placeholder — so the image surface can never fall into a broken-image state. The resolution itself lives
in `widget/src/lib/images.js`, which has no JS unit-test runner (plan.md adds none). This dependency-free
Python guard pins the *precondition* that makes resolution infallible: for EVERY `Category` enum value the
backend can persist, the matching committed asset `widget/src/assets/placeholders/{value}.svg` exists.

If someone adds a sixth category without committing its placeholder, this fails — which is exactly the
moment the "can always resolve to a category placeholder" guarantee would otherwise silently break.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.models.recipe import Category

# tests/unit/ → repo root → the widget's committed placeholder assets.
_PLACEHOLDER_DIR = Path(__file__).resolve().parents[2] / "widget" / "src" / "assets" / "placeholders"


@pytest.mark.parametrize("category", list(Category), ids=lambda c: c.value)
def test_every_category_has_a_committed_placeholder(category: Category) -> None:
    """A committed, non-empty SVG exists for each canonical category value."""
    asset = _PLACEHOLDER_DIR / f"{category.value}.svg"
    assert asset.is_file(), f"missing committed placeholder for category '{category.value}': {asset}"
    # Non-empty so an accidentally-truncated asset (which would render blank) is also caught.
    assert asset.stat().st_size > 0, f"placeholder for '{category.value}' is empty: {asset}"


def test_no_stray_placeholders_beyond_the_fixed_categories() -> None:
    """Every committed SVG maps to a real Category — no orphaned/misnamed asset drifts in unnoticed."""
    valid = {f"{c.value}.svg" for c in Category}
    actual = {p.name for p in _PLACEHOLDER_DIR.glob("*.svg")}
    assert actual == valid, f"placeholder set drifted from the Category enum: {actual ^ valid}"
