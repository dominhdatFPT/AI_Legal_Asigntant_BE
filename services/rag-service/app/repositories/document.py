"""Truy vấn database cho legal_documents và document_chunks."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, defer

from app.models import DocumentChunk, DocumentVersion, IngestLog, LegalDocument


def get_by_doc_number(db: Session, doc_number: str) -> LegalDocument | None:
    return db.scalar(select(LegalDocument).where(LegalDocument.doc_number == doc_number))


def get_document(db: Session, document_id: uuid.UUID) -> LegalDocument | None:
    return db.get(LegalDocument, document_id)


def list_documents(
    db: Session,
    page: int,
    page_size: int,
    status: str | None = None,
    doc_type: str | None = None,
) -> tuple[list[LegalDocument], int]:
    """Danh sách văn bản mới tạo trước, có phân trang. Trả về (các dòng của trang, tổng số dòng khớp bộ lọc)."""
    filters = []
    if status:
        filters.append(LegalDocument.status == status)
    if doc_type:
        filters.append(LegalDocument.doc_type == doc_type)
    total = db.scalar(select(func.count()).select_from(LegalDocument).where(*filters)) or 0
    stmt = (
        select(LegalDocument)
        .where(*filters)
        # id làm khóa phụ để các trang không bị lặp/sót dòng khi nhiều văn bản cùng created_at
        .order_by(LegalDocument.created_at.desc(), LegalDocument.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
        # Không kéo toàn văn luật về cho từng dòng danh sách (xem semantic_search)
        .options(defer(LegalDocument.content))
    )
    return list(db.scalars(stmt).all()), total


def get_document_with_length(db: Session, document_id: uuid.UUID) -> tuple[LegalDocument, int] | None:
    """Văn bản kèm số ký tự toàn văn (tính bằng SQL). Không kéo cột content về."""
    stmt = (
        select(LegalDocument, func.length(LegalDocument.content))
        .where(LegalDocument.id == document_id)
        .options(defer(LegalDocument.content))
    )
    row = db.execute(stmt).first()
    return (row[0], int(row[1] or 0)) if row else None


def get_brief_by_doc_number(db: Session, doc_number: str) -> tuple[uuid.UUID, str, str] | None:
    """(id, title, status) của văn bản có số hiệu này, không kéo cột content."""
    row = db.execute(
        select(LegalDocument.id, LegalDocument.title, LegalDocument.status).where(
            LegalDocument.doc_number == doc_number
        )
    ).first()
    return (row[0], row[1], row[2]) if row else None


def claim_for_processing(db: Session, document_id: uuid.UUID) -> bool:
    """'Chiếm chỗ' nguyên tử để duyệt: một câu UPDATE có điều kiện đặt ingest_status = processing, chỉ khi văn bản còn
    là draft và ingest_status còn pending/failed. Trả về False nếu không dòng nào đổi (người khác vừa chiếm).
    Không commit, người gọi commit."""
    result = db.execute(
        update(LegalDocument)
        .where(
            LegalDocument.id == document_id,
            LegalDocument.status == "draft",
            LegalDocument.ingest_status.in_(["pending", "failed"]),
        )
        .values(ingest_status="processing", ingest_error=None, updated_at=datetime.now(timezone.utc))
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def delete_draft(db: Session, document_id: uuid.UUID) -> bool:
    """Xóa văn bản chỉ khi vẫn là draft và không đang processing (một câu DELETE có điều kiện; chunk xóa theo
    ON DELETE CASCADE). Trả về False nếu không xóa được dòng nào. Không commit, người gọi commit."""
    result = db.execute(
        delete(LegalDocument)
        .where(
            LegalDocument.id == document_id,
            LegalDocument.status == "draft",
            LegalDocument.ingest_status != "processing",
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


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
        # Không kéo toàn văn luật và vector về theo từng dòng (search.py không dùng), trước đây làm db=15s
        .options(defer(LegalDocument.content), defer(DocumentChunk.embedding))
    )
    if doc_types:
        stmt = stmt.where(LegalDocument.doc_type.in_(doc_types))
    return [(chunk, doc, float(dist)) for chunk, doc, dist in db.execute(stmt).all()]


def list_ingest_logs(
    db: Session,
    page: int,
    page_size: int,
    document_id: uuid.UUID | None = None,
    action: str | None = None,
) -> tuple[list[IngestLog], int]:
    """Nhật ký nạp mới nhất trước, có phân trang. Trả về (các dòng của trang, tổng số dòng khớp bộ lọc)."""
    filters = []
    if document_id:
        filters.append(IngestLog.document_id == document_id)
    if action:
        filters.append(IngestLog.action == action)
    total = db.scalar(select(func.count()).select_from(IngestLog).where(*filters)) or 0
    stmt = (
        select(IngestLog)
        .where(*filters)
        # id làm khóa phụ để các trang không bị lặp/sót dòng khi nhiều dòng cùng created_at
        .order_by(IngestLog.created_at.desc(), IngestLog.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(db.scalars(stmt).all()), total


def list_chunk_summaries(db: Session, document_id: uuid.UUID) -> list[tuple[str, dict | None, str]]:
    """(content, section_info, embedding_model) của các chunk theo thứ tự trong văn bản. Không kéo cột embedding về
    (xem trước thay thế không cần vector)."""
    rows = db.execute(
        select(DocumentChunk.content, DocumentChunk.section_info, DocumentChunk.embedding_model)
        .where(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.chunk_index)
    ).all()
    return [(r[0], r[1], r[2]) for r in rows]


def list_chunks_with_vectors(
    db: Session, document_id: uuid.UUID
) -> list[tuple[str, dict | None, list[float], str]]:
    """(content, section_info, embedding, embedding_model) của các chunk theo thứ tự trong văn bản, dạng dữ liệu thuần
    (không phải đối tượng ORM) để dùng tiếp sau khi kết thúc giao dịch. Dùng để tái sử dụng vector khi thay nội dung."""
    rows = db.execute(
        select(DocumentChunk.content, DocumentChunk.section_info, DocumentChunk.embedding, DocumentChunk.embedding_model)
        .where(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.chunk_index)
    ).all()
    return [(r[0], r[1], r[2], r[3]) for r in rows]


def claim_for_replace(db: Session, document_id: uuid.UUID) -> bool:
    """'Chiếm chỗ' nguyên tử để thay nội dung: một câu UPDATE có điều kiện đặt ingest_status = processing, chỉ khi văn bản
    đang active và ingest_status là done hoặc failed (status KHÔNG đổi). Trả về False nếu không dòng nào đổi.
    Không commit, người gọi commit."""
    result = db.execute(
        update(LegalDocument)
        .where(
            LegalDocument.id == document_id,
            LegalDocument.status == "active",
            LegalDocument.ingest_status.in_(["done", "failed"]),
        )
        .values(ingest_status="processing", ingest_error=None, updated_at=datetime.now(timezone.utc))
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def add_document_version(
    db: Session,
    document_id: uuid.UUID,
    content: str,
    chunk_count: int | None,
    replaced_by_user_id: str | None,
    note: str | None = None,
) -> DocumentVersion:
    """Lưu toàn văn CŨ trước khi thay. Không commit, người gọi commit cùng giao dịch với việc thay chunk."""
    version = DocumentVersion(
        document_id=document_id,
        replaced_by_user_id=replaced_by_user_id[:64] if replaced_by_user_id else None,
        content=content,
        chunk_count=chunk_count,
        content_length=len(content),
        note=note,
    )
    db.add(version)
    db.flush()
    return version
