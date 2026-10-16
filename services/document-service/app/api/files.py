from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartException
from python_multipart.exceptions import MultipartParseError

from app.core.config import ConfigurationError, Settings
from app.core.errors import DocumentError, success
from app.core.security import document_user, document_writer
from app.core.downloads import content_disposition
from app.repositories.database import Database
from app.repositories.files import Files
from app.services.file_validation import MAX_FILE, MAX_MULTIPART, inspect_file
from app.services.storage import Storage

router = APIRouter(dependencies=[Depends(document_user)])
Purpose = Literal["contract_template", "supporting_document"]
DocumentType = Literal["contract", "identity_card", "other"]


def get_files():
    try:
        settings = Settings.from_env()
    except ConfigurationError:
        raise DocumentError("CONFIGURATION_ERROR", "Cấu hình Document chưa sẵn sàng.") from None
    return Files(Database(settings), Storage(settings))


@router.get("/")
def list_documents(request: Request, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                   purpose: Purpose | None = None, document_type: DocumentType | None = None,
                   user=Depends(document_user), files=Depends(get_files)):
    return success(request, files.list(user.id, page, page_size, purpose, document_type))


@router.get("/{document_id}")
def get_document(request: Request, document_id: UUID, user=Depends(document_user), files=Depends(get_files)):
    return success(request, files.detail(user.id, document_id))


@router.get("/{document_id}/download")
def get_download(request: Request, document_id: UUID, user=Depends(document_user), files=Depends(get_files)):
    return success(request, files.download(user.id, document_id))


@router.get("/{document_id}/content")
def get_content(document_id: UUID, user=Depends(document_user), files=Depends(get_files)):
    content, media_type, filename, digest = files.content(user.id, document_id)
    return Response(content=content, media_type=media_type, headers={"Content-Disposition": content_disposition(filename),
                    "X-Content-SHA256": digest, "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


@router.delete("/{document_id}", status_code=204)
def delete_document(document_id: UUID, user=Depends(document_user), files=Depends(get_files)):
    files.delete(user.id, document_id)
    return Response(status_code=204)


@router.post("/", status_code=202)
async def upload_document(request: Request, user=Depends(document_writer), files=Depends(get_files)):
    try:
        key = UUID(request.headers.get("Idempotency-Key", ""))
    except ValueError:
        raise DocumentError("VALIDATION_ERROR", "Idempotency-Key phải là UUID.", 422) from None
    if request.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "multipart/form-data":
        raise DocumentError("BAD_REQUEST", "Upload phải dùng multipart/form-data.", 400)
    length = request.headers.get("Content-Length")
    if length:
        try:
            if int(length) < 0:
                raise ValueError
            if int(length) > MAX_MULTIPART:
                raise DocumentError("FILE_TOO_LARGE", "Multipart vượt giới hạn 10 MiB.", 413)
        except ValueError:
            raise DocumentError("BAD_REQUEST", "Content-Length không hợp lệ.", 400) from None
    received = 0
    async def bounded_receive():
        nonlocal received
        message = await request.receive()
        received += len(message.get("body", b""))
        if received > MAX_MULTIPART:
            # Parser catches this and closes already-opened spooled files before propagating.
            raise MultiPartException("DOCUMENT_MULTIPART_TOO_LARGE")
        return message
    bounded = Request(request.scope, receive=bounded_receive)
    try:
        async with bounded.form(max_files=1, max_fields=2, max_part_size=MAX_FILE) as form:
            if (set(form) != {"file", "purpose", "document_type"} or len(form.multi_items()) != 3
                    or not isinstance(form["file"], UploadFile)
                    or not isinstance(form["purpose"], str) or not isinstance(form["document_type"], str)):
                raise DocumentError("VALIDATION_ERROR", "Chỉ gửi một file, purpose và document_type.", 422)
            upload = form["file"]
            if upload.size is not None and upload.size > MAX_FILE:
                raise DocumentError("FILE_TOO_LARGE", "File vượt giới hạn 9 MiB.", 413)
            content = await upload.read(MAX_FILE + 1)
            admission = await run_in_threadpool(inspect_file, upload.filename, upload.content_type or "", content,
                                              form["purpose"], form["document_type"])
    except HTTPException as exc:
        if exc.detail == "DOCUMENT_MULTIPART_TOO_LARGE":
            raise DocumentError("FILE_TOO_LARGE", "Multipart vượt giới hạn 10 MiB.", 413) from None
        raise DocumentError("BAD_REQUEST", "Multipart không hợp lệ hoặc có quá nhiều phần.", 400) from None
    except MultipartParseError:
        raise DocumentError("BAD_REQUEST", "Multipart không đọc được.", 400) from None
    result = await run_in_threadpool(files.upload, user.id, key, admission)
    return success(request, result)
