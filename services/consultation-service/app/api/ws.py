"""WebSocket chỉ để nhận sự kiện (API_GUIDELINES mục 10). Kênh lấy từ URL; sau khi xác thực mọi tin client gửi đều bị bỏ qua."""

import asyncio
import uuid

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from common_auth.core import ACCESS_COOKIE, AuthError, decode_access_token

from app.api.deps import Caller
from app.core.config import allowed_origins
from app.core.database import get_db
from app.core.responses import CONSULTATION_NOT_FOUND, FORBIDDEN, MESSAGES, UNAUTHORIZED, ApiError
from app.models.consultation import utcnow
from app.repositories import consultation_repository as repo
from app.services import consultation_service as service
from app.services import realtime

router = APIRouter(prefix="/ws/v1/consultations")

AUTH_TIMEOUT = 5.0  # giây chờ tin xác thực đầu tiên
UNAUTHENTICATED, NO_PERMISSION, NOT_FOUND, TRY_LATER = 4401, 4403, 4404, 1013


async def _reject(websocket: WebSocket, close_code: int, code: str, message: str | None = None) -> None:
    await websocket.send_json({"type": "error", "data": {"code": code, "message": message or MESSAGES.get(code, code)}})
    await websocket.close(code=close_code)


async def _authenticate(websocket: WebSocket) -> tuple[Caller, str]:
    """Trình duyệt dùng cookie HttpOnly. Client khác gửi tin đầu {"type": "auth", "data": {"token": ...}}."""
    try:
        token = websocket.cookies.get(ACCESS_COOKIE)
        if token:
            # Trình duyệt tự gửi cookie, nên phải chặn trang lạ mở socket bằng phiên của user.
            if websocket.headers.get("origin") not in allowed_origins():
                raise ApiError(403, FORBIDDEN)
        else:
            first = await asyncio.wait_for(websocket.receive_json(), AUTH_TIMEOUT)
            token = first["data"]["token"] if first["type"] == "auth" else None
        user = decode_access_token(token)
        return Caller(uuid.UUID(user.id), user.role), token
    except WebSocketDisconnect:
        raise
    except (asyncio.TimeoutError, AuthError, KeyError, TypeError, ValueError, AttributeError):
        raise ApiError(401, UNAUTHORIZED) from None


def _chat_room(db: Session, caller: Caller, channel: str) -> str:
    try:
        consultation_id = uuid.UUID(channel)
    except ValueError:
        raise ApiError(404, CONSULTATION_NOT_FOUND) from None
    try:
        c = repo.get_visible(db, consultation_id, utcnow())
        if c is None:
            raise ApiError(404, CONSULTATION_NOT_FOUND)
        if caller.id not in (c.citizen_user_id, c.lawyer_user_id):
            raise ApiError(403, FORBIDDEN)
        return realtime.chat(c.id)
    finally:
        # Rollback sau cùng: nó làm hết hạn object, đọc lại `c` sẽ mượn kết nối DB suốt đời socket.
        db.rollback()


async def _room_for(db: Session, caller: Caller, token: str, channel: str) -> str:
    if channel == "lobby":
        await run_in_threadpool(service.require_practicing_lawyer, caller, token)
        return realtime.LOBBY
    if channel == "me":
        return realtime.me(caller.id)
    return await run_in_threadpool(_chat_room, db, caller, channel)


@router.websocket("/{channel}")
async def consultation_socket(websocket: WebSocket, channel: str, db: Session = Depends(get_db)):
    await websocket.accept()
    try:
        caller, token = await _authenticate(websocket)
        room = await _room_for(db, caller, token, channel)
    except WebSocketDisconnect:
        return
    except ApiError as e:
        close_code = {401: UNAUTHENTICATED, 403: NO_PERMISSION, 404: NOT_FOUND}.get(e.status, TRY_LATER)
        await _reject(websocket, close_code, e.code, e.message)
        return

    realtime.manager.join(room, websocket)
    try:
        while True:
            await websocket.receive_text()  # bỏ qua mọi tin; chỉ để phát hiện client ngắt kết nối
    except WebSocketDisconnect:
        pass
    finally:
        realtime.manager.leave(room, websocket)
