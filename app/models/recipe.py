"""ORM models for the grounded recipe corpus: Recipe, Ingredient, NutritionCache.

These are the read side of the cook-facing surface and the write target of the offline ingestion
pipeline. Steps are stored verbatim (`steps` text[]) and rendered as-is — never regenerated. The
allergen/diet booleans and `allergen_certain` are precomputed at ingestion so the deterministic wall
(`services/user/constraint_guard.py`) can decide visibility with plain Python, no model calls.

Domain enums live here as `StrEnum`s (their values are the strings persisted/returned) and are reused
by the Pydantic schemas and the wall. See specs/002-catalog-wall-favorites/data-model.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import get_settings
from app.models.base import Base


class Category(StrEnum):
    """The five fixed browse categories; each recipe has exactly one (a metadata filter, not a guess)."""

    HOT_DRINK = "hot_drink"
    COLD_DRINK = "cold_drink"
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"


class Allergen(StrEnum):
    """The nine supported allergens; stored as text[] sets on recipes and per-ingredient matches."""

    PEANUTS = "peanuts"
    TREE_NUTS = "tree_nuts"
    MILK = "milk"
    EGGS = "eggs"
    WHEAT_GLUTEN = "wheat_gluten"
    SOY = "soy"
    FISH = "fish"
    SHELLFISH = "shellfish"
    SESAME = "sesame"


class Diet(StrEnum):
    """A cook's diet preference. `none` never filters; the others require the matching recipe flag."""

    NONE = "none"
    VEGETARIAN = "vegetarian"
    VEGAN = "vegan"
    PESCATARIAN = "pescatarian"


class Source(StrEnum):
    """Origin of a recipe; with `source_id` it forms the idempotency key for ingestion upserts."""

    THEMEALDB = "themealdb"
    THECOCKTAILDB = "thecocktaildb"
    KAGGLE = "kaggle"


class Recipe(Base):
    """A grounded recipe: verbatim steps plus precomputed category/allergen/diet/completeness flags.

    `is_complete` gates surfacing — only complete recipes are candidates for browse, enforced at the
    repo query layer before the wall even runs. `(source, source_id)` is the natural idempotency key.
    """

    __tablename__ = "recipes"
    __table_args__ = (
        # The natural idempotency key: re-ingesting the same source row upserts rather than duplicates.
        UniqueConstraint("source", "source_id", name="uq_recipes_source_source_id"),
        # Browse is an indexed (category, is_complete) lookup, never a scan.
        Index("ix_recipes_category_is_complete", "category", "is_complete"),
        # Persist enums as constrained strings (StrEnum values) rather than native PG enums.
        CheckConstraint(
            "category IN ('hot_drink', 'cold_drink', 'breakfast', 'lunch', 'dinner')",
            name="ck_recipes_category",
        ),
        CheckConstraint(
            "source IN ('themealdb', 'thecocktaildb', 'kaggle')",
            name="ck_recipes_source",
        ),
    )

    # Surrogate UUID PK generated app-side so ingestion can reference children before a flush.
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    cuisine: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_time_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Source servings; the nutrition basis. Defaults to 1 when the source does not state servings.
    servings: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Ordered instruction lines, stored and rendered verbatim (grounding invariant).
    steps: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Union of allergens detected across ingredients (+OFF tags); may be empty.
    allergens: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    # False ⇒ an ingredient could not be recognized ⇒ the wall treats the recipe as possibly any allergen.
    allergen_certain: Mapped[bool] = mapped_column(nullable=False, default=True)
    is_vegetarian: Mapped[bool] = mapped_column(nullable=False, default=False)
    is_vegan: Mapped[bool] = mapped_column(nullable=False, default=False)
    is_pescatarian: Mapped[bool] = mapped_column(nullable=False, default=False)
    # True only when category + ≥1 ingredient + allergens + nutrition are all present (surfacing gate).
    is_complete: Mapped[bool] = mapped_column(nullable=False, default=False)
    # Semantic embedding of the recipe (title + cuisine + category + key ingredients), written offline by
    # ingestion (infra.embeddings). Nullable: a recipe with no embedding is simply skipped by vector
    # search (`embedding IS NOT NULL`). The width is pinned to the migration's vector(1536) and asserted
    # against config at startup, so a model/dim change must go through a new migration, never a silent
    # mismatch. Cosine distance (`<=>`) ranks candidates; the HNSW index in 0003 backs the order-by.
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(get_settings().embeddings_dim), nullable=True
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Children load eagerly when the repo requests them; cascade keeps a recipe's rows atomic.
    ingredients: Mapped[list[Ingredient]] = relationship(
        back_populates="recipe",
        cascade="all, delete-orphan",
        order_by="Ingredient.position",
    )
    nutrition: Mapped[NutritionCache | None] = relationship(
        back_populates="recipe",
        cascade="all, delete-orphan",
        uselist=False,
    )


class Ingredient(Base):
    """A parsed ingredient line: provenance for allergens/nutrition and the card's key-ingredient list.

    `raw_text` is always retained (the original source line) even when quantity/unit could not be parsed,
    so nothing is invented and the parse is auditable.
    """

    __tablename__ = "ingredients"
    __table_args__ = (Index("ix_ingredients_recipe_id", "recipe_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recipe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recipes.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Display/stored order; the first few drive RecipeCard.key_ingredients.
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    unit: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    # Per-ingredient allergen matches (may be empty); recipes.allergens is the union of these.
    allergen_tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)

    recipe: Mapped[Recipe] = relationship(back_populates="ingredients")


class NutritionCache(Base):
    """Per-recipe nutrition precomputed at ingestion; runtime reads + scales, never calling OFF live.

    One row per recipe (PK == FK). Totals correspond to `basis_servings` (= recipes.servings);
    `is_approximate` is true whenever any ingredient was unmapped or unquantified.
    """

    __tablename__ = "nutrition_cache"

    recipe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recipes.id", ondelete="CASCADE"),
        primary_key=True,
    )
    basis_servings: Mapped[int] = mapped_column(Integer, nullable=False)
    calories: Mapped[float] = mapped_column(Numeric, nullable=False)
    protein_g: Mapped[float] = mapped_column(Numeric, nullable=False)
    carbs_g: Mapped[float] = mapped_column(Numeric, nullable=False)
    fat_g: Mapped[float] = mapped_column(Numeric, nullable=False)
    is_approximate: Mapped[bool] = mapped_column(nullable=False, default=False)
    unmapped_ingredient_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    recipe: Mapped[Recipe] = relationship(back_populates="nutrition")
