"""Open Food Facts adapter — ingredient → allergen tags + per-100g nutriments (offline, ingestion-only).

Used at ingestion time to derive allergens and nutrition; the request path NEVER calls OFF. Results are
cached on disk under `ingestion/cache/` keyed by normalized ingredient name, so re-running ingestion is
fast and idempotent and does not hammer the public API. A lookup that finds nothing is cached as a
miss (empty payload) so we don't retry it every run.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx

# Default on-disk cache location (gitignored); ingestion may override it.
_DEFAULT_CACHE_DIR = Path("ingestion/cache/openfoodfacts")
_SEARCH_URL = "https://world.openfoodfacts.org/cgi/search.pl"
# The per-100g nutriment fields we care about (energy in kcal + the three macros).
_NUTRIMENT_FIELDS = ("energy-kcal_100g", "proteins_100g", "carbohydrates_100g", "fat_100g")


def _slug(name: str) -> str:
    """Normalize an ingredient name into a safe cache-file slug (lowercase, alnum + underscores)."""
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_") or "_empty"


class OpenFoodFacts:
    """Cached client over Open Food Facts returning a normalized {allergen_tags, nutriments} payload."""

    def __init__(self, *, cache_dir: Path | str = _DEFAULT_CACHE_DIR, timeout: float = 30.0) -> None:
        """Create the cache directory if needed and a reusable httpx client."""
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def __enter__(self) -> OpenFoodFacts:
        """Support use as a context manager so the client is always closed."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close the client on context-manager exit."""
        self.close()

    def _cache_path(self, name: str) -> Path:
        """Return the JSON cache file path for a normalized ingredient name."""
        return self._cache_dir / f"{_slug(name)}.json"

    def lookup_ingredient(self, name: str) -> dict[str, Any]:
        """Return {allergen_tags: [...], nutriments: {...}} for an ingredient, using the disk cache.

        On a cache miss, queries OFF's search endpoint for the best-matching product, extracts its
        `allergens_tags` and per-100g nutriments, writes the result to disk, and returns it. Any HTTP
        error degrades gracefully to an empty payload (also cached) so one bad lookup never aborts the
        run — completeness is decided downstream by `is_complete`/`allergen_certain`.
        """
        cache_path = self._cache_path(name)
        if cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))

        payload = self._fetch(name)
        cache_path.write_text(json.dumps(payload), encoding="utf-8")
        return payload

    def _fetch(self, name: str) -> dict[str, Any]:
        """Query OFF for the best product match and normalize it; return an empty payload on any failure."""
        empty: dict[str, Any] = {"allergen_tags": [], "nutriments": {}}
        try:
            resp = self._client.get(
                _SEARCH_URL,
                params={
                    "search_terms": name,
                    "search_simple": 1,
                    "action": "process",
                    "json": 1,
                    "page_size": 1,
                    "fields": "allergens_tags,nutriments",
                },
            )
            resp.raise_for_status()
            products = resp.json().get("products") or []
        except (httpx.HTTPError, ValueError):
            return empty

        if not products:
            return empty

        product = products[0]
        # OFF allergen tags look like "en:milk"; strip the language prefix to bare allergen words.
        allergen_tags = [
            tag.split(":", 1)[-1] for tag in product.get("allergens_tags", []) if tag
        ]
        nutriments = {
            field: product["nutriments"][field]
            for field in _NUTRIMENT_FIELDS
            if isinstance(product.get("nutriments"), dict) and field in product["nutriments"]
        }
        return {"allergen_tags": allergen_tags, "nutriments": nutriments}
