import hmac
import os
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from starlette.formparsers import MultiPartException

from app.api.procedures import error_response
from app.services import procedure_registration
from app.services.procedure_registration import FormTemplateUnavailable, ProcedureCodeConflict, ProcedureCodeExists
from app.services.procedure_service import invalidate_procedure_caches

router = APIRouter(prefix="/internal/v1/procedures", tags=["Internal"])
_MAX_PDF_BYTES = 10 * 1024 * 1024
_MAX_REGISTER_BODY_BYTES = _MAX_PDF_BYTES + 64 * 1024


def _authorized(token: str | None) -> bool:
    expected = os.getenv("INTERNAL_TOKEN", "")
    return bool(expected and token and hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8")))


def _pdf_too_large(request_id: str):
    return error_response(422, "PDF_TOO_LARGE", "Tệp PDF không được lớn hơn 10 MB.", request_id)


class InternalRequestMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path, root_path = scope["path"], scope.get("root_path", "")
        if root_path and path.startswith(root_path + "/"):
            path = path[len(root_path):]
        if not path.startswith("/internal/"):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        request_id = request.state.request_id
        if not _authorized(request.headers.get("X-Internal-Token")):
            response = error_response(401, "UNAUTHORIZED", "Internal token không hợp lệ.", request_id)
            await response(scope, receive, send)
            return
        if request.method != "POST" or path.rstrip("/") not in (
            "/internal/v1/procedures", "/internal/v1/procedures/register",
        ):
            await self.app(scope, receive, send)
            return

        try:
            content_length = int(request.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0
        if content_length > _MAX_REGISTER_BODY_BYTES:
            await _pdf_too_large(request_id)(scope, receive, send)
            return

        size = 0
        oversized = False

        async def limited_receive():
            nonlocal size, oversized
            message = await receive()
            size += len(message.get("body", b""))
            if size > _MAX_REGISTER_BODY_BYTES:
                oversized = True
                # The multipart parser closes partially written files on this exception.
                raise MultiPartException("Internal upload exceeds body limit")
            return message

        async def guarded_send(message):
            if not oversized:
                await send(message)

        await self.app(scope, limited_receive, guarded_send)
        if oversized:
            await _pdf_too_large(request_id)(scope, receive, send)


@router.get("/next-code")
def next_code(
    request: Request,
    domain: Literal["land", "business"],
    floor: int = Query(0, ge=0),
):
    code, number = procedure_registration.next_code(domain, floor)
    return {"data": {"code": code, "number": number}, "error": None, "request_id": request.state.request_id}


@router.get("/{procedure_id}")
def lookup(request: Request, procedure_id: UUID):
    request_id = request.state.request_id
    if not procedure_registration.exists(procedure_id):
        return error_response(404, "PROCEDURE_NOT_FOUND", "Không tìm thấy thủ tục.", request_id)
    return {"data": {"procedure_id": str(procedure_id)}, "error": None, "request_id": request_id}


@router.post("", status_code=201)
def register(
    request: Request,
    procedure_id: UUID = Form(...),
    name: str = Form(..., min_length=5, max_length=255),
    domain: Literal["land", "business"] = Form(...),
    code: str | None = Form(None),
    floor: int = Form(0, ge=0),
    form_template_id: UUID | None = Form(None),
    reference_pdf: UploadFile | None = File(None),
):
    request_id = request.state.request_id
    if reference_pdf and reference_pdf.size is not None and reference_pdf.size > _MAX_PDF_BYTES:
        return _pdf_too_large(request_id)
    pdf_data = reference_pdf.file.read(_MAX_PDF_BYTES + 1) if reference_pdf else None
    if pdf_data is not None and len(pdf_data) > _MAX_PDF_BYTES:
        return _pdf_too_large(request_id)

    try:
        result = procedure_registration.register(
            procedure_id=str(procedure_id), name=name, domain=domain, code=code, floor=floor,
            form_template_id=str(form_template_id) if form_template_id else None,
            pdf_data=pdf_data, pdf_filename=reference_pdf.filename if reference_pdf else None,
        )
    except ProcedureCodeExists as exc:
        return error_response(409, "PROCEDURE_CODE_EXISTS", f"Mã thủ tục {exc.code} đã tồn tại.", request_id)
    except ProcedureCodeConflict:
        return error_response(
            409, "PROCEDURE_CODE_CONFLICT", "Không thể cấp phát mã thủ tục mới lúc này, vui lòng thử lại.", request_id,
        )
    except FormTemplateUnavailable:
        return error_response(422, "FORM_TEMPLATE_UNAVAILABLE", "Không thể gắn biểu mẫu đã chọn.", request_id)
    invalidate_procedure_caches()
    return JSONResponse(
        status_code=201 if result["created"] else 200,
        content={"data": result, "error": None, "request_id": request_id},
    )
