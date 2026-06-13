"""Semantic recipe retrieval — embed the query, search, apply the wall, return ranked real cards.

This is the heart of US1 (conversational ranked discovery). `search(...)` runs the grounded retrieval
pipeline end to end:

    embed query → repo.search_by_vector (category + diet pre-filtered, OVER-FETCHED pool)
        → constraint_guard wall (fail-closed) over the pool → take top-k → recipe_view cards
        → LLM phrases a grounded reply about ONLY those real cards.

Two design points carry the safety + grounding guarantees:

  * **Over-fetch, then trim.** `search_by_vector` returns a candidate pool (`retrieval_candidate_pool`,
    ~20) ordered by cosine distance, NOT the final 3 — the allergen wall trims afterward, so 3 compliant
    cards still surface even when violators rank higher (FR-006/FR-007/FR-008).
  * **The LLM only explains.** Cards come from stored rows via `recipe_view` (the wall choke point); the
    Groq call writes the natural-language reply but never invents or re-orders recipes (FR-005). On no
    compliant match we return an honest empty result (FR-009), and the LLM is not even called.

`retrieve(...)` exposes the wall-cleared rows themselves (top-k) for callers that need the underlying
recipe rather than a card — notably the `nutrition_q` handler, which scales the matched recipe's stored
nutrition. Freshness (excluding already-seen recipes) is layered on top of this in US2 via `exclude_ids`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.infra import embeddings, llm
from app.models.recipe import Recipe
from app.repo import recipes as repo_recipes
from app.schemas.recipe import Category, RecipeCard
from app.services.shared import recipe_view
from app.services.user import constraint_guard, freshness
from app.services.user.constraint_guard import ConstraintProfile

# The reply writer's system prompt (prompts are code; never inline — golden rule #7 / Constitution VII).
_EXPLAINER_PROMPT = Path("prompts/recipe_explainer.md")

# Honest "no compliant recipe" reply (FR-009) — never a fabricated or constraint-relaxed suggestion.
_EMPTY_REPLY = (
    "I couldn't find a recipe that matches that and fits your preferences. "
    "Try a different cuisine, ingredient, or meal."
)

# Cap the LLM reply so a chatty model can't run up tokens on what is a short helper sentence.
_REPLY_MAX_TOKENS = 200


@dataclass(frozen=True)
class RagResult:
    """One search turn's output: the ranked wall-cleared cards and the grounded reply that frames them."""

    cards: list[RecipeCard] = field(default_factory=list)
    reply: str = ""


def retrieve(
    session: Session,
    query: str,
    cp: ConstraintProfile,
    *,
    category: Category | None = None,
    k: int = 3,
    exclude_ids: list[uuid.UUID] | None = None,
) -> list[Recipe]:
    """Return up to `k` wall-cleared recipe rows best matching `query`, ranked by semantic relevance.

    Embeds the query, pulls the over-fetched candidate pool from the repo (category + diet + seen-history
    pre-filtered in SQL), runs the deterministic allergen wall over that pool (`constraint_guard.filter`,
    fail-closed), and slices the top `k` survivors. Returning rows (not cards) lets the nutrition path
    reuse the same grounded retrieval; cook-facing card building still happens only through `recipe_view`.
    """
    settings = get_settings()
    query_vec = embeddings.embed_query(query)
    # Over-fetch a candidate pool so the wall can trim violators and still leave `k` compliant rows.
    pool = repo_recipes.search_by_vector(
        session,
        query_vec,
        category=category.value if category is not None else None,
        diet=cp.diet,
        exclude_ids=exclude_ids,
        pool=settings.retrieval_candidate_pool,
    )
    # The wall is the grade: only non-violating recipes survive, fail-closed on uncertain allergens.
    safe = constraint_guard.filter(pool, cp)
    return safe[:k]


def search(
    session: Session,
    query: str,
    cp: ConstraintProfile,
    profile_id: str,
    *,
    category: Category | None = None,
    k: int = 3,
) -> RagResult:
    """Run the full search turn: freshness-aware retrieval, ranked cards, and a grounded reply.

    Retrieves through `_retrieve_fresh` (which excludes this cook's seen recipes and resets on
    exhaustion), turns the survivors into cards through the `recipe_view` choke point (so the wall is
    enforced on the surfaced cards as well), records the surfaced ids to seen-history so the next repeat
    returns new recipes (US2; favorites stay exempt), and asks the LLM to write a short reply about ONLY
    those real cards. When nothing compliant matches, returns an honest empty result without calling the
    LLM and without recording anything (no fabrication — FR-009).
    """
    recipes, cards = fresh_cards(session, query, cp, profile_id, category=category, k=k)
    if not cards:
        return RagResult(cards=[], reply=_EMPTY_REPLY)
    reply = _explain(query, recipes)
    return RagResult(cards=cards, reply=reply)


