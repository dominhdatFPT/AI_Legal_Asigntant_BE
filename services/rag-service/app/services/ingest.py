"""Nạp văn bản pháp luật: cắt đoạn → tạo embedding → lưu database."""

import logging
from datetime import date

from sqlalchemy.orm import Session

from app.repositories import document as document_repo
from app.services.chunking import DEFAULT_MAX_CHARS, chunk_legal_text, embedding_text
from app.services.embedding import Embedder

logger = logging.getLogger(__name__)


class DocumentExistsError(Exception):
    pass


class EmptyDocumentError(Exception):
    pass


def ingest_document(
    db: Session,
    embedder: Embedder,
    *,
    title: str,
    doc_type: str,
    content: str,
    doc_number: str | None = None,
    issuing_body: str | None = None,
    issued_date: date | None = None,
    effective_date: date | None = None,
    status: str = "active",
    source_url: str | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
):
    """Tạo văn bản mới kèm toàn bộ chunk + embedding. Tự commit.
    Embedding được tạo xong hết rồi mới ghi database, nên lỗi giữa chừng không để lại dữ liệu dở."""
    if doc_number and document_repo.get_by_doc_number(db, doc_number):
        raise DocumentExistsError(f"Văn bản số {doc_number} đã có trong database")

    chunks = chunk_legal_text(content, max_chars=max_chars)
    if not chunks:
        raise EmptyDocumentError("Văn bản rỗng, không cắt được đoạn nào")
    logger.info("Cắt được %d đoạn, bắt đầu tạo embedding (%s)", len(chunks), embedder.model_name)

    vectors = embedder.embed_documents([embedding_text(title, c) for c in chunks])

    try:
        doc = document_repo.create_document(
            db,
            title=title,
            doc_type=doc_type,
            doc_number=doc_number,
            issuing_body=issuing_body,
            issued_date=issued_date,
            effective_date=effective_date,
            status=status,
            source_url=source_url,
            content=content,
        )
        document_repo.replace_chunks(
            db,
            doc,
            [
                {"content": c.content, "section_info": c.section_info, "embedding": v}
                for c, v in zip(chunks, vectors)
            ],
            embedding_model=embedder.model_name,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return doc


def reembed_document(db: Session, embedder: Embedder, doc, max_chars: int = DEFAULT_MAX_CHARS):
    """Cắt + embedding lại một văn bản đã có (VD sau khi đổi model embedding). Tự commit."""
    chunks = chunk_legal_text(doc.content, max_chars=max_chars)
    if not chunks:
        raise EmptyDocumentError("Văn bản rỗng, không cắt được đoạn nào")
    vectors = embedder.embed_documents([embedding_text(doc.title, c) for c in chunks])
    try:
        document_repo.replace_chunks(
            db,
            doc,
            [
                {"content": c.content, "section_info": c.section_info, "embedding": v}
                for c, v in zip(chunks, vectors)
            ],
            embedding_model=embedder.model_name,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return doc
