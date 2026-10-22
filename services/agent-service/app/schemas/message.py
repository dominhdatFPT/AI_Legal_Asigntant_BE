# Pydantic schemas cho message endpoints (theo openapi.yaml)
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_DOCUMENTS = 3


class SendMessageRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    content: str = Field(min_length=1, max_length=2000)
    # Tài liệu đã upload ở document-service; document-service tự kiểm tra người dùng có phải chủ file
    document_ids: list[uuid.UUID] | None = Field(default=None, max_length=MAX_DOCUMENTS)

    @field_validator("document_ids")
    @classmethod
    def no_duplicates(cls, ids):
        if ids and len(set(ids)) != len(ids):
            raise ValueError("document_ids không được trùng nhau")
        return ids or None


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    # Model ORM đặt tên thuộc tính là `meta` (xem app/models/message.py)
    metadata: dict | None = Field(default=None, validation_alias="meta")
    created_at: datetime


class SendMessageResponse(BaseModel):
    user_message: MessageOut
    assistant_message: MessageOut
