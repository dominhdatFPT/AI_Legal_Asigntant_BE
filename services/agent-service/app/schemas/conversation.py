# Pydantic schemas cho conversation endpoints (theo openapi.yaml)
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.message import MessageOut


class ConversationCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str | None = Field(default=None, max_length=255)


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class Pagination(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int


class ConversationList(BaseModel):
    items: list[ConversationOut]
    pagination: Pagination
