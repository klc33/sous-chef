"""ORM model package. Re-exports Base/metadata so Alembic can target app.models.

Every model module MUST be imported here so its tables are registered on Base.metadata and picked up
by `alembic revision --autogenerate`. Importing the classes (even if only for the side effect of
registration) is intentional — do not remove them.
"""

from app.models.base import Base
from app.models.profile import Favorite, Profile, SeenHistory
from app.models.recipe import (
    Allergen,
    Category,
    Diet,
    Ingredient,
    NutritionCache,
    Recipe,
    Source,
)

metadata = Base.metadata

__all__ = [
    "Base",
    "metadata",
    # recipe.py
    "Recipe",
    "Ingredient",
    "NutritionCache",
    "Category",
    "Allergen",
    "Diet",
    "Source",
    # profile.py
    "Profile",
    "Favorite",
    "SeenHistory",
]
