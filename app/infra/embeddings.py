"""Embeddings adapter — turn text into vectors via a hosted OpenAI-compatible provider.

Groq is chat-only, so embeddings come from a SEPARATE hosted provider (default OpenAI
`text-embedding-3-small`, 1536-d). Base URL + model are config-driven (`app/config.py`) so the provider
swaps without code changes; the API key comes from Vault (`EMBEDDINGS_API_KEY`), never config or code
(golden rule #4). Two module-level functions are the whole surface — `embed_query` for a single search
string and `embed_texts` for batched corpus embedding — so callers (rag service, ingestion) just import
them and tests monkeypatch them. The client is built lazily and cached so the Vault read + HTTP client
construction happen once per process.
"""

from __future__ import annotations

from functools import lru_cache

from openai import OpenAI

from app.config import get_settings
from app.infra.vault import VaultAdapter

# Batch size for embed_texts: large enough to be efficient, small enough to stay under provider
# request limits on a ≤2,000-recipe corpus.
_BATCH_SIZE = 128
# Simple retry budget for transient provider/network errors (no backoff library — keep it lean).
_MAX_RETRIES = 3


@lru_cache
def _client() -> OpenAI:
    """Build (once) the OpenAI-compatible client, reading the API key from Vault.

    lru_cache makes the Vault read and client construction happen a single time per process. Vault is
    reachable in every context this runs in (the app and the offline ingestion job), so constructing a
    fresh VaultAdapter here keeps the function self-contained without threading app state through.
    """
    settings = get_settings()
    vault = VaultAdapter(settings)
    vault.load_secrets()
    return OpenAI(base_url=settings.embeddings_base_url, api_key=vault.get("EMBEDDINGS_API_KEY"))


def _embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed one batch, retrying a few times on transient errors before giving up.

    Returns vectors in the same order as the input. A persistent failure raises after the retry budget
    so the caller (ingestion) can log+skip rather than silently storing nothing.
    """
    settings = get_settings()
    last_exc: Exception | None = None
    for _ in range(_MAX_RETRIES):
        try:
            resp = _client().embeddings.create(model=settings.embeddings_model, input=texts)
            # The provider returns items in request order, but sort by index to be safe.
            return [item.embedding for item in sorted(resp.data, key=lambda d: d.index)]
        except Exception as exc:  # provider/network errors — retry a bounded number of times
            last_exc = exc
    raise RuntimeError(f"embeddings provider failed after {_MAX_RETRIES} retries: {last_exc}")


def embed_query(text: str) -> list[float]:
    """Embed a single query string into one vector (the search path).

    Thin wrapper over a one-item batch so query and corpus embedding share the exact same model + client
    and can never drift apart.
    """
    return _embed_batch([text])[0]


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed many texts into vectors, batched, preserving input order (the ingestion path).

    Splits the input into fixed-size batches to respect provider request limits, then concatenates the
    per-batch results. An empty input returns an empty list without calling the provider.
    """
    vectors: list[list[float]] = []
    for start in range(0, len(texts), _BATCH_SIZE):
        vectors.extend(_embed_batch(texts[start : start + _BATCH_SIZE]))
    return vectors
