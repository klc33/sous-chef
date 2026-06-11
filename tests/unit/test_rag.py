"""Unit tests for semantic retrieval (services/user/rag.py) — US1, with embeddings + LLM mocked.

These pin the grounded retrieval contract in isolation (no DB, no network):
  * the query is embedded and the repo is searched with the cook's category + diet pre-filter (FR-007),
  * the over-fetched pool is trimmed by the wall and capped at k=3 ranked cards (FR-006/FR-008),
  * a no-compliant-match turn returns an honest empty result and never calls the LLM (FR-005/FR-009),
  * the reply is the LLM's text when it answers, and a grounded fallback when it fails.

`search_by_vector`, `embed_query`, and `llm_groq.chat` are monkeypatched so the test exercises rag's own
logic (pool → wall → top-k → cards → reply), not the providers it depends on.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from app.models.recipe import Category, Diet
from app.services.user import rag
from app.services.user.constraint_guard import ConstraintProfile


def _recipe(
    rid: str,
    title: str,
    *,
    allergens: tuple[str, ...] = (),
    certain: bool = True,
    vegan: bool = True,
) -> SimpleNamespace:
    """Build a minimal recipe stand-in exposing exactly the fields the wall + recipe_view read.

    Defaults describe a permissive, dinner recipe with one ingredient; a test overrides only the allergen
    or diet attribute it is probing so the rest stays out of the way.
    """
    return SimpleNamespace(
        id=rid,
        title=title,
        category="dinner",
        image_url=None,
        ingredients=[SimpleNamespace(name="tomato"), SimpleNamespace(name="basil")],
        allergens=list(allergens),
        allergen_certain=certain,
        is_vegan=vegan,
        is_vegetarian=True,
        is_pescatarian=True,
        nutrition=None,
    )


def _fake_chat_response(text: str) -> Any:
    """Shape a stand-in mirroring the Groq response surface rag reads (`choices[0].message.content`)."""
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


@pytest.fixture
def patched_rag(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch rag's embeddings + LLM and capture the args `search_by_vector` is called with.

    Returns a mutable dict whose `pool` the test sets (the rows the repo "returns") and whose `calls`
    records the kwargs passed to `search_by_vector`, so a test can assert the category/diet pre-filter.
    """
    state: dict[str, Any] = {"pool": [], "calls": [], "chat_count": 0}

    monkeypatch.setattr(rag.embeddings, "embed_query", lambda _text: [0.1] * 1536)

    def _fake_search(_session: Any, _vec: Any, **kwargs: Any) -> list[Any]:
        state["calls"].append(kwargs)
        return list(state["pool"])

    monkeypatch.setattr(rag.repo_recipes, "search_by_vector", _fake_search)

    def _fake_chat(_messages: Any, **_kwargs: Any) -> Any:
        state["chat_count"] += 1
        return _fake_chat_response("Here are a couple of grounded ideas.")

    monkeypatch.setattr(rag.llm_groq, "chat", _fake_chat)

    # Freshness is exercised by its own unit tests + the integration flow; here it is neutralised so
    # these tests isolate rag's pool→wall→top-k→cards→reply logic (no DB). No exclusion, no recording,
    # no exhaustion-reset — `record_seen`/`reset_if_exhausted` become no-ops over the mocked session.
    monkeypatch.setattr(rag.freshness, "exclude_seen", lambda _session, _profile: [])
    monkeypatch.setattr(rag.freshness, "record_seen", lambda _session, _profile, _ids: None)
    monkeypatch.setattr(
        rag.freshness, "reset_if_exhausted", lambda _session, _profile, **_kwargs: False
    )
    return state


def test_search_caps_at_three_ranked_cards(patched_rag: dict[str, Any]) -> None:
    """A pool larger than k yields exactly 3 cards in the repo's ranked order (FR-006)."""
    patched_rag["pool"] = [_recipe(str(i), f"Recipe {i}") for i in range(5)]
    result = rag.search(None, "thai dinner", ConstraintProfile.default(), "cook-1")

    assert [c.id for c in result.cards] == ["0", "1", "2"]
    assert result.reply == "Here are a couple of grounded ideas."


def test_search_pre_filters_by_category_and_diet(patched_rag: dict[str, Any]) -> None:
    """The repo search receives the cook's explicit category and diet as pre-filters (FR-007)."""
    patched_rag["pool"] = [_recipe("1", "Veg Curry")]
    cp = ConstraintProfile(diet=Diet.VEGAN, allergies=frozenset())
    rag.search(None, "curry", cp, "cook-1", category=Category("dinner"))

    call = patched_rag["calls"][0]
    assert call["category"] == "dinner"
    assert call["diet"] == Diet.VEGAN


def test_wall_trims_violators_before_top_k(patched_rag: dict[str, Any]) -> None:
    """The allergen wall drops violating rows from the pool so only compliant cards surface (FR-008)."""
    patched_rag["pool"] = [
        _recipe("nut", "Peanut Curry", allergens=("peanuts",)),
        _recipe("safe1", "Veg Stew"),
        _recipe("uncertain", "Mystery Stew", certain=False),
        _recipe("safe2", "Bean Chili"),
    ]
    cp = ConstraintProfile(diet=Diet.NONE, allergies=frozenset({"peanuts"}))
    result = rag.search(None, "stew", cp, "cook-1")

    surfaced = {c.id for c in result.cards}
    assert "nut" not in surfaced  # declared allergen present
    assert "uncertain" not in surfaced  # fail-closed on undetermined status
    assert surfaced == {"safe1", "safe2"}


def test_search_honest_empty_skips_llm(patched_rag: dict[str, Any]) -> None:
    """No compliant match → empty cards, the honest empty reply, and the LLM is never called (FR-009)."""
    patched_rag["pool"] = []
    result = rag.search(None, "nothing matches", ConstraintProfile.default(), "cook-1")

    assert result.cards == []
    assert "couldn't find" in result.reply.lower()
    assert patched_rag["chat_count"] == 0


def test_reply_falls_back_to_grounded_titles_on_llm_error(
    patched_rag: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the LLM raises, the reply falls back to a plain sentence naming the real recipes (grounding)."""
    patched_rag["pool"] = [_recipe("1", "Green Curry"), _recipe("2", "Pad Thai")]

    def _boom(_messages: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("provider down")

    monkeypatch.setattr(rag.llm_groq, "chat", _boom)
    result = rag.search(None, "thai", ConstraintProfile.default(), "cook-1")

    assert "Green Curry" in result.reply and "Pad Thai" in result.reply
    assert len(result.cards) == 2


def test_retrieve_k1_returns_single_best_row(patched_rag: dict[str, Any]) -> None:
    """retrieve(k=1) returns the single top wall-cleared row (the nutrition_q resolution path, FR-034)."""
    patched_rag["pool"] = [_recipe("a", "Tikka Masala"), _recipe("b", "Korma")]
    rows = rag.retrieve(None, "calories in tikka masala", ConstraintProfile.default(), k=1)

    assert len(rows) == 1
    assert rows[0].title == "Tikka Masala"
