import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import require_internal_token
from app.core.database import get_db
from app.schemas.search import SearchRequest
from app.services.embedding import Embedder, EmbeddingError, get_embedder
from app.services.search import search_chunks

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/v1/rag", tags=["internal"])


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
def semantic_search(
    body: SearchRequest,
    request: Request,
    response: Response,
    _: None = Depends(require_internal_token),
    db: Session = Depends(get_db),
    embedder: Embedder = Depends(embedder_dep),
):
    """Chỉ agent-service gọi. Embedding câu hỏi rồi tìm top_k chunk gần nhất."""
    request_id = request.headers.get("X-Request-ID") or f"req-{uuid.uuid4().hex[:8]}"
    response.headers["X-Request-ID"] = request_id
    try:
        chunks = search_chunks(db, embedder, body.query, body.top_k, body.doc_types)
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
    return {"data": {"chunks": chunks}, "error": None, "request_id": request_id}
