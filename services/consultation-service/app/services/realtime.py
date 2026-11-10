"""Quản lý WebSocket trong bộ nhớ: mỗi "phòng" là một tập socket. Một user mở nhiều tab thì có nhiều socket
trong cùng phòng `me:<user_id>`."""

import asyncio
import logging
import uuid

from fastapi.encoders import jsonable_encoder

logger = logging.getLogger(__name__)

LOBBY = "lobby"


def me(user_id: uuid.UUID) -> str:
    return f"me:{user_id}"


def chat(consultation_id: uuid.UUID) -> str:
    return f"chat:{consultation_id}"


# shortcut: phòng chỉ nằm trong RAM của một process, chạy 2 instance thì sự kiện chỉ tới socket cùng instance;
# khi cần chạy nhiều instance thì đưa sự kiện qua Redis pub/sub hoặc RabbitMQ.
class Manager:
    def __init__(self) -> None:
        self.rooms: dict[str, set] = {}

    def join(self, room: str, websocket) -> None:
        self.rooms.setdefault(room, set()).add(websocket)

    def leave(self, room: str, websocket) -> None:
        sockets = self.rooms.get(room)
        if sockets is not None:
            sockets.discard(websocket)
            if not sockets:
                del self.rooms[room]

    async def _send(self, room: str, websocket, message: dict) -> None:
        try:
            await websocket.send_json(message)
        except Exception:  # socket chết: bỏ khỏi phòng, không ảnh hưởng socket khác
            logger.info("Bỏ socket lỗi khỏi phòng %s", room)
            self.leave(room, websocket)

    async def send(self, room: str, message: dict) -> None:
        # Duyệt bản sao vì _send có thể xóa socket khỏi tập trong lúc gửi.
        await asyncio.gather(*(self._send(room, ws, message) for ws in list(self.rooms.get(room, ()))))


manager = Manager()


async def publish(event_type: str, data, *rooms: str) -> None:
    """Gửi sự kiện tới các phòng. Chạy bằng BackgroundTasks, tức là sau khi đã commit."""
    message = jsonable_encoder({"type": event_type, "data": data})
    await asyncio.gather(*(manager.send(room, message) for room in dict.fromkeys(rooms)))
