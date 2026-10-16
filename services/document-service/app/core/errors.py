import logging
import re
import uuid

from common_auth.fastapi_dep import AuthHTTPException
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

logger = logging.getLogger("document-service")


class DocumentError(Exception):
    def __init__(self, code, message, status=503, details=None, *, current_revision=None):
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details
        self.current_revision = current_revision


def request_id(request):
    return getattr(request.state, "request_id", f"req-{uuid.uuid4()}")


def success(request, data):
    return {"data": data, "error": None, "request_id": request_id(request)}


def failure(request, status, code, message, details=None, headers=None, current_revision=None):
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    if current_revision is not None:
        error["current_revision"] = current_revision
    return JSONResponse(status_code=status, content={"data": None, "error": error,
                        "request_id": request_id(request)},
                        headers={**(headers or {}), "X-Request-ID": request_id(request), "Cache-Control": "no-store"})


def install_error_handlers(app):
    @app.middleware("http")
    async def correlation(request, call_next):
        candidate = request.headers.get("X-Request-ID", "")
        request.state.request_id = candidate if re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", candidate) else f"req-{uuid.uuid4()}"
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(AuthHTTPException)
    async def auth_error(request, exc):
        return failure(request, exc.status_code, exc.detail["code"], exc.detail["message"], headers=exc.headers)

    @app.exception_handler(DocumentError)
    async def document_error(request, exc):
        return failure(request, exc.status, exc.code, exc.message, exc.details, current_revision=exc.current_revision)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        if any(error["type"] == "json_invalid" for error in exc.errors()):
            return failure(request, 400, "BAD_REQUEST", "JSON gửi lên không đọc được.")
        # Do not echo Pydantic input/context: it can contain CCCD, file bytes or tokens.
        details = [{"field": ".".join(str(part) for part in error["loc"]), "message": "Dữ liệu không hợp lệ."}
                   for error in exc.errors()]
        return failure(request, 422, "VALIDATION_ERROR", "Kiểm tra dữ liệu gửi lên.", details)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return failure(request, exc.status_code, code, "Yêu cầu không được thực hiện.", headers=exc.headers)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        # No traceback/message/body: third-party exceptions may include credentials or PII.
        logger.error("Unhandled Document error; request_id=%s", request_id(request))
        return failure(request, 500, "INTERNAL_ERROR", "Có lỗi hệ thống, vui lòng thử lại.")
