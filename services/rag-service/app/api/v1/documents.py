import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import require_admin, get_current_user
from app.core.auth import TokenPayload
from app.core.config import get_settings
from app.core.database import get_db
from app.models import LegalDocument
from app.repositories import document as document_repo
from app.schemas.document import (
    DocStatus,
    DocumentCreateRequest,
    DocumentDetail,
    DocumentList,
    DocumentListItem,
    DocumentPreviewRequest,
    DocumentReplaceRequest,
    IngestAction,
    IngestLogList,
)
from app.schemas.search import DocType
from app.services import document_replace, ingest_log
from app.services.chunking import chunk_legal_text
from app.services.document_approval import approve_document_background
from app.services.document_preview import EmptyContentError, analyze_document
from app.services.document_replace import replace_document_background

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/rag/documents", tags=["documents"])


def _request_id(request: Request, response: Response) -> str:
    request_id = request.headers.get("X-Request-ID") or f"req-{uuid.uuid4().hex[:8]}"
    response.headers["X-Request-ID"] = request_id
    return request_id


def _empty_content_error() -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={
            "code": "VALIDATION_ERROR",
            "message": "Nội dung văn bản rỗng, không có gì để cắt.",
            "details": [{"field": "content", "message": "Nội dung văn bản không được rỗng."}],
        },
    )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "NOT_FOUND", "message": "Không tìm thấy văn bản pháp luật."},
    )


def _invalid_state(message: str) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": "INVALID_STATE", "message": message})


def _document_exists(doc_number: str, title: str) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "DOCUMENT_EXISTS",
            "message": f"Số hiệu {doc_number} đã có trong hệ thống (văn bản: {title}).",
        },
    )


def _detail(doc: LegalDocument, content_length: int) -> DocumentDetail:
    fields = {name: getattr(doc, name) for name in DocumentDetail.model_fields if name != "content_length"}
    return DocumentDetail(**fields, content_length=content_length)


