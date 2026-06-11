"""Unit tests for the per-cook freshness store (services/user/freshness.py) — US2, no DB.

These pin freshness's own policy in isolation by replacing the two repos it leans on with tiny
in-memory fakes (seen-history + favorites), so the tests exercise exclusion, recording, the
favorites exemption, per-cook isolation, and the reset-on-exhaustion decision — not the DB layer:

  * `exclude_seen` returns exactly the recipe ids recorded for that cook (FR-010);
  * `record_seen` skips favorites (never suppresses a saved recipe) and de-dupes (FR-011);
  * one cook's history never leaks into another's (per-cook isolation);
  * `reset_if_exhausted` clears only when the cook fell short AND has seen-history to blame (FR-012).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from app.services.user import freshness

# A throwaway session object — the fakes ignore it, but freshness still threads it through.
_SESSION: Any = object()


class _FakeSeenRepo:
    """In-memory stand-in for repo.seen_history: a profile_id → ordered list of recipe ids."""

    def __init__(self) -> None:
        """Start with no history for any cook."""
        self.rows: dict[str, list[uuid.UUID]] = {}

    def list(self, _session: Any, profile_id: str) -> list[Any]:
        """Return SeenHistory-like rows (only `recipe_id` is read) for one cook — profile-scoped."""
        return [SimpleNamespace(recipe_id=rid) for rid in self.rows.get(profile_id, [])]

    def insert(self, _session: Any, profile_id: str, recipe_id: uuid.UUID) -> None:
        """Append one shown recipe to the cook's history."""
        self.rows.setdefault(profile_id, []).append(recipe_id)

    def clear(self, _session: Any, profile_id: str) -> None:
        """Drop all of one cook's history (reset-on-exhaustion), leaving other cooks untouched."""
        self.rows.pop(profile_id, None)


class _FakeFavRepo:
    """In-memory stand-in for repo.favorites: the set of (profile_id, recipe_id) favorites."""

    def __init__(self) -> None:
        """Start with no favorites."""
        self.favs: set[tuple[str, uuid.UUID]] = set()

    def exists(self, _session: Any, profile_id: str, recipe_id: uuid.UUID) -> bool:
        """Answer the favorites-exemption check freshness makes before recording a recipe."""
        return (profile_id, recipe_id) in self.favs


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> tuple[_FakeSeenRepo, _FakeFavRepo]:
    """Swap freshness's seen-history + favorites repos for in-memory fakes; return them for assertions."""
    seen = _FakeSeenRepo()
    favs = _FakeFavRepo()
    monkeypatch.setattr(freshness, "repo_seen", seen)
    monkeypatch.setattr(freshness, "repo_favorites", favs)
    return seen, favs


def test_exclude_seen_returns_recorded_ids(fakes: tuple[_FakeSeenRepo, _FakeFavRepo]) -> None:
    """exclude_seen projects out exactly the recipe ids the cook has been shown (the exclusion set)."""
    seen, _ = fakes
    a, b = uuid.uuid4(), uuid.uuid4()
    seen.rows["cook-1"] = [a, b]

    assert set(freshness.exclude_seen(_SESSION, "cook-1")) == {a, b}
    assert freshness.exclude_seen(_SESSION, "cook-other") == []  # a cook with no history excludes nothing


def test_record_seen_records_new_ids(fakes: tuple[_FakeSeenRepo, _FakeFavRepo]) -> None:
    """record_seen writes the surfaced ids so a later exclude_seen returns them."""
    seen, _ = fakes
    a, b = uuid.uuid4(), uuid.uuid4()

    freshness.record_seen(_SESSION, "cook-1", [a, b])

    assert set(seen.rows["cook-1"]) == {a, b}


def test_record_seen_skips_favorites(fakes: tuple[_FakeSeenRepo, _FakeFavRepo]) -> None:
    """A favorited recipe is never recorded, so freshness can never suppress it from future results."""
    seen, favs = fakes
    fav_id, other_id = uuid.uuid4(), uuid.uuid4()
    favs.favs.add(("cook-1", fav_id))

    freshness.record_seen(_SESSION, "cook-1", [fav_id, other_id])

    assert seen.rows["cook-1"] == [other_id]  # the favorite was skipped


def test_record_seen_dedupes_against_existing_history(
    fakes: tuple[_FakeSeenRepo, _FakeFavRepo]
) -> None:
    """Re-recording an already-seen id is a no-op — no duplicate seen-history rows accumulate."""
    seen, _ = fakes
    a, b = uuid.uuid4(), uuid.uuid4()
    seen.rows["cook-1"] = [a]

    freshness.record_seen(_SESSION, "cook-1", [a, b, b])  # a already seen; b appears twice

    assert seen.rows["cook-1"] == [a, b]  # a not duplicated, b recorded once


def test_per_cook_isolation(fakes: tuple[_FakeSeenRepo, _FakeFavRepo]) -> None:
    """One cook's recorded history never appears in another cook's exclusion set."""
    seen, _ = fakes
    a = uuid.uuid4()

    freshness.record_seen(_SESSION, "cook-1", [a])

    assert freshness.exclude_seen(_SESSION, "cook-1") == [a]
    assert freshness.exclude_seen(_SESSION, "cook-2") == []  # cook-2 is unaffected


def test_reset_if_exhausted_clears_when_short_and_history_present(
    fakes: tuple[_FakeSeenRepo, _FakeFavRepo]
) -> None:
    """Falling short of k WITH history to blame is exhaustion: history is cleared and True returned."""
    seen, _ = fakes
    seen.rows["cook-1"] = [uuid.uuid4(), uuid.uuid4()]

    assert freshness.reset_if_exhausted(_SESSION, "cook-1", found_count=1, needed=3) is True
    assert "cook-1" not in seen.rows  # history wiped so a re-query can resume


def test_reset_if_exhausted_noop_when_enough_found(
    fakes: tuple[_FakeSeenRepo, _FakeFavRepo]
) -> None:
    """When k compliant recipes were found, there is nothing to reset (returns False, history kept)."""
    seen, _ = fakes
    rows = [uuid.uuid4()]
    seen.rows["cook-1"] = list(rows)

    assert freshness.reset_if_exhausted(_SESSION, "cook-1", found_count=3, needed=3) is False
    assert seen.rows["cook-1"] == rows  # untouched


def test_reset_if_exhausted_noop_when_no_history(
    fakes: tuple[_FakeSeenRepo, _FakeFavRepo]
) -> None:
    """A shortfall with no seen-history is genuine scarcity, not exhaustion — no reset, no re-query signal."""
    assert freshness.reset_if_exhausted(_SESSION, "cook-empty", found_count=0, needed=3) is False
