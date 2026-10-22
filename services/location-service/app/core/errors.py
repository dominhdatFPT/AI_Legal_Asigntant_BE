"""Location response envelopes and safe error handling."""
import logging
import re
import time
import uuid

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from app.core.metrics import provider_counter


logger = logging.getLogger("location-service")


class LocationError(Exception):
    def __init__(self, code: str, message: str, status: int = 503, *, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details


def request_id(request):
    return getattr(request.state, "request_id", f"req-{uuid.uuid4()}")


def success(request, data):
    return {"data": data, "error": None, "request_id": request_id(request)}


def failure(request, status, code, message, *, details=None, headers=None):
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse(
        status_code=status,
        content={"data": None, "error": error, "request_id": request_id(request)},
        headers={**(headers or {}), "X-Request-ID": request_id(request), "Cache-Control": "no-store"},
    )


def install_error_handlers(app):
    @app.middleware("http")
    async def correlation(request, call_next):
        started = time.monotonic()
        counter = [0]
        token = provider_counter.set(counter)
        candidate = request.headers.get("X-Request-ID", "")
        request.state.request_id = candidate if re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", candidate) else f"req-{uuid.uuid4()}"
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request.state.request_id
            response.headers["Cache-Control"] = "no-store"
            return response
        finally:
            logger.info("request_id=%s operation=%s status=%s duration_ms=%d upstream_calls=%d",
                        request.state.request_id, request.url.path.split("?")[0],
                        response.status_code if "response" in locals() else 500,
                        round((time.monotonic() - started) * 1000), counter[0])
            provider_counter.reset(token)

    @app.exception_handler(LocationError)
    async def location_error(request, exc):
        return failure(request, exc.status, exc.code, exc.message, details=exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        if any(error["type"] == "json_invalid" for error in exc.errors()):
            return failure(request, 400, "BAD_REQUEST", "JSON gửi lên không đọc được.")
        details = [{"field": ".".join(map(str, error["loc"])), "message": "Dữ liệu không hợp lệ."}
                   for error in exc.errors()]
        return failure(request, 422, "VALIDATION_ERROR", "Kiểm tra dữ liệu gửi lên.", details=details)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return failure(request, exc.status_code, code, "Yêu cầu không được thực hiện.", headers=exc.headers)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        logger.error("Unhandled Location error; request_id=%s", request_id(request))
        return failure(request, 500, "INTERNAL_ERROR", "Có lỗi hệ thống, vui lòng thử lại.")
