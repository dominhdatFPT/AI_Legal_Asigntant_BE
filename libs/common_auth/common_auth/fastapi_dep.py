import uuid

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from . import revocation
from .core import ACCESS_COOKIE, FORBIDDEN, AuthError, CurrentUser, decode_access_token, extract_token


class AuthHTTPException(HTTPException):
    def __init__(self, error: AuthError):
        super().__init__(
            status_code=error.status,
            detail={"code": error.code, "message": error.message},
            headers={"WWW-Authenticate": "Bearer"} if error.status == 401 else None,
        )


def get_current_user(request: Request) -> CurrentUser:
    try:
        # Bearer header (Postman, mobile, service-to-service) hoặc HttpOnly cookie (trình duyệt).
        token = extract_token(request.headers.get("Authorization"), request.cookies.get(ACCESS_COOKIE))
        return decode_access_token(token)
    except AuthError as e:
        raise AuthHTTPException(e)


def require_role(*roles: str):
    def checker(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in roles:
            raise AuthHTTPException(AuthError(FORBIDDEN))
        return user

    return checker


async def _auth_error_response(request: Request, exc: AuthHTTPException) -> JSONResponse:
    request_id = request.headers.get("X-Request-ID") or f"req-{uuid.uuid4()}"
    headers = {**(exc.headers or {}), "X-Request-ID": request_id}
    return JSONResponse(
        status_code=exc.status_code,
        content={"data": None, "error": exc.detail, "request_id": request_id},
        headers=headers,
    )


def add_auth_error_handler(app: FastAPI) -> None:
    app.add_exception_handler(AuthHTTPException, _auth_error_response)
    # Nghe event thu hồi token ngay khi app khởi tạo, không chờ request đầu tiên.
    revocation.ensure_started()
