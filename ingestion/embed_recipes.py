"""Offline embed stage — give every complete recipe a semantic vector for retrieval.

Runs inside `make ingest` (after load, before the coverage report). For each complete recipe lacking an
embedding it builds a compact, deterministic embed text — `"{title}. {cuisine}. {category}. {key
ingredients}"` — embeds the batch via `infra.embeddings.embed_texts`, and writes the vectors back through
`app.repo.recipes`. Idempotent: only null-embedding rows are processed, so a rebuild converges without
re-embedding the whole corpus. Requires `EMBEDDINGS_API_KEY` in Vault (seeded by `make seed`). All DB
access stays in the repo layer — this module only orchestrates.
"""

from __future__ import annotations

import structlog
from app.infra import embeddings as embeddings_infra
from app.models.recipe import Recipe
from app.repo import recipes as recipes_repo
from sqlalchemy.orm import Session

log = structlog.get_logger()

# How many ingredient names to fold into the embed text — enough to signal the dish, not the full list.
_KEY_INGREDIENTS = 5


def build_embed_text(recipe: Recipe) -> str:
    """Compose the deterministic text embedded for a recipe (title-weighted, grounded in stored fields).

    Title comes first (the strongest dish signal), then cuisine + category so queries like "something
    Thai for dinner" land, then the first few ingredient names. `cuisine` may be null → rendered as
    "unknown" so the text is stable. Built only from stored row fields (no invention).
    """
    cuisine = recipe.cuisine or "unknown"
    key_ingredients = ", ".join(ing.name for ing in recipe.ingredients[:_KEY_INGREDIENTS])
    return f"{recipe.title}. {cuisine}. {recipe.category}. {key_ingredients}"


def embed_pending(session: Session) -> int:
    """Embed every complete recipe that still lacks a vector; return how many were embedded.

    Selects the embeddable rows, builds their texts, embeds them in one batched call, and writes each
    vector back via the repo. Returns 0 (without calling the provider) when nothing needs embedding, so
    a fully-embedded corpus re-runs as a cheap no-op. Flushes through the repo; the caller commits.
    """
    pending = recipes_repo.iter_embeddable(session)
    if not pending:
        log.info("embed.nothing_pending")
        return 0

    texts = [build_embed_text(recipe) for recipe in pending]
    vectors = embeddings_infra.embed_texts(texts)
    for recipe, vector in zip(pending, vectors, strict=True):
        recipes_repo.set_embedding(session, recipe.id, vector)

    log.info("embed.done", embedded=len(pending))
    return len(pending)
