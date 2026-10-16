"""Truy vấn database cho legal_documents và document_chunks."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import DocumentChunk, LegalDocument


def get_by_doc_number(db: Session, doc_number: str) -> LegalDocument | None:
    return db.scalar(select(LegalDocument).where(LegalDocument.doc_number == doc_number))


def get_document(db: Session, document_id: uuid.UUID) -> LegalDocument | None:
    return db.get(LegalDocument, document_id)


def create_document(db: Session, **fields) -> LegalDocument:
    doc = LegalDocument(**fields)
    db.add(doc)
    db.flush()  # để có doc.id
    return doc


def replace_chunks(
    db: Session,
    doc: LegalDocument,
    chunks: list[dict],
    embedding_model: str,
) -> None:
    """Xóa chunk cũ của văn bản rồi lưu chunk mới.
    chunks: [{"content", "section_info", "embedding"}], theo thứ tự trong văn bản."""
    db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc.id))
    db.add_all(
        DocumentChunk(
            document_id=doc.id,
            chunk_index=i,
            content=c["content"],
            section_info=c.get("section_info"),
            embedding=c["embedding"],
            embedding_model=embedding_model,
        )
        for i, c in enumerate(chunks)
    )
    doc.chunk_count = len(chunks)
    doc.is_processed = True
    doc.updated_at = datetime.now(timezone.utc)
    db.flush()


def semantic_search(
    db: Session,
    query_embedding: list[float],
    top_k: int,
    embedding_model: str,
    doc_types: list[str] | None = None,
) -> list[tuple[DocumentChunk, LegalDocument, float]]:
    """Tìm top_k chunk gần câu hỏi nhất theo khoảng cách cosine (toán tử <=> của pgvector).
    Chỉ PostgreSQL + pgvector chạy được (SQLite không có).
    Trả về [(chunk, văn bản, khoảng cách)], khoảng cách càng nhỏ càng giống."""
    distance = DocumentChunk.embedding.cosine_distance(query_embedding).label("distance")
    stmt = (
        select(DocumentChunk, LegalDocument, distance)
        .join(LegalDocument, DocumentChunk.document_id == LegalDocument.id)
        .where(
            LegalDocument.status == "active",
            LegalDocument.is_processed.is_(True),
            DocumentChunk.embedding_model == embedding_model,
        )
        .order_by(distance)
        .limit(top_k)
    )
    if doc_types:
        stmt = stmt.where(LegalDocument.doc_type.in_(doc_types))
    return [(chunk, doc, float(dist)) for chunk, doc, dist in db.execute(stmt).all()]
