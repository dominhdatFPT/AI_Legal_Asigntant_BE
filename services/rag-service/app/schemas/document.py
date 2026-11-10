# Pydantic request/response schemas cho document endpoints
import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.search import DocType

DocStatus = Literal["active", "superseded", "draft"]
IngestStatus = Literal["pending", "processing", "done", "failed"]
IngestAction = Literal[
    "create_draft", "delete_draft", "approve_requested", "approve_done", "approve_failed",
    "replace_requested", "replace_done", "replace_failed",
]

# Tối đa ký tự của `content` khi xem trước (toàn văn một bộ luật lớn cỡ vài trăm nghìn ký tự)
PREVIEW_MAX_CONTENT_CHARS = 3_000_000


class DocumentListItem(BaseModel):
    """Một dòng trong danh sách văn bản. KHÔNG có `content` (toàn văn rất nặng)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    doc_type: DocType
    doc_number: str | None
    issuing_body: str | None
    effective_date: date | None
    status: DocStatus
    chunk_count: int
    is_processed: bool
    ingest_status: IngestStatus
    created_at: datetime
    updated_at: datetime


class DocumentDetail(DocumentListItem):
    """Chi tiết một văn bản. Vẫn KHÔNG có `content`; `content_length` là số ký tự của toàn văn."""

    issued_date: date | None
    source_url: str | None
    ingest_error: str | None
    content_length: int


class DocumentList(BaseModel):
    items: list[DocumentListItem]
    total: int
    page: int
    page_size: int


class DocumentPreviewRequest(BaseModel):
    """Body của POST /api/v1/rag/documents/preview (JSON, không upload file)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=500)
    doc_type: DocType
    doc_number: str | None = Field(default=None, max_length=100)
    content: str = Field(max_length=PREVIEW_MAX_CONTENT_CHARS)

    @field_validator("doc_number")
    @classmethod
    def _blank_doc_number_is_none(cls, v: str | None) -> str | None:
        return v or None


class DocumentCreateRequest(BaseModel):
    """Body của POST /api/v1/rag/documents: lưu bản nháp (chưa cắt chunk, chưa embedding)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=500)
    doc_type: DocType
    doc_number: str | None = Field(default=None, max_length=100)
    issuing_body: str | None = Field(default=None, max_length=255)
    issued_date: date | None = None
    effective_date: date | None = None
    source_url: str | None = Field(default=None, max_length=2000)
    content: str = Field(max_length=PREVIEW_MAX_CONTENT_CHARS)

    @field_validator("doc_number")
    @classmethod
    def _blank_doc_number_is_none(cls, v: str | None) -> str | None:
        return v or None

    @field_validator("title", "doc_number", "issuing_body", "source_url", "content")
    @classmethod
    def _no_nul(cls, v: str | None) -> str | None:
        # PostgreSQL không lưu được ký tự NUL (mã 0) trong text
        if v is not None and "\x00" in v:
            raise ValueError("Không được chứa ký tự NUL (mã 0).")
        return v


class DocumentReplaceRequest(BaseModel):
    """Body của POST /{id}/replace/preview và POST /{id}/replace: chỉ có toàn văn mới (cùng giới hạn như tạo nháp)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    content: str = Field(max_length=PREVIEW_MAX_CONTENT_CHARS)

    @field_validator("content")
    @classmethod
    def _no_nul(cls, v: str) -> str:
        # Cùng kiểm tra với DocumentCreateRequest._no_nul (PostgreSQL không lưu được ký tự NUL)
        if "\x00" in v:
            raise ValueError("Không được chứa ký tự NUL (mã 0).")
        return v


class IngestLogItem(BaseModel):
    """Một dòng nhật ký nạp (bảng ingest_log). Không có nội dung văn bản."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    actor_user_id: str | None
    actor_role: str | None
    action: IngestAction
    document_id: uuid.UUID | None
    doc_number: str | None
    title: str | None
    detail: str | None
    chunk_count: int | None
    duration_ms: int | None


class IngestLogList(BaseModel):
    items: list[IngestLogItem]
    total: int
    page: int
    page_size: int


# TODO (các việc sau):
# class DocumentUpdate(BaseModel): title?, status?, ...  (PATCH metadata)
# reprocess: chưa có schema
