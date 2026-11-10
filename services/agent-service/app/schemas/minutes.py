# Pydantic schemas cho API nội bộ biên bản hội thoại user - luật sư (theo openapi.yaml)
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

MAX_MESSAGES = 60
MAX_CONTENT_CHARS = 5000  # khớp CHECK của consultation_messages.content


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Thời điểm phải có múi giờ, ví dụ 2026-11-08T09:00:00Z")
    return value


class MinutesMessage(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    role: Literal["CITIZEN", "LAWYER"]
    time: datetime
    content: str = Field(min_length=1, max_length=MAX_CONTENT_CHARS)  # đã được consultation-service che PII

    _time = field_validator("time")(_aware)


class PreviousSummary(BaseModel):
    overview: str | None = None
    legal_domains: list[str] = Field(default_factory=list, max_length=8)


class MinutesRequest(BaseModel):
    consultation_id: uuid.UUID
    window_start: datetime | None = None
    window_end: datetime
    previous_summary: PreviousSummary | None = None
    messages: list[MinutesMessage] = Field(min_length=1, max_length=MAX_MESSAGES)

    _end = field_validator("window_end")(_aware)

    @field_validator("window_start")
    @classmethod
    def start_aware(cls, value: datetime | None) -> datetime | None:
        return value if value is None else _aware(value)

    @field_validator("messages")
    @classmethod
    def unique_ids(cls, value: list[MinutesMessage]) -> list[MinutesMessage]:
        if len({m.id for m in value}) != len(value):
            raise ValueError("id tin nhắn bị trùng")
        return value


class MinutesOut(BaseModel):
    summary: dict
    prompt_version: str
