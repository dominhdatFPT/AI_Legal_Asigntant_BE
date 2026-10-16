from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DocType = Literal["luat", "nghi_dinh", "thong_tu", "quyet_dinh", "khac"]


class SearchRequest(BaseModel):
    """Body của POST /internal/v1/rag/search (xem openapi.yaml → SearchRequest)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=20)
    doc_types: list[DocType] | None = None


class SectionInfo(BaseModel):
    chuong: str | None = None
    dieu: str | None = None
    khoan: str | None = None
    diem: str | None = None


class DocumentBrief(BaseModel):
    id: str
    title: str
    doc_number: str | None
    doc_type: DocType


class ChunkResult(BaseModel):
    id: str
    content: str
    score: float  # độ tương đồng cosine 0–1, càng cao càng liên quan
    section_info: SectionInfo | None
    document: DocumentBrief
