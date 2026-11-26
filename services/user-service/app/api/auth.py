import os
from datetime import datetime, timezone
from urllib.parse import urlsplit

import jwt
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session
from common_auth import ACCESS_COOKIE, AuthError, CurrentUser, decode_access_token, extract_token
from common_auth.fastapi_dep import AuthHTTPException

from app.core import cookies, rabbitmq, token_blacklist
from app.core.database import get_db
from app.core.responses import INVALID_REFRESH_TOKEN, ApiError, error, ok
from app.schemas.auth import FirebaseLoginRequest, PasswordLoginRequest, RefreshTokenRequest, RegisterRequest
from app.schemas.user import UserOut
from app.repositories import refresh_token_repository as refresh_tokens
from app.repositories import user_repository as users
from app.services import account_guard, auth_service

router = APIRouter(prefix="/api/v1/users/auth", tags=["Auth"])


def _browser_origin(value: str, *, referer: bool = False) -> str | None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.fragment or (not referer and (parsed.path or parsed.query))):
            return None
        host = parsed.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        default_port = 443 if parsed.scheme == "https" else 80
        return f"{parsed.scheme}://{host}" + (f":{port}" if port is not None and port != default_port else "")
    except ValueError:
        return None


def _require_browser_origin(request: Request) -> None:
    configured = os.getenv("AUTH_BROWSER_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    allowed = {_browser_origin(item.strip()) for item in configured.split(",")}
    allowed.discard(None)
    origins = request.headers.getlist("origin")
    if origins:
        origin = _browser_origin(origins[0]) if len(origins) == 1 else None
    else:
        referers = request.headers.getlist("referer")
        origin = _browser_origin(referers[0], referer=True) if len(referers) == 1 else None
    if origin is None or origin not in allowed:
        raise ApiError(403, "CSRF_ORIGIN_DENIED", "Nguồn yêu cầu không được phép.")


def _require_browser_origin_if_browser_or_strict(request: Request) -> None:
    headers = request.headers
    cross_site = any(value.strip().lower() == "cross-site" for value in headers.getlist("sec-fetch-site"))
    if "origin" in headers or "referer" in headers or cross_site:
        _require_browser_origin(request)


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


@router.post("/session")
def prepare_browser_session(
    request: Request,
    rejected_jti: str | None = Query(default=None, max_length=64),
    db: Session = Depends(get_db),
):
    """Cookie-only readiness protocol. Authentication failures are states, not HTTP errors."""
    _require_browser_origin(request)

    def reply(data: dict):
        response = ok(request, data)
        response.headers["Cache-Control"] = "no-store"
        return response

    def anonymous(code: str, message: str):
        # Do not send a stale Set-Cookie deletion over a newer login/refresh response.
        return reply({"state": "anonymous", "code": code, "message": message})

    raw_access = request.cookies.get(ACCESS_COOKIE)
    if raw_access:
        try:
            current = decode_access_token(raw_access)
        except AuthError:
            current = None
        if current is not None:
            user_id = auth_service.parse_user_id(current.id)
            user = users.get_by_id(db, user_id) if user_id else None
            if user is None:
                if user_id is not None:
                    refresh_tokens.revoke_all_for_user(db, user_id)
                    db.commit()
                return anonymous("UNAUTHORIZED", "Bạn cần đăng nhập để tiếp tục.")
            if account_guard.is_locked(user):
                refresh_tokens.revoke_all_for_user(db, user.id)
                db.commit()
                return anonymous("ACCOUNT_LOCKED", account_guard.locked_message(user))
            # Signature/type/issuer/revocation were checked above; only read metadata here.
            claims = jwt.decode(raw_access, options={"verify_signature": False})
            remaining = int(claims["exp"] - datetime.now(timezone.utc).timestamp())
            if (current.jti and remaining > 60 and current.jti != rejected_jti
                    and not token_blacklist.is_token_blacklisted(current.jti)):
                return reply({
                    "state": "ready", "jti": current.jti,
                    "user_id": current.id, "expires_in": remaining, "renewed": False,
                })

    raw_refresh = cookies.refresh_token_from(request, None)
    if not raw_refresh:
        return anonymous("UNAUTHORIZED", "Bạn cần đăng nhập để tiếp tục.")
    try:
        pair = auth_service.refresh(
            db, raw_refresh, request.headers.get("User-Agent"), contention_grace_seconds=10,
        )
    except ApiError as exc:
        db.rollback()
        if exc.code == "REFRESH_IN_PROGRESS":
            # No tokens, no Set-Cookie, no revocation. Wait for the winning tab's cookies.
            return reply({"state": "retry"})
        if exc.status in (401, 403):
            return anonymous(exc.code, exc.message)
        raise
    claims = jwt.decode(pair["access_token"], options={"verify_signature": False})
    response = reply({
        "state": "ready", "jti": claims["jti"], "user_id": claims["sub"],
        "expires_in": max(0, int(claims["exp"] - datetime.now(timezone.utc).timestamp())),
        "renewed": True,
    })
    cookies.set_auth_cookies(response, pair)
    return response


@router.post("/refresh")
def refresh_token(request: Request, body: RefreshTokenRequest | None = None, db: Session = Depends(get_db)):
    body_token = body.refresh_token if body else None
    if body_token is None:
        _require_browser_origin_if_browser_or_strict(request)
    raw = cookies.refresh_token_from(request, body_token)
    try:
        if not raw:
            raise ApiError(401, INVALID_REFRESH_TOKEN)
        pair = auth_service.refresh(db, raw, request.headers.get("User-Agent"))
    except ApiError as e:
        # Refresh hỏng thì xoá cookie để trình duyệt không gửi lại token chết mãi.
        response = error(request, e.status, e.code, e.message)
        cookies.clear_auth_cookies(response)
        return response
    data = pair
    if body_token is None and "origin" in request.headers:
        data = {"expires_in": pair["expires_in"], "token_type": "Bearer"}
    response = ok(request, data)
    response.headers["Cache-Control"] = "no-store"
    cookies.set_auth_cookies(response, pair)
    return response


@router.post("/logout")
async def logout(
    request: Request,
    body: RefreshTokenRequest | None = None,
    current: CurrentUser = Depends(get_logout_user),
    db: Session = Depends(get_db),
):
    body_token = body.refresh_token if body else None
    if body_token is None or not request.headers.get("Authorization"):
        _require_browser_origin_if_browser_or_strict(request)

    # 1. Thu hồi refresh token trong DB
    raw = cookies.refresh_token_from(request, body_token)
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

    # 4. Ghi sự kiện vào outbox hoặc phát trực tiếp lên RabbitMQ.
    from common_auth import outbox
    from starlette.concurrency import run_in_threadpool
    from app.core import event_outbox

    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("User-Agent")
    request_id = request.headers.get("X-Request-ID")
    event_args = dict(
        user_id=current.id,
        role=current.role,
        jti=jti,
        exp=exp,
        client_ip=client_ip,
        user_agent=user_agent,
        request_id=request_id,
    )
    queued = outbox.enabled() and await run_in_threadpool(
        event_outbox.enqueue_and_commit,
        db,
        rabbitmq.ROUTING_KEY_USER_LOGGED_OUT,
        rabbitmq.logged_out_payload(**event_args),
        request_id=request_id,
    )
    if not queued:
        await rabbitmq.publish_user_logged_out(**event_args)

    # 5. Xóa sạch HttpOnly cookies trên trình duyệt
    response = ok(request, {"logged_out": True})
    cookies.clear_auth_cookies(response)
    return response
