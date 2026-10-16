"""Format {data, error, request_id}, mã lỗi và handler lỗi của Lawyer Service."""

import contextvars
import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

logger = logging.getLogger("lawyer-service")

UNAUTHORIZED = "UNAUTHORIZED"
FORBIDDEN = "FORBIDDEN"
VALIDATION_ERROR = "VALIDATION_ERROR"
INTERNAL_ERROR = "INTERNAL_ERROR"
ACCOUNT_LOCKED = "ACCOUNT_LOCKED"
LAWYER_DB_UNAVAILABLE = "LAWYER_DB_UNAVAILABLE"
LAWYER_NOT_FOUND = "LAWYER_NOT_FOUND"
APPLICATION_NOT_FOUND = "APPLICATION_NOT_FOUND"
LAWYER_PROFILE_EXISTS = "LAWYER_PROFILE_EXISTS"
OPEN_APPLICATION_EXISTS = "OPEN_APPLICATION_EXISTS"
CARD_NOT_SET = "CARD_NOT_SET"
CARD_CHANGE_REQUIRES_REVIEW = "CARD_CHANGE_REQUIRES_REVIEW"
BAR_CARD_NUMBER_TAKEN = "BAR_CARD_NUMBER_TAKEN"
APPLICATION_NOT_EDITABLE = "APPLICATION_NOT_EDITABLE"
APPLICATION_NOT_DRAFT = "APPLICATION_NOT_DRAFT"
APPLICATION_NOT_PENDING = "APPLICATION_NOT_PENDING"
CITIZEN_PROFILE_REQUIRED = "CITIZEN_PROFILE_REQUIRED"
REAPPLY_COOLDOWN = "REAPPLY_COOLDOWN"
APPLICANT_UNAVAILABLE = "APPLICANT_UNAVAILABLE"

MESSAGES = {
    UNAUTHORIZED: "Bạn cần đăng nhập để tiếp tục.",
    FORBIDDEN: "Bạn không có quyền thực hiện thao tác này.",
    VALIDATION_ERROR: "Dữ liệu gửi lên không hợp lệ.",
    INTERNAL_ERROR: "Hệ thống đang gặp lỗi, vui lòng thử lại sau.",
    ACCOUNT_LOCKED: "Tài khoản của bạn đã bị khóa. Vui lòng liên hệ quản trị viên.",
    LAWYER_DB_UNAVAILABLE: "Hệ thống luật sư tạm thời không truy cập được dữ liệu, vui lòng thử lại sau.",
    LAWYER_NOT_FOUND: "Không tìm thấy hồ sơ luật sư.",
    APPLICATION_NOT_FOUND: "Không tìm thấy đơn.",
    LAWYER_PROFILE_EXISTS: "Bạn đã có hồ sơ luật sư.",
    OPEN_APPLICATION_EXISTS: "Bạn đang có một đơn chưa xử lý xong, hãy sửa hoặc rút đơn đó trước.",
    CARD_NOT_SET: "Hồ sơ chưa có số thẻ hành nghề, hãy điền trực tiếp ở hồ sơ luật sư.",
    CARD_CHANGE_REQUIRES_REVIEW: "Đổi số thẻ hành nghề cần admin duyệt, vui lòng tạo đơn đổi thông tin thẻ.",
    BAR_CARD_NUMBER_TAKEN: "Số thẻ hành nghề này đã được đăng ký.",
    APPLICATION_NOT_EDITABLE: "Đơn này đã được xử lý, không sửa được nữa.",
    APPLICATION_NOT_DRAFT: "Chỉ gửi được đơn đang ở trạng thái nháp.",
    APPLICATION_NOT_PENDING: "Đơn này đã được xử lý trước đó.",
    CITIZEN_PROFILE_REQUIRED: "Bạn cần khai hồ sơ cá nhân (CCCD) trước khi gửi đơn.",
    REAPPLY_COOLDOWN: "Đơn trước của bạn vừa bị từ chối, vui lòng chờ rồi gửi lại.",
    APPLICANT_UNAVAILABLE: "Tài khoản người gửi đơn không còn hợp lệ, hãy từ chối đơn này.",
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
current_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("lawyer_request_id", default=None)


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
    logger.error("Lawyer DB lỗi kết nối; request_id=%s", request_id(request))
    return error(request, 503, LAWYER_DB_UNAVAILABLE, MESSAGES[LAWYER_DB_UNAVAILABLE])


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
