"""Format {data, error, request_id}, mã lỗi và handler lỗi của Consultation Service."""

import contextvars
import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

logger = logging.getLogger("consultation-service")

UNAUTHORIZED = "UNAUTHORIZED"
FORBIDDEN = "FORBIDDEN"
VALIDATION_ERROR = "VALIDATION_ERROR"
INTERNAL_ERROR = "INTERNAL_ERROR"
ACCOUNT_LOCKED = "ACCOUNT_LOCKED"
LAWYER_NOT_APPROVED = "LAWYER_NOT_APPROVED"
CONSULTATION_DB_UNAVAILABLE = "CONSULTATION_DB_UNAVAILABLE"
CONSULTATION_NOT_FOUND = "CONSULTATION_NOT_FOUND"
CONSULTATION_INVALID_STATUS = "CONSULTATION_INVALID_STATUS"
CONSULTATION_ALREADY_ACCEPTED = "CONSULTATION_ALREADY_ACCEPTED"
CONSULTATION_ALREADY_OPEN = "CONSULTATION_ALREADY_OPEN"
CANNOT_ACCEPT_OWN_CONSULTATION = "CANNOT_ACCEPT_OWN_CONSULTATION"
TERMS_NOT_ACCEPTED = "TERMS_NOT_ACCEPTED"
WAITING_LIMIT_REACHED = "WAITING_LIMIT_REACHED"
LAWYER_CAPACITY_REACHED = "LAWYER_CAPACITY_REACHED"
DIRECT_NOT_ALLOWED = "DIRECT_NOT_ALLOWED"
SUMMARY_NOT_AVAILABLE = "SUMMARY_NOT_AVAILABLE"

MESSAGES = {
    UNAUTHORIZED: "Bạn cần đăng nhập để tiếp tục.",
    FORBIDDEN: "Bạn không có quyền thực hiện thao tác này.",
    VALIDATION_ERROR: "Dữ liệu gửi lên không hợp lệ.",
    INTERNAL_ERROR: "Hệ thống đang gặp lỗi, vui lòng thử lại sau.",
    ACCOUNT_LOCKED: "Tài khoản của bạn đã bị khóa. Vui lòng liên hệ quản trị viên.",
    LAWYER_NOT_APPROVED: "Hồ sơ luật sư của bạn chưa được duyệt.",
    CONSULTATION_DB_UNAVAILABLE: "Hệ thống tư vấn tạm thời không truy cập được dữ liệu, vui lòng thử lại sau.",
    CONSULTATION_NOT_FOUND: "Không tìm thấy cuộc tư vấn.",
    CONSULTATION_INVALID_STATUS: "Cuộc tư vấn đang ở trạng thái không thực hiện được thao tác này.",
    CONSULTATION_ALREADY_ACCEPTED: "Yêu cầu này đã có luật sư khác nhận.",
    CONSULTATION_ALREADY_OPEN: "Hai bên đang có một cuộc tư vấn chưa đóng.",
    CANNOT_ACCEPT_OWN_CONSULTATION: "Bạn không thể nhận yêu cầu của chính mình.",
    TERMS_NOT_ACCEPTED: "Bạn cần đồng ý điều khoản tư vấn trước khi tiếp tục.",
    WAITING_LIMIT_REACHED: "Bạn đã có 10 yêu cầu đang chờ luật sư nhận. Vui lòng chờ trước khi gửi thêm.",
    LAWYER_CAPACITY_REACHED: "Luật sư đang tư vấn tối đa 3 cuộc cùng lúc. Vui lòng thử lại sau.",
    SUMMARY_NOT_AVAILABLE: "Yêu cầu này không có cuộc trò chuyện với AI để tóm tắt.",
    DIRECT_NOT_ALLOWED: "Bạn chỉ nhắn lại được luật sư đã tư vấn xong cho bạn và vẫn còn hoạt động.",
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str | None = None, details: list[dict] | None = None):
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message or MESSAGES.get(code) or MESSAGES[INTERNAL_ERROR]
        self.details = details


def invalid(field: str, message: str) -> ApiError:
    return ApiError(422, VALIDATION_ERROR, details=[{"field": field, "message": message}])


# Mã lần gọi của request đang xử lý; client gọi service khác đọc ở đây để truyền tiếp X-Request-ID.
current_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("consultation_request_id", default=None)


def request_id(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    if rid is None:
        rid = request.headers.get("X-Request-ID") or f"req-{uuid.uuid4()}"
        request.state.request_id = rid
    return rid


def ok(request: Request, data: Any, status: int = 200) -> JSONResponse:
    rid = request_id(request)
    body = {"data": jsonable_encoder(data), "error": None, "request_id": rid}
    return JSONResponse(status_code=status, content=body, headers={"X-Request-ID": rid})


def error(request: Request, status: int, code: str, message: str, details: list | None = None) -> JSONResponse:
    rid = request_id(request)
    err = {"code": code, "message": message}
    if details:
        err["details"] = details
    return JSONResponse(status_code=status, content={"data": None, "error": err, "request_id": rid},
                        headers={"X-Request-ID": rid})


async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
    return error(request, exc.status, exc.code, exc.message, exc.details)


async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    if any(e.get("type") == "json_invalid" for e in exc.errors()):
        return error(request, 400, "BAD_REQUEST", "JSON gửi lên không đọc được.")
    details = [
        {"field": ".".join(str(p) for p in e["loc"] if p != "body") or "body", "message": e["msg"]}
        for e in exc.errors()
    ]
    return error(request, 422, VALIDATION_ERROR, MESSAGES[VALIDATION_ERROR], details)


async def _db_error(request: Request, exc: OperationalError) -> JSONResponse:
    logger.error("Consultation DB lỗi kết nối; request_id=%s", request_id(request))
    return error(request, 503, CONSULTATION_DB_UNAVAILABLE, MESSAGES[CONSULTATION_DB_UNAVAILABLE])


async def _internal_error(request: Request, exc: Exception) -> JSONResponse:
    # Không log nội dung exception: có thể chứa dữ liệu cá nhân trong câu SQL.
    logger.error("Lỗi không lường trước %s; request_id=%s", type(exc).__name__, request_id(request))
    return error(request, 500, INTERNAL_ERROR, MESSAGES[INTERNAL_ERROR])


def install_error_handlers(app: FastAPI) -> None:
    @app.middleware("http")
    async def correlation(request: Request, call_next):
        token = current_request_id.set(request_id(request))
        try:
            response = await call_next(request)
        finally:
            current_request_id.reset(token)
        response.headers["X-Request-ID"] = request_id(request)
        return response

    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(OperationalError, _db_error)
    app.add_exception_handler(Exception, _internal_error)
