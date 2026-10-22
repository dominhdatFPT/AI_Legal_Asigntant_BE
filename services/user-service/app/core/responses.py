import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

INVALID_REFRESH_TOKEN = "INVALID_REFRESH_TOKEN"
INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
INVALID_FIREBASE_TOKEN = "INVALID_FIREBASE_TOKEN"
EMAIL_NOT_VERIFIED = "EMAIL_NOT_VERIFIED"
EMAIL_IN_USE = "EMAIL_IN_USE"
PHONE_IN_USE = "PHONE_IN_USE"
ACCOUNT_LOCKED = "ACCOUNT_LOCKED"
UNAUTHORIZED = "UNAUTHORIZED"
VALIDATION_ERROR = "VALIDATION_ERROR"
INTERNAL_ERROR = "INTERNAL_ERROR"
CCCD_ALREADY_USED = "CCCD_ALREADY_USED"
USER_NOT_FOUND = "USER_NOT_FOUND"
FORBIDDEN = "FORBIDDEN"
ACCOUNT_DELETED = "ACCOUNT_DELETED"
EMAIL_DELETED = "EMAIL_DELETED"
USER_ALREADY_LOCKED = "USER_ALREADY_LOCKED"
USER_NOT_LOCKED = "USER_NOT_LOCKED"
USER_DELETED = "USER_DELETED"
USER_NOT_DELETED = "USER_NOT_DELETED"
CANNOT_LOCK_SELF = "CANNOT_LOCK_SELF"
CANNOT_DELETE_SELF = "CANNOT_DELETE_SELF"
CANNOT_CHANGE_OWN_ROLE = "CANNOT_CHANGE_OWN_ROLE"
ROLE_CHANGE_NOT_ALLOWED = "ROLE_CHANGE_NOT_ALLOWED"

MESSAGES = {
    INVALID_CREDENTIALS: "Tên đăng nhập hoặc mật khẩu không chính xác.",
    INVALID_FIREBASE_TOKEN: "Đăng nhập không hợp lệ, vui lòng thử lại.",
    EMAIL_NOT_VERIFIED: "Vui lòng xác thực email trước khi đăng nhập.",
    EMAIL_IN_USE: "Email này đã được dùng cho một tài khoản khác.",
    PHONE_IN_USE: "Số điện thoại này đã được dùng cho một tài khoản khác.",
    INVALID_REFRESH_TOKEN: "Phiên đăng nhập đã hết hạn, vui lòng đăng nhập lại.",
    ACCOUNT_LOCKED: "Tài khoản của bạn đã bị khóa. Vui lòng liên hệ quản trị viên.",
    UNAUTHORIZED: "Bạn cần đăng nhập để tiếp tục.",
    VALIDATION_ERROR: "Dữ liệu gửi lên không hợp lệ.",
    INTERNAL_ERROR: "Hệ thống đang gặp lỗi, vui lòng thử lại sau.",
    CCCD_ALREADY_USED: "Số CCCD này đã được dùng cho một tài khoản khác.",
    USER_NOT_FOUND: "Không tìm thấy người dùng.",
    FORBIDDEN: "Bạn không có quyền thực hiện thao tác này.",
    ACCOUNT_DELETED: "Tài khoản này đã bị xóa. Vui lòng liên hệ quản trị viên để được khôi phục.",
    EMAIL_DELETED: "Email này thuộc một tài khoản đã bị xóa. Hãy khôi phục tài khoản đó thay vì tạo mới.",
    USER_ALREADY_LOCKED: "Tài khoản này đã bị khóa.",
    USER_NOT_LOCKED: "Tài khoản này không bị khóa.",
    USER_DELETED: "Tài khoản này đã bị xóa, hãy khôi phục trước khi thao tác.",
    USER_NOT_DELETED: "Tài khoản này chưa bị xóa.",
    CANNOT_LOCK_SELF: "Bạn không thể tự khóa tài khoản của mình.",
    CANNOT_DELETE_SELF: "Bạn không thể tự xóa tài khoản của mình.",
    CANNOT_CHANGE_OWN_ROLE: "Bạn không thể tự đổi vai trò của mình.",
    ROLE_CHANGE_NOT_ALLOWED: "Không thể chuyển tài khoản này sang vai trò luật sư.",
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str | None = None):
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message or MESSAGES[code]


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
    return JSONResponse(
        status_code=status,
        content={"data": None, "error": err, "request_id": rid},
        headers={"X-Request-ID": rid},
    )


async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
    return error(request, exc.status, exc.code, exc.message)


async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {"field": ".".join(str(p) for p in e["loc"] if p != "body") or "body", "message": e["msg"]}
        for e in exc.errors()
    ]
    return error(request, 422, VALIDATION_ERROR, MESSAGES[VALIDATION_ERROR], details)


async def _internal_error(request: Request, exc: Exception) -> JSONResponse:
    return error(request, 500, INTERNAL_ERROR, MESSAGES[INTERNAL_ERROR])


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _internal_error)
