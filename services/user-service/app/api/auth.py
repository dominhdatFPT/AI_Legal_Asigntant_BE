import jwt
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from common_auth import ACCESS_COOKIE, AuthError, CurrentUser, decode_access_token, extract_token
from common_auth.fastapi_dep import AuthHTTPException

from app.core import cookies, rabbitmq, token_blacklist
from app.core.database import get_db
from app.core.responses import INVALID_REFRESH_TOKEN, ApiError, error, ok
from app.schemas.auth import FirebaseLoginRequest, PasswordLoginRequest, RefreshTokenRequest, RegisterRequest
from app.schemas.user import UserOut
from app.services import auth_service

router = APIRouter(prefix="/api/v1/users/auth", tags=["Auth"])


def get_logout_user(request: Request) -> CurrentUser:
    """Như get_current_user nhưng chấp nhận token đã bị thu hồi: logout gọi lại lần hai vẫn trả 200."""
    try:
        token = extract_token(request.headers.get("Authorization"), request.cookies.get(ACCESS_COOKIE))
        return decode_access_token(token, check_revoked=False)
    except AuthError as e:
        raise AuthHTTPException(e)


@router.post("/firebase")
def login_with_firebase(body: FirebaseLoginRequest, request: Request, db: Session = Depends(get_db)):
    user, pair, is_new_user = auth_service.login_with_firebase(db, body.id_token, request.headers.get("User-Agent"))
    response = ok(request, {**pair, "user": UserOut.model_validate(user), "is_new_user": is_new_user})
    cookies.set_auth_cookies(response, pair)
    return response


@router.post("/register", status_code=201)
def register(body: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    user, pair = auth_service.register(db, body, request.headers.get("User-Agent"))
    response = ok(request, {**pair, "user": UserOut.model_validate(user), "is_new_user": True}, status=201)
    cookies.set_auth_cookies(response, pair)
    return response


@router.post("/login")
def login_with_password(body: PasswordLoginRequest, request: Request, db: Session = Depends(get_db)):
    user, pair = auth_service.login_with_password(db, body.username, body.password, request.headers.get("User-Agent"))
    response = ok(request, {**pair, "user": UserOut.model_validate(user), "is_new_user": False})
    cookies.set_auth_cookies(response, pair)
    return response


@router.post("/refresh")
def refresh_token(request: Request, body: RefreshTokenRequest | None = None, db: Session = Depends(get_db)):
    raw = cookies.refresh_token_from(request, body.refresh_token if body else None)
    try:
        if not raw:
            raise ApiError(401, INVALID_REFRESH_TOKEN)
        pair = auth_service.refresh(db, raw, request.headers.get("User-Agent"))
    except ApiError as e:
        # Refresh hỏng thì xoá cookie để trình duyệt không gửi lại token chết mãi.
        response = error(request, e.status, e.code, e.message)
        cookies.clear_auth_cookies(response)
        return response
    response = ok(request, pair)
    cookies.set_auth_cookies(response, pair)
    return response


@router.post("/logout")
async def logout(
    request: Request,
    body: RefreshTokenRequest | None = None,
    current: CurrentUser = Depends(get_logout_user),
    db: Session = Depends(get_db),
):
    # 1. Thu hồi refresh token trong DB
    raw = cookies.refresh_token_from(request, body.refresh_token if body else None)
    if raw:
        auth_service.logout(db, raw, current.id)

    # 2. Trích xuất claims của access token để blacklist và gửi sang RabbitMQ
    jti = current.jti
    exp = None
    try:
        raw_access = extract_token(request.headers.get("Authorization"), request.cookies.get(ACCESS_COOKIE))
        claims = jwt.decode(raw_access, options={"verify_signature": False})
        if not jti:
            jti = claims.get("jti")
        exp = claims.get("exp")
    except Exception:
        pass

    # 3. Đưa jti vào local blacklist ngay lập tức
    if jti:
        token_blacklist.blacklist_token(jti, exp)

    # 4. Bắn sự kiện user.logged_out lên RabbitMQ (non-blocking / asynchronous)
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("User-Agent")
    request_id = request.headers.get("X-Request-ID")
    await rabbitmq.publish_user_logged_out(
        user_id=current.id,
        role=current.role,
        jti=jti,
        exp=exp,
        client_ip=client_ip,
        user_agent=user_agent,
        request_id=request_id,
    )

    # 5. Xóa sạch HttpOnly cookies trên trình duyệt
    response = ok(request, {"logged_out": True})
    cookies.clear_auth_cookies(response)
    return response
