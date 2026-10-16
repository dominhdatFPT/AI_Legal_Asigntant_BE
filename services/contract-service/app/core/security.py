from uuid import UUID

from common_auth import CurrentUser
from common_auth.fastapi_dep import get_current_user
from fastapi import Depends, Request
from starlette.concurrency import run_in_threadpool

from app.core.config import allowed_origins
from app.core.errors import ContractError


def contract_user(request: Request, user: CurrentUser = Depends(get_current_user)):
    try:
        owner = str(UUID(user.id))
    except (ValueError, TypeError, AttributeError):
        raise ContractError("UNAUTHORIZED", "Access token không hợp lệ.", 401) from None
    if user.role not in {"citizen", "lawyer", "admin"}:
        raise ContractError("FORBIDDEN", "Bạn không có quyền thực hiện thao tác này.", 403)
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not request.headers.get("Authorization"):
        if request.headers.get("Origin") not in allowed_origins():
            raise ContractError("FORBIDDEN", "Nguồn yêu cầu không được phép.", 403)
    return CurrentUser(id=owner, role=user.role)


def contract_admin(user: CurrentUser = Depends(contract_user)):
    if user.role != "admin":
        raise ContractError("FORBIDDEN", "Chỉ quản trị viên được thực hiện thao tác này.", 403)
    return user


def require_owner(owner_user_id, user):
    # Call after fetching Contract's own row; admins do not bypass private ownership.
    if str(owner_user_id) != user.id:
        raise ContractError("CONTRACT_NOT_FOUND", "Không tìm thấy hợp đồng.", 404)


def get_user_gate():
    from app.services.user_gate import UserGate
    return UserGate.from_env()


async def dossier_creator(request: Request, user: CurrentUser = Depends(contract_user), gate=Depends(get_user_gate)):
    # C5 draft creation only; not wired to reads, edits or admin catalog.
    await run_in_threadpool(gate.ensure_can_create, user.id, request.state.request_id)
    return user


def get_admin_gate():
    from app.services.admin_gate import AdminGate
    return AdminGate.from_env()


async def catalog_user(request: Request, user: CurrentUser = Depends(contract_user)):
    # Also verify admin reads: stale admin claims must not disclose draft content.
    if user.role == "admin":
        from common_auth.core import ACCESS_COOKIE, extract_token
        token = extract_token(request.headers.get("Authorization"), request.cookies.get(ACCESS_COOKIE))
        await run_in_threadpool(get_admin_gate().ensure_admin, user.id, token, request.state.request_id)
    return user


def catalog_admin(user: CurrentUser = Depends(catalog_user)):
    return contract_admin(user)
