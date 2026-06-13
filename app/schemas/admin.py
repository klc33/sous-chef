"""Pydantic response models for the operator-only admin surface (corpus / evals / metrics).

These mirror contracts/admin.openapi.yaml. They are a SEPARATE surface from the cook schemas: the
corpus projection intentionally carries operator-only fields (source/source_id, allergen + diet tags)
the cook card omits, since the operator is inspecting the ingested corpus, not browsing to cook. The
category enum is reused from app.models.recipe so the five fixed values have one source of truth.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.recipe import Category

__all__ = [
    "GateResult",
    "RecipeCardAdmin",
    "CorpusPage",
    "EvalRunResult",
    "ClassifierMetrics",
    "RoutingSplit",
    "PhoenixLinks",
    "MetricsSummary",
]


class GateResult(BaseModel):
    """One eval gate's outcome: name, a PASS/FAIL/SKIP verdict, and a measured-vs-threshold detail line.

    Mirrors the in-process `evals.run_evals.GateResult` dataclass field-for-field so the runner's results
    serialize straight onto the wire without a translation layer.
    """

    name: str
    status: str = Field(description="PASS | FAIL | SKIP")
    detail: str = Field(description="Human-readable measured-vs-threshold line.")


class RecipeCardAdmin(BaseModel):
    """A read-only corpus row for operator inspection: identity, provenance, and allergen/diet tags.

    Richer than the cook's RecipeCard on purpose — the operator may see the allergen union and diet
    flags (which the wall uses) to audit ingestion, where the cook only ever sees safe, filtered cards.
    """

    id: str
    title: str
    category: Category
    cuisine: str | None = None
    source: str
    source_id: str
    allergens: list[str]
    diet_flags: list[str] = Field(description="Diet tags the recipe satisfies (vegetarian/vegan/...).")


class CorpusPage(BaseModel):
    """One page of corpus rows plus the total count, so the dashboard can render pager controls."""

    items: list[RecipeCardAdmin]
    total: int
    page: int
    page_size: int


class EvalRunResult(BaseModel):
    """The result of an on-demand eval run: the gate rows, an echo of the thresholds, and a timestamp.

    `thresholds` echoes `eval_thresholds.yaml` verbatim so the page can show measured-vs-floor side by
    side; `ran_at` is an ISO-8601 instant marking when the run completed.
    """

    gates: list[GateResult]
    thresholds: dict = Field(description="Echo of eval_thresholds.yaml (measured-vs-floor display).")
    ran_at: str = Field(description="ISO-8601 timestamp of the run.")


class ClassifierMetrics(BaseModel):
    """Served-classifier quality: macro-F1 plus per-class F1, scored on the held-out eval testset."""

    macro_f1: float
    per_class: dict[str, float] = Field(default_factory=dict)


class RoutingSplit(BaseModel):
    """The workflow-vs-agent turn split derived from the router's lightweight Redis counters.

    `workflow_pct`/`agent_pct` are percentages of `total_turns` (the sum of the two counters); when no
    turns have been routed yet the percentages are 0 so the page renders a clean empty state.
    """

    workflow_pct: float
    agent_pct: float
    total_turns: int


class PhoenixLinks(BaseModel):
    """Deep-link only Phoenix pointers — per-turn token cost is viewed in Phoenix, not rolled up here."""

    ui_base_url: str | None = None
    trace_deep_link: str | None = None


class MetricsSummary(BaseModel):
    """The operator metrics summary: classifier quality, routing split, last gate status, Phoenix links."""

    classifier: ClassifierMetrics
    routing: RoutingSplit
    gates: list[GateResult]
    phoenix: PhoenixLinks | None = None
