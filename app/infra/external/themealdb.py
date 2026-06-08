"""TheMealDB adapter — fetch food recipes (offline, imported ONLY by the ingestion pipeline).

This is an `infra/external` adapter: it speaks HTTP to a third-party source and returns plain dicts. It
is used at ingestion time to build the corpus, never on a request path (the request path reads the DB).
The free developer test key `1` is sufficient. Each meal exposes up to 20 strIngredientN/strMeasureN
pairs and strInstructions — the verbatim steps we store.
"""

from __future__ import annotations

from typing import Any

import httpx

# Free developer test key per research.md §1; ingestion can override via the constructor.
_DEFAULT_KEY = "1"
_BASE_URL = "https://www.themealdb.com/api/json/v1"
# Listing by first letter a–z is the simplest way to enumerate the whole meal set.
_LETTERS = "abcdefghijklmnopqrstuvwxyz"


class TheMealDB:
    """Thin client over TheMealDB's JSON API returning raw meal dicts for ingestion to normalize."""

    def __init__(self, *, api_key: str = _DEFAULT_KEY, timeout: float = 30.0) -> None:
        """Store the API key and a reusable httpx client with a sane timeout."""
        self._base = f"{_BASE_URL}/{api_key}"
        self._client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        """Close the underlying HTTP client (call when ingestion is done with this adapter)."""
        self._client.close()

    def __enter__(self) -> TheMealDB:
        """Support `with TheMealDB() as db:` so the client is always closed."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close the client on context-manager exit regardless of success/failure."""
        self.close()

    def search_by_letter(self, letter: str) -> list[dict[str, Any]]:
        """Return all meals whose name starts with `letter` (the API's enumeration primitive).

        TheMealDB returns `{"meals": null}` for letters with no matches; we normalize that to [].
        """
        resp = self._client.get(f"{self._base}/search.php", params={"f": letter})
        resp.raise_for_status()
        meals = resp.json().get("meals")
        return meals or []

    def lookup(self, meal_id: str) -> dict[str, Any] | None:
        """Return the full meal record for an id, or None if the source has no such meal."""
        resp = self._client.get(f"{self._base}/lookup.php", params={"i": meal_id})
        resp.raise_for_status()
        meals = resp.json().get("meals")
        return meals[0] if meals else None

    def iter_all_meals(self) -> list[dict[str, Any]]:
        """Enumerate the whole meal catalog by walking a–z and deduplicating on idMeal.

        The per-letter search already returns full records, so no second lookup is needed. Dedup guards
        against a meal matching more than one enumeration path.
        """
        seen: dict[str, dict[str, Any]] = {}
        for letter in _LETTERS:
            for meal in self.search_by_letter(letter):
                meal_id = meal.get("idMeal")
                if meal_id and meal_id not in seen:
                    seen[meal_id] = meal
        return list(seen.values())
