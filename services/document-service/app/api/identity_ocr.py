from uuid import UUID

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException

from app.core.config import ConfigurationError, Settings
from app.core.errors import DocumentError, success
from app.core.security import document_user, document_writer
from app.repositories.database import Database
from app.repositories.identity_ocr import IdentityOcr
from app.services.identity_images import MAX_IDENTITY_IMAGE, prepare_identity_pair
from app.services.file_validation import MAX_MULTIPART
from app.services.storage import Storage
from app.services.local_ocr import LocalOcr
from app.services.ekyc_ocr import build_provider

router = APIRouter()


def get_identity_ocr():
    try:
        settings = Settings.from_env()
    except ConfigurationError as exc:
        raise DocumentError("CONFIGURATION_ERROR", str(exc)) from None
    return IdentityOcr(Database(settings), Storage(settings), build_provider(LocalOcr()))


@router.get("/identity-ocr/{session_id}")
def detail_identity_ocr(session_id: UUID, request: Request, user=Depends(document_user), service=Depends(get_identity_ocr)):
    return success(request, service.detail(user.id, session_id))


@router.post("/identity-ocr", status_code=201)
async def create_identity_ocr(request: Request, user=Depends(document_writer), service=Depends(get_identity_ocr)):
    try:
        key = UUID(request.headers.get("Idempotency-Key", ""))
    except ValueError:
        raise DocumentError("VALIDATION_ERROR", "Idempotency-Key phải là UUID.", 422) from None
    if request.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "multipart/form-data":
        raise DocumentError("BAD_REQUEST", "Hai ảnh phải dùng multipart/form-data.", 400)
    try:
        length = int(request.headers.get("Content-Length", "0"))
        if length < 0:
            raise ValueError
    except ValueError:
        raise DocumentError("BAD_REQUEST", "Content-Length không hợp lệ.", 400) from None
    if length > MAX_MULTIPART:
        raise DocumentError("FILE_TOO_LARGE", "Multipart vượt giới hạn 10 MiB.", 413)
    received = 0
    async def bounded_receive():
        nonlocal received
        message = await request.receive()
        received += len(message.get("body", b""))
        if received > MAX_MULTIPART:
            raise MultiPartException("IDENTITY_MULTIPART_TOO_LARGE")
        return message
    bounded = Request(request.scope, receive=bounded_receive)
    try:
        async with bounded.form(max_files=2, max_fields=0, max_part_size=MAX_IDENTITY_IMAGE) as form:
            if set(form) != {"front", "back"} or len(form.multi_items()) != 2 or any(
                    not isinstance(form[side], UploadFile) for side in ("front", "back")):
                raise DocumentError("VALIDATION_ERROR", "Chỉ gửi hai file front và back.", 422)
            values = []
            for side in ("front", "back"):
                upload = form[side]
                values.append((upload.filename, upload.content_type or "", await upload.read(MAX_IDENTITY_IMAGE + 1)))
            pair = await run_in_threadpool(prepare_identity_pair, *values)
    except MultiPartException:
        raise DocumentError("FILE_TOO_LARGE", "Multipart vượt giới hạn 10 MiB.", 413) from None
    except (ValueError, UnicodeDecodeError):
        raise DocumentError("BAD_REQUEST", "Multipart không hợp lệ.", 400) from None
    except Exception:
        # Starlette turns parser failures into HTTPException, as in the upload API.
        if received > MAX_MULTIPART:
            raise DocumentError("FILE_TOO_LARGE", "Multipart vượt giới hạn 10 MiB.", 413) from None
        raise
    row = await run_in_threadpool(service.create, user.id, key, pair)
    return success(request, row)
