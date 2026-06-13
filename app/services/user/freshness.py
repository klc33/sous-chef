"""Freshness — the per-cook seen-history that keeps repeated requests returning new recipes (US2).

A single global per-cook set of already-surfaced recipe ids. The retrieval path reads it
(`exclude_seen`) to drop recipes the cook has already been shown, records what it surfaces
(`record_seen`), and resets it when the cook has exhausted the compliant corpus
(`reset_if_exhausted`) so discovery never dead-ends (FR-010..013, SC-001).

Two invariants carry the safety/UX guarantees:

  * **Favorites are exempt.** `record_seen` never writes a favorited recipe to the history, so a
    saved recipe is never suppressed from future results (data-model.md invariant).
  * **Per-cook isolation.** Every call is scoped to one `profile_id`; one cook's history can never
    exclude (or reset) another cook's results — the repo queries are profile-scoped.

This module is the only freshness policy holder; `repo.seen_history` does the DB access and
`repo.favorites.exists` answers the exemption check.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.repo import favorites as repo_favorites
from app.repo import profiles as repo_profiles
from app.repo import seen_history as repo_seen


def exclude_seen(session: Session, profile_id: str) -> list[uuid.UUID]:
    """Return the recipe ids this cook has already been shown — the freshness exclusion set.

    Reads the cook's seen-history rows (profile-scoped) and projects out their recipe ids, which the
    caller hands to `search_by_vector` as `exclude_ids` so already-surfaced recipes are dropped in SQL.
    An empty list (no history yet) simply means nothing is excluded.
    """
    return [row.recipe_id for row in repo_seen.list(session, profile_id)]


def record_seen(session: Session, profile_id: str, recipe_ids: Iterable[uuid.UUID]) -> None:
    """Record the recipes just surfaced to a cook so they are excluded next time — favorites exempt.

    Skips two kinds of id: any already in the cook's history (defensive de-dupe, so re-recording can't
    pile up duplicate rows) and any the cook has favorited (favorites are never suppressed from future
    results — the data-model invariant). Everything else is inserted, profile-scoped, in the caller's
    transaction.
    """
    ids = list(recipe_ids)
    if not ids:
        return  # nothing surfaced — record nothing (and don't create a profile row for an empty result)

    # Ensure the cook's profile row exists so the seen_history.profile_id FK is satisfied: a cook can chat
    # (which surfaces recipes and records them) before ever saving constraints via PUT /profile. Mirrors
    # the favorites save path (services/user/favorites.py), which ensure_exists() for the same reason.
    repo_profiles.ensure_exists(session, profile_id)

    already = set(exclude_seen(session, profile_id))
    for recipe_id in ids:
        if recipe_id in already:
            continue  # already tracked — don't write a duplicate seen-history row
        if repo_favorites.exists(session, profile_id, recipe_id):
            continue  # favorites are exempt from freshness — never record (and so never suppress) them
        repo_seen.insert(session, profile_id, recipe_id)
        already.add(recipe_id)


def reset_if_exhausted(
    session: Session, profile_id: str, *, found_count: int, needed: int
) -> bool:
    """Clear the cook's history when their seen-set has exhausted the compliant corpus; return whether it did.

    "Exhausted" means retrieval surfaced fewer than `needed` (k) fresh compliant recipes **and** the cook
    actually has seen-history to blame — i.e. the shortfall is "you've seen everything", not "the corpus
    is just that small". In that case we wipe this cook's history (profile-scoped) and return True so the
    caller re-queries and discovery resumes (FR-012/SC-001). A genuine scarcity shortfall (no history)
    returns False so the caller doesn't pointlessly re-run the identical query.
    """
    if found_count >= needed:
        return False  # enough fresh results — nothing to reset
    if not repo_seen.list(session, profile_id):
        return False  # nothing seen yet — the shortfall is real scarcity, not exhaustion
    repo_seen.clear(session, profile_id)
    return True
