"""Tìm kiếm ngữ nghĩa: embedding câu hỏi → tìm chunk gần nhất trong pgvector."""

import logging
import time

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.repositories import document as document_repo
from app.services.embedding import Embedder
from app.services.query_expansion import expand

logger = logging.getLogger(__name__)


def search_chunks(
    db: Session,
    embedder: Embedder,
    query: str,
    top_k: int = 5,
    doc_types: list[str] | None = None,
) -> list[dict]:
    """Trả về danh sách chunk đúng hợp đồng ChunkSearchResult trong openapi.yaml."""
    started = time.perf_counter()
    if get_settings().query_expansion_enabled:
        query = expand(query)
    query_vec = embedder.embed_query(query)
    embedded = time.perf_counter()
    rows = document_repo.semantic_search(
        db, query_vec, top_k, embedding_model=embedder.model_name, doc_types=doc_types
    )
    # Đo từng bước để biết chậm ở Gemini embedding hay ở Supabase
    logger.info(
        "rag timing embed=%.2fs db=%.2fs results=%d query=%r",
        embedded - started, time.perf_counter() - embedded, len(rows), query[:200],
    )
    results = []
    for chunk, doc, distance in rows:
        # khoảng cách cosine 0–2 → độ tương đồng 1–(-1); kẹp về 0–1 theo hợp đồng
        score = max(0.0, min(1.0, 1.0 - distance))
        results.append(
            {
                "id": str(chunk.id),
                "content": chunk.content,
                "score": round(score, 4),
                "section_info": chunk.section_info,
                "document": {
                    "id": str(doc.id),
                    "title": doc.title,
                    "doc_number": doc.doc_number,
                    "doc_type": doc.doc_type,
                },
            }
        )
    return results
