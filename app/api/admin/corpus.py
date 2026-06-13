"""GET /admin/corpus — paged, read-only browse of the ingested recipe corpus (operator-only).

Thin HTTP: validates the optional category query param, delegates to `services/admin/corpus.browse`, and
returns a `CorpusPage`. Guarded by `require_operator` (the shared Vault admin token) so the public widget
cannot reach it. Mirrors contracts/admin.openapi.yaml. The DB session comes from the same `get_db`
dependency the cook surface uses, keeping the repo the only layer that touches the database.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.admin_deps import OperatorAuth
from app.api.deps import get_db
from app.core.errors import AppError
from app.models.recipe import Category
from app.schemas.admin import CorpusPage
from app.services.admin import corpus as corpus_service

router = APIRouter(prefix="/admin", tags=["admin"])

DbSession = Annotated[Session, Depends(get_db)]


def _validate_category(raw: str | None) -> str | None:
    """Validate the optional category filter against the five fixed values, or raise a 400.

    A missing filter is fine (browse all); a present-but-unknown value is a client error answered as 400
    with a machine code (consistent with the cook recipes surface) rather than FastAPI's default 422.
    """
    if raw is None or not raw.strip():
        return None
    try:
        return Category(raw).value
    except ValueError:
        raise AppError(
            f"Unknown category '{raw}'.", status_code=400, code="invalid_category"
        ) from None


@router.get("/corpus", response_model=CorpusPage)
def browse_corpus(
    _: OperatorAuth,
    session: DbSession,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    category: Annotated[str | None, Query()] = None,
) -> CorpusPage:
    """Return one page of the corpus for operator inspection, behind the admin-token guard.

    The `_: OperatorAuth` parameter runs `require_operator` before the body executes (401/403 on a bad
    token). The category param is validated to one of the fixed values; paging bounds are enforced by both
    Query constraints here and the service clamp. Read-only — no recipe is created or modified.
    """
    cat = _validate_category(category)
    return corpus_service.browse(session, page=page, page_size=page_size, category=cat)
