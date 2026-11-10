"""Duyệt bản nháp: cắt chunk + tạo embedding ở nền rồi chuyển văn bản sang active.

Hàm đồng bộ (không async) để Starlette BackgroundTasks tự chạy trong threadpool, không chặn event loop.
Giả định rag-service chạy MỘT tiến trình: trạng thái processing chỉ nằm trong DB, không có khóa giữa các tiến trình.
"""

import logging
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import text as sql_text
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.models import LegalDocument
from app.repositories import document as document_repo
from app.services import ingest_log
from app.services.chunking import chunk_legal_text, embedding_text
from app.services.embedding import EmbeddingError, get_embedder
from app.services.ingest import EmptyDocumentError

logger = logging.getLogger(__name__)

MAX_ERROR_CHARS = 500
INTERRUPTED_MESSAGE = "Bị ngắt do service khởi động lại, hãy bấm duyệt lại"


def _open_session() -> Session:
    """Chỗ để test thay bằng session trên DB tạm."""
    return get_session()


class DocumentChangedError(Exception):
    """Văn bản bị đổi trong lúc việc nền đang chạy (dùng bởi document_replace.py); thông báo hiện cho admin như EmbeddingError."""


def _failure_message(exc: Exception) -> str:
    if isinstance(exc, (EmbeddingError, EmptyDocumentError, DocumentChangedError)):
        text = f"{type(exc).__name__}: {exc}"
    else:
        text = f"{type(exc).__name__}: lỗi không lường trước, xem log của rag-service."
    return ingest_log.mask_secrets(text)[:MAX_ERROR_CHARS]


def _mark_failed(db: Session | None, document_id: uuid.UUID, exc: Exception) -> None:
    """Đặt ingest_status = failed, status giữ draft. Không để ngoại lệ thoát ra."""
    own: Session | None = None
    try:
        if db is None:
            own = db = _open_session()
        else:
            db.rollback()
        db.execute(
            update(LegalDocument)
            .where(LegalDocument.id == document_id, LegalDocument.ingest_status == "processing")
            .values(
                ingest_status="failed",
                ingest_error=_failure_message(exc),
                updated_at=datetime.now(timezone.utc),
            )
            .execution_options(synchronize_session=False)
        )
        db.commit()
    except Exception:  # noqa: BLE001 - hàm nền không được ném lỗi
        logger.exception("Không ghi được ingest_status=failed cho văn bản %s", document_id)
    finally:
        if own is not None:
            own.close()


def approve_document_background(
    document_id: uuid.UUID,
    actor_user_id: str | None = None,
    actor_role: str | None = None,
) -> None:
    """Việc nền của POST /{id}/approve (văn bản đã được 'chiếm chỗ' = processing). Ba giai đoạn:
    1. Đọc: lấy id, title, content rồi kết thúc giao dịch ngay.
    2. Embed (có thể vài phút với bộ luật dài): KHÔNG giữ giao dịch DB nào, kết nối trả về pool.
    3. Ghi: một giao dịch mới ngắn, trên PostgreSQL đặt `SET LOCAL statement_timeout = 0` (ghi hàng trăm vector có thể
       vượt trần statement_timeout mặc định của database.py), thay chunk rồi chuyển active, commit một lần.
    Lỗi ở giai đoạn nào cũng thành failed (văn bản giữ draft). Kết quả được ghi vào nhật ký nạp, người gọi là actor_*."""
    started = time.perf_counter()
    db: Session | None = None
    doc_number = title = None
    try:
        db = _open_session()
        doc = db.get(LegalDocument, document_id)
        if doc is None:
            raise LookupError(f"Không tìm thấy văn bản {document_id}")
        doc_number, title, content = doc.doc_number, doc.title, doc.content
        db.rollback()  # đóng giao dịch của lần đọc, không giữ qua lúc gọi Gemini

        chunks = chunk_legal_text(content)
        if not chunks:
            raise EmptyDocumentError("Văn bản rỗng, không cắt được đoạn nào")
        embedder = get_embedder()
        vectors = embedder.embed_documents([embedding_text(title, c) for c in chunks])

        if db.get_bind().dialect.name == "postgresql":
            db.execute(sql_text("SET LOCAL statement_timeout = 0"))  # phải là câu đầu của giao dịch ghi; chỉ trong giao dịch này
        doc = db.get(LegalDocument, document_id)
        if doc is None:
            raise LookupError(f"Không tìm thấy văn bản {document_id}")
        document_repo.replace_chunks(
            db,
            doc,
            [{"content": c.content, "section_info": c.section_info, "embedding": v} for c, v in zip(chunks, vectors)],
            embedding_model=embedder.model_name,
        )
        doc.status = "active"
        doc.ingest_status = "done"
        doc.ingest_error = None
        doc.updated_at = datetime.now(timezone.utc)
        chunk_count = doc.chunk_count
        db.commit()
    except Exception as exc:  # noqa: BLE001 - mọi lỗi đều đổi thành failed, không để thoát ra khỏi hàm nền
        logger.exception("Duyệt văn bản %s thất bại sau %.2fs", document_id, time.perf_counter() - started)
        _mark_failed(db, document_id, exc)
        ingest_log.record(
            "approve_failed", actor_user_id=actor_user_id, actor_role=actor_role, document_id=document_id,
            doc_number=doc_number, title=title, detail=_failure_message(exc),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    else:
        logger.info("Duyệt xong văn bản %s: %d chunk, %.2fs", document_id, chunk_count, time.perf_counter() - started)
        ingest_log.record(
            "approve_done", actor_user_id=actor_user_id, actor_role=actor_role, document_id=document_id,
            doc_number=doc_number, title=title, chunk_count=chunk_count,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                logger.exception("Không đóng được session của việc nền duyệt văn bản %s", document_id)


def recover_interrupted_ingests(db: Session) -> int:
    """Văn bản đang processing mà không còn tiến trình nào chạy nó (service vừa khởi động lại) -> failed.
    Trả về số văn bản được phục hồi. Chỉ đụng ingest_status = processing."""
    result = db.execute(
        update(LegalDocument)
        .where(LegalDocument.ingest_status == "processing")
        .values(ingest_status="failed", ingest_error=INTERRUPTED_MESSAGE, updated_at=datetime.now(timezone.utc))
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return result.rowcount


def recover_on_startup() -> None:
    """Gọi từ lifespan. Lỗi chỉ ghi log, không được làm service chết khi khởi động."""
    db: Session | None = None
    try:
        db = _open_session()
        count = recover_interrupted_ingests(db)
        if count:
            logger.warning("Đặt %d văn bản đang processing thành failed (service khởi động lại)", count)
    except Exception as e:  # noqa: BLE001
        logger.warning("Không phục hồi được văn bản processing khi khởi động (bỏ qua): %s", e)
    finally:
        if db is not None:
            db.close()
