# Pydantic schemas cho endpoint kịch bản hội thoại (theo openapi.yaml)
import uuid
from datetime import datetime

from pydantic import BaseModel


class TranscriptMessage(BaseModel):
    content: str
    created_at: datetime


class TranscriptTurn(BaseModel):
    turn: int  # số thứ tự lượt, từ 1
    # None chỉ khi dữ liệu lệch cặp (tin trợ lý không có tin người dùng đứng trước); luồng lưu thông thường không tạo ra
    user: TranscriptMessage | None
    assistant: TranscriptMessage | None  # None khi tin người dùng cuối chưa có câu trả lời


class Transcript(BaseModel):
    conversation_id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime
    total_messages: int  # tổng số tin của hội thoại, có thể lớn hơn số tin nằm trong `turns` khi truncated
    truncated: bool
    turns: list[TranscriptTurn]
