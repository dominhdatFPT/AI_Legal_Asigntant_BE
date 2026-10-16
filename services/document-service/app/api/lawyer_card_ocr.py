"""Ảnh thẻ luật sư cho đơn xin làm luật sư: POST/GET /api/v1/documents/lawyer-card-ocr (chủ lấy từ JWT sub)."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartException

from app.core.config import ConfigurationError, Settings
from app.core.errors import DocumentError, success
from app.core.security import document_user, document_writer
from app.repositories.database import Database
from app.repositories.lawyer_cards import LawyerCards
from app.services.file_validation import MAX_MULTIPART
from app.services.lawyer_card_images import MAX_LAWYER_CARD_IMAGE, prepare_lawyer_card_image
from app.services.lawyer_card_ocr import LocalLawyerCardOcr
from app.services.storage import Storage

router = APIRouter()


def get_lawyer_cards():
    try:
        settings = Settings.from_env()
    except ConfigurationError:
        raise DocumentError("CONFIGURATION_ERROR", "Cấu hình Document chưa sẵn sàng.") from None
    return LawyerCards(Database(settings), Storage(settings), LocalLawyerCardOcr())


@router.get("/lawyer-card-ocr/{card_id}")
def detail_lawyer_card(card_id: UUID, request: Request, user=Depends(document_user), cards=Depends(get_lawyer_cards)):
    return success(request, cards.detail(user.id, card_id))


@router.post("/lawyer-card-ocr", status_code=201)
async def create_lawyer_card(request: Request, user=Depends(document_writer), cards=Depends(get_lawyer_cards)):
    try:
        key = UUID(request.headers.get("Idempotency-Key", ""))
    except ValueError:
        raise DocumentError("VALIDATION_ERROR", "Idempotency-Key phải là UUID.", 422) from None
    if request.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "multipart/form-data":
        raise DocumentError("BAD_REQUEST", "Ảnh thẻ phải gửi bằng multipart/form-data.", 400)
    length = request.headers.get("Content-Length")
    if length:
        try:
            if int(length) < 0:
                raise ValueError
        except ValueError:
            raise DocumentError("BAD_REQUEST", "Content-Length không hợp lệ.", 400) from None
        if int(length) > MAX_MULTIPART:
            raise DocumentError("FILE_TOO_LARGE", "Multipart vượt giới hạn 10 MiB.", 413)
    received = 0

    async def bounded_receive():
        nonlocal received
        message = await request.receive()
        received += len(message.get("body", b""))
        if received > MAX_MULTIPART:
            raise MultiPartException("LAWYER_CARD_MULTIPART_TOO_LARGE")
        return message

    bounded = Request(request.scope, receive=bounded_receive)
    try:
        async with bounded.form(max_files=1, max_fields=0, max_part_size=MAX_LAWYER_CARD_IMAGE) as form:
            if set(form) != {"file"} or len(form.multi_items()) != 1 or not isinstance(form["file"], UploadFile):
                raise DocumentError("VALIDATION_ERROR", "Chỉ gửi một file ảnh thẻ ở trường file.", 422)
            upload = form["file"]
            content = await upload.read(MAX_LAWYER_CARD_IMAGE + 1)
            image, fingerprint = await run_in_threadpool(prepare_lawyer_card_image, upload.filename or "",
                                                         upload.content_type or "", content)
    except MultiPartException:
        raise DocumentError("FILE_TOO_LARGE", "Multipart vượt giới hạn 10 MiB.", 413) from None
    except HTTPException:
        if received > MAX_MULTIPART:
            raise DocumentError("FILE_TOO_LARGE", "Multipart vượt giới hạn 10 MiB.", 413) from None
        raise DocumentError("BAD_REQUEST", "Multipart không hợp lệ hoặc có quá nhiều phần.", 400) from None
    row = await run_in_threadpool(cards.create, user.id, key, image, fingerprint)
    return success(request, row)
