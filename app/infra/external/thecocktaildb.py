"""TheCocktailDB adapter — fetch NON-ALCOHOLIC drinks (offline, ingestion-only).

Same shape as the TheMealDB adapter (an `infra/external` HTTP client returning raw dicts, used only by
ingestion). We keep only drinks whose `strAlcoholic == "Non alcoholic"` — alcohol is out of scope for
the cook-facing corpus. Each drink exposes strIngredientN/strMeasureN pairs and strInstructions.
"""

from __future__ import annotations

from typing import Any

import httpx

_DEFAULT_KEY = "1"
_BASE_URL = "https://www.thecocktaildb.com/api/json/v1"
_NON_ALCOHOLIC = "Non alcoholic"


class TheCocktailDB:
    """Thin client over TheCocktailDB returning raw non-alcoholic drink dicts for ingestion."""

    def __init__(self, *, api_key: str = _DEFAULT_KEY, timeout: float = 30.0) -> None:
        """Store the API key and a reusable httpx client with a sane timeout."""
        self._base = f"{_BASE_URL}/{api_key}"
        self._client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def __enter__(self) -> TheCocktailDB:
        """Support use as a context manager so the client is always closed."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close the client on context-manager exit."""
        self.close()

    def _list_non_alcoholic_ids(self) -> list[str]:
        """Return ids of all drinks the API filters as Non alcoholic (the filter endpoint is summary-only)."""
        resp = self._client.get(
            f"{self._base}/filter.php", params={"a": _NON_ALCOHOLIC}
        )
        resp.raise_for_status()
        drinks = resp.json().get("drinks") or []
        return [d["idDrink"] for d in drinks if d.get("idDrink")]

    def lookup(self, drink_id: str) -> dict[str, Any] | None:
        """Return the full drink record for an id, or None when absent."""
        resp = self._client.get(f"{self._base}/lookup.php", params={"i": drink_id})
        resp.raise_for_status()
        drinks = resp.json().get("drinks")
        return drinks[0] if drinks else None

    def iter_non_alcoholic_drinks(self) -> list[dict[str, Any]]:
        """Fetch the full record for every non-alcoholic drink.

        The filter endpoint returns only id+name+thumb, so we look up each id to get ingredients and
        instructions. We defensively re-check `strAlcoholic` on the full record before yielding it.
        """
        records: list[dict[str, Any]] = []
        for drink_id in self._list_non_alcoholic_ids():
            drink = self.lookup(drink_id)
            if drink and drink.get("strAlcoholic") == _NON_ALCOHOLIC:
                records.append(drink)
        return records
