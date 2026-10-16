# Pydantic schemas cho message endpoints (theo openapi.yaml)
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SendMessageRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    content: str = Field(min_length=1, max_length=2000)


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
