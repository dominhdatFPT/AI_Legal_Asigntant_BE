"""Tìm kiếm ngữ nghĩa: embedding câu hỏi → tìm chunk gần nhất trong pgvector."""

from sqlalchemy.orm import Session

from app.repositories import document as document_repo
from app.services.embedding import Embedder


def search_chunks(
    db: Session,
    embedder: Embedder,
    query: str,
    top_k: int = 5,
    doc_types: list[str] | None = None,
) -> list[dict]:
    """Trả về danh sách chunk đúng hợp đồng ChunkSearchResult trong openapi.yaml."""
    query_vec = embedder.embed_query(query)
    rows = document_repo.semantic_search(
        db, query_vec, top_k, embedding_model=embedder.model_name, doc_types=doc_types
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
