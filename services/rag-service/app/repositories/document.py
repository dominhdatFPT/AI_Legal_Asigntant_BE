"""Truy vấn database cho legal_documents và document_chunks."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, func, or_, select, text, update
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
    # top_k ở đây đã bao gồm overfetch từ search.py.
    # SET LOCAL chỉ có hiệu lực trong transaction hiện tại.
    if top_k > 20 and db.get_bind().dialect.name == "postgresql":
        ef_search = max(64, top_k * 2)
        db.execute(text(f"SET LOCAL hnsw.ef_search = {ef_search}"))
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


def _active_chunks_stmt(*columns):
    """SELECT các cột của document_chunks thuộc văn bản đang tìm được (cùng điều kiện status/is_processed như semantic_search).
    Chỉ chọn cột truyền vào: không kéo embedding, không kéo legal_documents.content."""
    return (
        select(*columns)
        .select_from(DocumentChunk)
        .join(LegalDocument, DocumentChunk.document_id == LegalDocument.id)
        .where(LegalDocument.status == "active", LegalDocument.is_processed.is_(True))
    )


def active_chunks_fingerprint(db: Session) -> tuple[int, datetime | None]:
    """(số đoạn, thời điểm tạo mới nhất) của các đoạn đang active. Đổi khi nạp, duyệt, thay nội dung (tạo lại mọi đoạn)
    hoặc đổi trạng thái văn bản. Chỉ đọc; dùng để biết chỉ mục BM25 trong bộ nhớ (services/hybrid.py) còn đúng không."""
    count, newest = db.execute(
        _active_chunks_stmt(func.count(DocumentChunk.id), func.max(DocumentChunk.created_at))
    ).one()
    return int(count or 0), newest


def active_chunk_texts(db: Session) -> list[tuple[uuid.UUID, str]]:
    """[(id, content)] của mọi đoạn đang active, theo thứ tự văn bản rồi chunk_index (ổn định giữa các lần chạy).
    Chỉ đọc; dựng chỉ mục BM25."""
    stmt = _active_chunks_stmt(DocumentChunk.id, DocumentChunk.content).order_by(
        DocumentChunk.document_id, DocumentChunk.chunk_index
    )
    return [(row[0], row[1]) for row in db.execute(stmt).all()]


def retrieval_rows(db: Session, embedding_model: str, doc_types: list[str] | None = None,
                   chunk_ids: list[str] | None = None, doc_numbers: list[str] | None = None,
                   articles: list[str] | None = None,
                   limit: int | None = None,
                   doc_ids: list[uuid.UUID] | None = None,
                   title_patterns: list[str] | None = None) -> list[tuple[DocumentChunk, LegalDocument, float]]:
    """Independent lexical/exact pool with the same visibility filters as vector.

    No vector or document full text is materialized. Callers own their session.
    doc_ids / title_patterns (LIKE, mọi giá trị đều là bind param) phục vụ tra cứu Điều/Khoản liền kề và theo tên Điều.
    """
    stmt = _active_chunks_stmt(DocumentChunk, LegalDocument).where(DocumentChunk.embedding_model == embedding_model)
    if doc_types:
        stmt = stmt.where(LegalDocument.doc_type.in_(doc_types))
    if chunk_ids is not None:
        stmt = stmt.where(DocumentChunk.id.in_([uuid.UUID(cid) for cid in chunk_ids]))
    if doc_numbers:
        # lower() của SQLite chỉ hạ chữ ASCII ("NQ-HĐTP"), nên so thêm đúng dạng gốc
        stmt = stmt.where(or_(
            LegalDocument.doc_number.in_(doc_numbers),
            func.lower(LegalDocument.doc_number).in_([n.lower() for n in doc_numbers]),
        ))
    if doc_ids:
        stmt = stmt.where(DocumentChunk.document_id.in_(doc_ids))
    if title_patterns:
        stmt = stmt.where(or_(*[DocumentChunk.content.like(p, escape="\\") for p in title_patterns]))
    if articles:
        stmt = stmt.where(DocumentChunk.section_info["dieu"].as_string().in_(articles))
    stmt = stmt.order_by(DocumentChunk.document_id, DocumentChunk.chunk_index).options(
        defer(LegalDocument.content), defer(DocumentChunk.embedding),
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return [(chunk, doc, 1.0) for chunk, doc in db.execute(stmt).all()]


def lexical_corpus_fingerprint(
    db: Session,
    embedding_model: str,
    doc_types: list[str] | None = None,
) -> tuple[int, datetime | None]:
    """Dấu vân tay (số đoạn, thời điểm tạo mới nhất) theo embedding_model và doc_types để cache BM25."""
    stmt = _active_chunks_stmt(
        func.count(DocumentChunk.id), func.max(DocumentChunk.created_at)
    ).where(DocumentChunk.embedding_model == embedding_model)
    if doc_types:
        stmt = stmt.where(LegalDocument.doc_type.in_(doc_types))
    count, newest = db.execute(stmt).one()
    return int(count or 0), newest


def lexical_corpus(db: Session, embedding_model: str,
                   doc_types: list[str] | None = None) -> list[tuple[str, str]]:
    stmt = _active_chunks_stmt(
        DocumentChunk.id, DocumentChunk.content, LegalDocument.title, LegalDocument.doc_number,
    ).where(DocumentChunk.embedding_model == embedding_model)
    if doc_types:
        stmt = stmt.where(LegalDocument.doc_type.in_(doc_types))
    stmt = stmt.order_by(DocumentChunk.document_id, DocumentChunk.chunk_index)
    return [(str(cid), f"{title} {number or ''}\n{content}") for cid, content, title, number in db.execute(stmt).all()]


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
