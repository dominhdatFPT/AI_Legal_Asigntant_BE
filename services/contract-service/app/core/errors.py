"""Contract-local envelopes. Never echo validation input, SQL or upstream bodies."""
import logging
import re
import uuid

from common_auth.fastapi_dep import AuthHTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException


class ContractError(Exception):
    def __init__(self, code, message, status=503, *, details=None):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status
        self.details = details


def success(request, data):
    return {"data": data, "error": None, "request_id": request.state.request_id}


def failure(request, status, code, message, *, headers=None, details=None):
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse(status_code=status, content={"data": None, "error": error,
        "request_id": request.state.request_id}, headers=headers)


def install_handlers(app):
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

    @app.exception_handler(ContractError)
    async def contract_error(request, exc):
        return failure(request, exc.status, exc.code, exc.message,
            headers={"WWW-Authenticate": "Bearer"} if exc.status == 401 else None, details=exc.details)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        if any(item["type"] == "json_invalid" for item in exc.errors()):
            return failure(request, 400, "BAD_REQUEST", "JSON gửi lên không đọc được.")
        # Only locations, no Pydantic input/context (could include PII/secrets).
        details = [{"field": ".".join(map(str, item["loc"])), "message": "Dữ liệu không hợp lệ."}
            for item in exc.errors()]
        return failure(request, 422, "VALIDATION_ERROR", "Kiểm tra dữ liệu gửi lên.", details=details)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return failure(request, exc.status_code, code, "Yêu cầu không được thực hiện.", headers=exc.headers)

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        logging.getLogger("contract-service").error("Unhandled Contract error; request_id=%s", request.state.request_id)
        # ServerErrorMiddleware returns this outside the correlation middleware.
        return failure(request, 500, "INTERNAL_ERROR", "Có lỗi hệ thống, vui lòng thử lại.",
            headers={"X-Request-ID": request.state.request_id, "Cache-Control": "no-store"})
