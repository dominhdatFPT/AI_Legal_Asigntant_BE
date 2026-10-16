from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from common_auth import CurrentUser
from common_auth.fastapi_dep import get_current_user

from app.core import cookies
from app.core.database import get_db
from app.core.responses import INVALID_REFRESH_TOKEN, ApiError, error, ok
from app.schemas.auth import FirebaseLoginRequest, PasswordLoginRequest, RefreshTokenRequest, RegisterRequest
from app.schemas.user import UserOut
from app.services import auth_service

router = APIRouter(prefix="/api/v1/users/auth", tags=["Auth"])


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
def logout(
    request: Request,
    body: RefreshTokenRequest | None = None,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    raw = cookies.refresh_token_from(request, body.refresh_token if body else None)
    if raw:
        auth_service.logout(db, raw, current.id)
    response = ok(request, {"logged_out": True})
    cookies.clear_auth_cookies(response)
    return response