@router.get("")
def list_documents(
    request: Request,
    response: Response,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: DocStatus | None = None,
    doc_type: DocType | None = None,
    _: TokenPayload = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Danh sách văn bản (admin). Không trả `content`."""
    request_id = _request_id(request, response)
    rows, total = document_repo.list_documents(db, page, page_size, status, doc_type)
    data = DocumentList(
        items=[DocumentListItem.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )
    return {"data": data, "error": None, "request_id": request_id}


@router.get("/logs")
def list_ingest_logs(
    request: Request,
    response: Response,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    document_id: uuid.UUID | None = None,
    action: IngestAction | None = None,
    _: TokenPayload = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Nhật ký nạp (admin): ai tạo nháp, xóa nháp, duyệt, kết quả duyệt, mới nhất trước. Khai báo TRƯỚC `/{document_id}`
    để "logs" không bị hiểu là document_id."""
    request_id = _request_id(request, response)
    rows, total = document_repo.list_ingest_logs(db, page, page_size, document_id, action)
    data = IngestLogList(items=rows, total=total, page=page, page_size=page_size)
    return {"data": data, "error": None, "request_id": request_id}


@router.post("/preview")
async def preview_document(
    body: DocumentPreviewRequest,
    request: Request,
    response: Response,
    _: TokenPayload = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Xem trước cách hệ thống cắt văn bản (admin). Chỉ đọc DB, không ghi, không gọi embedding."""
    request_id = _request_id(request, response)
    try:
        result = await run_in_threadpool(analyze_document, body.content, body.title)
    except EmptyContentError:
        raise _empty_content_error()

    existing = None
    if body.doc_number:
        found = await run_in_threadpool(document_repo.get_by_doc_number, db, body.doc_number)
        if found:
            existing = {"id": str(found.id), "title": found.title, "status": found.status}
    result["existing_document"] = existing
    return {"data": result, "error": None, "request_id": request_id}


def _save_draft(db: Session, fields: dict) -> uuid.UUID:
    """Ghi bản nháp: chưa có chunk, chưa embedding. Lỗi thì rollback sạch rồi ném lại."""
    try:
        doc = document_repo.create_document(
            db, **fields, status="draft", ingest_status="pending", is_processed=False, chunk_count=0
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return doc.id


@router.post("", status_code=201)
async def create_document(
    body: DocumentCreateRequest,
    request: Request,
    response: Response,
    user: TokenPayload = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Lưu văn bản mới ở trạng thái nháp (admin). Chưa cắt chunk, chưa embedding; duyệt bằng POST /{id}/approve."""
    request_id = _request_id(request, response)
    if not await run_in_threadpool(chunk_legal_text, body.content):
        raise _empty_content_error()

    if body.doc_number:
        existing = await run_in_threadpool(document_repo.get_brief_by_doc_number, db, body.doc_number)
        if existing:
            raise _document_exists(body.doc_number, existing[1])

    try:
        document_id = await run_in_threadpool(_save_draft, db, body.model_dump())
    except IntegrityError:
        # Hai người nạp cùng số hiệu cùng lúc: người sau thua ở khóa duy nhất
        existing = body.doc_number and await run_in_threadpool(
            document_repo.get_brief_by_doc_number, db, body.doc_number
        )
        if not existing:
            raise
        raise _document_exists(body.doc_number, existing[1])

    found = await run_in_threadpool(document_repo.get_document_with_length, db, document_id)
    logger.info("Tạo bản nháp văn bản %s bởi %s", document_id, user.sub)
    await run_in_threadpool(
        ingest_log.record, "create_draft", actor_user_id=user.sub, actor_role=user.role,
        document_id=document_id, doc_number=body.doc_number, title=body.title,
    )
    return {"data": _detail(*found), "error": None, "request_id": request_id}


@router.get("/{document_id}")
def get_document(
    document_id: uuid.UUID,
    request: Request,
    response: Response,
    _: TokenPayload = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Chi tiết một văn bản (admin). Không trả `content`, chỉ có `content_length`."""
    request_id = _request_id(request, response)
    found = document_repo.get_document_with_length(db, document_id)
    if not found:
        raise _not_found()
    return {"data": _detail(*found), "error": None, "request_id": request_id}


@router.delete("/{document_id}")
def delete_document(
    document_id: uuid.UUID,
    request: Request,
    response: Response,
    user: TokenPayload = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Xóa bản nháp (admin). Chỉ xóa được văn bản draft và không đang processing."""
    request_id = _request_id(request, response)
    found = document_repo.get_document_with_length(db, document_id)
    if not found:
        raise _not_found()
    doc = found[0]
    doc_number, title = doc.doc_number, doc.title
    if doc.status != "draft":
        raise _invalid_state("Chỉ xóa được văn bản nháp; văn bản đang có hiệu lực hoặc đã bị thay thế thì không xóa ở đây.")
    if doc.ingest_status == "processing":
        raise _invalid_state("Văn bản đang được xử lý, chưa xóa được.")
    try:
        deleted = document_repo.delete_draft(db, document_id)
        db.commit()
    except Exception:
        db.rollback()
        raise
    if not deleted:
        raise _invalid_state("Trạng thái văn bản vừa thay đổi, vui lòng tải lại rồi thử lại.")
    logger.info("Xóa bản nháp văn bản %s bởi %s", document_id, user.sub)
    ingest_log.record(
        "delete_draft", actor_user_id=user.sub, actor_role=user.role,
        document_id=document_id, doc_number=doc_number, title=title,
    )
    return {"data": {"deleted": True}, "error": None, "request_id": request_id}


@router.post("/{document_id}/approve", status_code=202)
def approve_document(
    document_id: uuid.UUID,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    user: TokenPayload = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Duyệt bản nháp (admin): trả 202 ngay, cắt chunk + embedding chạy ở nền, xong thì văn bản thành active.
    Bấm lại được khi lần trước failed."""
    request_id = _request_id(request, response)
    found = document_repo.get_document_with_length(db, document_id)
    if not found:
        raise _not_found()
    doc = found[0]
    if doc.status != "draft":
        raise _invalid_state("Chỉ duyệt được văn bản nháp.")
    if doc.ingest_status == "processing":
        raise _invalid_state("Văn bản đang được xử lý.")
    if doc.ingest_status not in ("pending", "failed"):
        raise _invalid_state("Văn bản này không ở trạng thái chờ duyệt.")

    try:
        claimed = document_repo.claim_for_processing(db, document_id)
        db.commit()
    except Exception:
        db.rollback()
        raise
    if not claimed:
        raise _invalid_state("Văn bản vừa được người khác duyệt hoặc thay đổi trạng thái.")

    ingest_log.record(
        "approve_requested", actor_user_id=user.sub, actor_role=user.role,
        document_id=document_id, doc_number=doc.doc_number, title=doc.title,
    )
    background_tasks.add_task(approve_document_background, document_id, actor_user_id=user.sub, actor_role=user.role)
    logger.info("Duyệt văn bản %s bởi %s", document_id, user.sub)
    db.expire_all()  # đọc lại dòng vừa bị UPDATE
    return {"data": _detail(*document_repo.get_document_with_length(db, document_id)), "error": None, "request_id": request_id}


def _replaceable_document(db: Session, document_id: uuid.UUID) -> LegalDocument:
    """Văn bản để thay nội dung: phải có, đang active và không đang processing (kéo cả cột content để so với bản mới)."""
    doc = document_repo.get_document(db, document_id)
    if not doc:
        raise _not_found()
    if doc.status != "active":
        raise _invalid_state("Chỉ thay được nội dung văn bản đang có hiệu lực (active).")
    if doc.ingest_status == "processing":
        raise _invalid_state("Văn bản đang được xử lý.")
    return doc


@router.post("/{document_id}/replace/preview")
def preview_replace_document(
    document_id: uuid.UUID,
    body: DocumentReplaceRequest,
    request: Request,
    response: Response,
    _: TokenPayload = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Xem trước việc thay nội dung văn bản active (admin): kết quả cắt bản mới, so chunk với bản cũ (chunk nào dùng lại được
    vector, chunk nào phải embed, Điều nào đổi), ước tính chi phí embedding và cảnh báo. Chỉ ĐỌC DB, không gọi embedding."""
    request_id = _request_id(request, response)
    doc = _replaceable_document(db, document_id)
    old_chunks = document_repo.list_chunk_summaries(db, document_id)
    try:
        result = document_replace.build_replace_preview(
            doc.title, doc.content, old_chunks, body.content, get_settings().embedding_model
        )
    except EmptyContentError:
        raise _empty_content_error()
    return {"data": result, "error": None, "request_id": request_id}


@router.post("/{document_id}/replace", status_code=202)
def replace_document(
    document_id: uuid.UUID,
    body: DocumentReplaceRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    user: TokenPayload = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Thay nội dung văn bản active bằng bản mới (admin): trả 202 ngay, cắt chunk + embedding (chỉ chunk khác bản cũ) chạy ở nền.
    Văn bản vẫn active và tìm kiếm vẫn dùng bản cũ cho tới khi bản mới sẵn sàng, rồi đổi trong một giao dịch; lỗi thì bản cũ còn
    nguyên và ingest_status = failed (bấm lại được). Toàn văn cũ được lưu vào document_versions."""
    request_id = _request_id(request, response)
    doc = _replaceable_document(db, document_id)
    if not chunk_legal_text(body.content):
        raise _empty_content_error()
    if document_replace.same_content(doc.content, body.content):
        raise _invalid_state("Nội dung mới giống hệt nội dung hiện tại, không có thay đổi nào để thay.")
    doc_number, title = doc.doc_number, doc.title

    try:
        claimed = document_repo.claim_for_replace(db, document_id)
        db.commit()
    except Exception:
        db.rollback()
        raise
    if not claimed:
        raise _invalid_state("Văn bản vừa được người khác thay hoặc thay đổi trạng thái.")

    ingest_log.record(
        "replace_requested", actor_user_id=user.sub, actor_role=user.role,
        document_id=document_id, doc_number=doc_number, title=title,
    )
    background_tasks.add_task(
        replace_document_background, document_id, body.content, actor_user_id=user.sub, actor_role=user.role
    )
    logger.info("Thay nội dung văn bản %s bởi %s", document_id, user.sub)
    db.expire_all()  # đọc lại dòng vừa bị UPDATE
    return {"data": _detail(*document_repo.get_document_with_length(db, document_id)), "error": None, "request_id": request_id}


# TODO: implement document management endpoints
# PATCH  /{id}  → cập nhật metadata
# POST   /{id}/reprocess  → chunk + embed lại (admin only)