def fresh_cards(
    session: Session,
    query: str,
    cp: ConstraintProfile,
    profile_id: str,
    *,
    category: Category | None = None,
    k: int = 3,
) -> tuple[list[Recipe], list[RecipeCard]]:
    """Freshness-aware retrieval that returns BOTH the wall-cleared rows and their cards, recording seen.

    The shared retrieval core behind `search` (which adds the LLM reply) and the agent's `search_recipes`
    tool (which composes its own narrative): runs `_retrieve_fresh` (seen-history excluded, reset on
    exhaustion), builds cards through the `recipe_view` wall choke point, and records the surfaced ids to
    seen-history so a repeat surfaces new recipes (favorites stay exempt). Returning the rows alongside the
    cards lets the meal-plan agent reason over real recipes (cuisine, ingredients) while cooks still only
    ever receive wall-cleared cards. An empty result records nothing.
    """
    recipes = _retrieve_fresh(session, query, cp, profile_id, category=category, k=k)
    cards = recipe_view.to_cards(recipes, cp)
    freshness.record_seen(session, profile_id, [r.id for r in recipes])
    return recipes, cards


def _retrieve_fresh(
    session: Session,
    query: str,
    cp: ConstraintProfile,
    profile_id: str,
    *,
    category: Category | None,
    k: int,
) -> list[Recipe]:
    """Retrieve top-`k` wall-cleared rows while skipping recipes this cook has already been shown.

    First excludes the cook's seen-history (`exclude_seen`) so a repeat query returns new recipes. If the
    fresh pool yields fewer than `k` compliant rows AND the cook has exhausted what they've seen,
    `reset_if_exhausted` wipes their history and we re-query once with no exclusion so discovery resumes
    (FR-010..013). The favorites path is unaffected — favorites are never excluded here and never recorded.
    """
    exclude_ids = freshness.exclude_seen(session, profile_id)
    recipes = retrieve(session, query, cp, category=category, k=k, exclude_ids=exclude_ids)
    if len(recipes) < k and freshness.reset_if_exhausted(
        session, profile_id, found_count=len(recipes), needed=k
    ):
        # History was cleared (cook had seen everything) — re-query once, now unrestricted.
        recipes = retrieve(session, query, cp, category=category, k=k)
    return recipes


def _explain(query: str, recipes: list[Recipe]) -> str:
    """Ask the LLM to phrase a short grounded reply about the retrieved recipes; fall back deterministically.

    Builds a numbered list of the real recipes (title + key ingredients) and hands it to Groq behind the
    `recipe_explainer` system prompt, which forbids invention or re-ordering. Any LLM failure or empty
    completion falls back to a plain, grounded sentence listing the real titles — so a turn never fails
    and never invents, even when the hosted model is unavailable.
    """
    catalog = "\n".join(
        f"{i}. {r.title} — key ingredients: {', '.join(ing.name for ing in r.ingredients[:4]) or 'n/a'}"
        for i, r in enumerate(recipes, start=1)
    )
    messages = [
        {"role": "system", "content": _EXPLAINER_PROMPT.read_text(encoding="utf-8")},
        {
            "role": "user",
            "content": f"The cook asked: {query!r}\n\nRetrieved recipes:\n{catalog}",
        },
    ]
    try:
        response = llm.chat(messages, max_tokens=_REPLY_MAX_TOKENS)
        content = (response.choices[0].message.content or "").strip()
        if content:
            return content
    except Exception:  # noqa: BLE001 — hosted LLM is best-effort; never fail or invent the turn on its error
        pass
    return _fallback_reply(recipes)


def _fallback_reply(recipes: list[Recipe]) -> str:
    """Build a plain grounded reply naming the real retrieved recipes (used when the LLM is unavailable).

    Lists the actual recipe titles so the reply stays truthful and useful without the hosted model —
    grounding holds because every title comes from a stored row that already cleared the wall.
    """
    titles = ", ".join(r.title for r in recipes)
    return f"Here are some recipes that fit: {titles}. Open a card for the full steps."
