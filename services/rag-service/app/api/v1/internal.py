import logging
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import require_internal_token
from app.core.database import get_db
from app.schemas.search import BatchSearchRequest, FairnessKey, SearchRequest
from app.services.embedding import Embedder, EmbeddingError, get_embedder
from app.services.search import (
    BatchDBUnavailable, _batch_db_limiter, run_search_db, search_batch, search_chunks, search_single,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/v1/rag", tags=["internal"])
_SYNC_SEARCH = search_chunks
FairnessHeader = Annotated[FairnessKey | None, Header(alias="X-Fairness-Key")]


def _search_context(body, request, response, fairness_header):
    request_id = request.headers.get("X-Request-ID") or f"req-{uuid.uuid4().hex[:8]}"
    response.headers["X-Request-ID"] = request_id
    tenant_key = fairness_header or body.fairness_key or body.request_turn_id or request_id
    return request_id, tenant_key


def _override_sessions(request):
    override = request.app.dependency_overrides.get(get_db)
    if override is None:
        return None

    @contextmanager
    def session():
        value = override()
        if isinstance(value, Iterator):
            yield from value
        else:
            yield value

    return session


def _scheduler_error(exc):
    return HTTPException(
        status_code=503,
        detail={"code": exc.code, "message": "Kho văn bản đang quá tải, vui lòng thử lại sau."},
    )


def embedder_dep() -> Embedder:
    try:
        return get_embedder()
    except EmbeddingError as e:
        logger.error("Không tạo được embedder: %s", e)
        raise HTTPException(
            status_code=500,
            detail={"code": "INTERNAL_ERROR", "message": "Hệ thống đang gặp lỗi, vui lòng thử lại sau."},
        )


@router.post("/search")
async def semantic_search(
    body: SearchRequest,
    request: Request,
    response: Response,
    _: None = Depends(require_internal_token),
    embedder: Embedder = Depends(embedder_dep),
    fairness_header: FairnessHeader = None,
):
    """Chỉ agent-service gọi. Tra cứu cục bộ, lấy embedding và xếp hạng top_k chunk."""
    request_id, tenant_key = _search_context(body, request, response, fairness_header)
    session_factory = _override_sessions(request)
    try:
        if search_chunks is _SYNC_SEARCH:
            chunks = await search_single(
                body, embedder, tenant_key=tenant_key, priority=body.priority, session_factory=session_factory,
            )
        else:
            # Existing integrations can replace the legacy synchronous callable.
            options = body.model_dump(exclude_defaults=True, exclude={
                "query", "top_k", "doc_types", "fairness_key", "priority", "deadline_ms",
            })

            def legacy_search(db):
                return search_chunks(db, embedder, body.query, body.top_k, body.doc_types, **options)

            chunks = await run_search_db(
                _batch_db_limiter(), time.monotonic() + (body.deadline_ms or 90000) / 1000, tenant_key,
                body.priority, legacy_search, session_factory=session_factory,
            )
    except BatchDBUnavailable as exc:
        raise _scheduler_error(exc) from exc
    except EmbeddingError as e:
        logger.warning("Embedding câu hỏi thất bại: %s", e)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "EMBEDDING_SERVICE_UNAVAILABLE",
                "message": "Dịch vụ tạo embedding đang bận, vui lòng thử lại sau.",
            },
        )
    except SQLAlchemyError as e:
        logger.error("Lỗi database khi tìm kiếm: %s", e)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "RAG_DATABASE_UNAVAILABLE",
                "message": "Không truy cập được kho văn bản, vui lòng thử lại sau.",
            },
        )
    data = {"chunks": chunks}
    usage = getattr(chunks, "embedding_usage", None)
    if usage is not None:
        data["embedding_usage"] = usage.model_dump()
    return {"data": data, "error": None, "request_id": request_id}


@router.post("/search/batch")
async def semantic_search_batch(
    body: BatchSearchRequest,
    request: Request,
    response: Response,
    _: None = Depends(require_internal_token),
    embedder: Embedder = Depends(embedder_dep),
    fairness_header: FairnessHeader = None,
):
    request_id, tenant_key = _search_context(body, request, response, fairness_header)
    try:
        result = await search_batch(body, embedder, tenant_key=tenant_key)
    except BatchDBUnavailable as exc:
        raise _scheduler_error(exc) from exc
    return {"data": result.model_dump(), "error": None, "request_id": request_id}
