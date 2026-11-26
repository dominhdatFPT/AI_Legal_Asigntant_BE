from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer

DocType = Literal["luat", "nghi_dinh", "thong_tu", "quyet_dinh", "khac"]
FairnessKey = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]


class SearchOptions(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=20000)
    top_k: int = Field(default=5, ge=1, le=20)
    doc_types: list[DocType] | None = None
    planned: bool = False  # Agent has already separated this issue; do not split again.
    # Trần cứng token embedding online (SAU khi mở rộng truy vấn) cho request này. 0 = chỉ exact/BM25, KHÔNG gọi embedding trả phí.
    # None = dùng EMBEDDING_DEFAULT_TOKEN_BUDGET.
    embedding_token_budget: int | None = Field(default=None, ge=0)
    # Gợi ý có cấu trúc: [{"doc_ref": "BLDS", "article": "328", "clause": "2", "point": "a", "title": "Đặt cọc"}]
    exact_refs: list[dict[str, Any]] | None = None
    expand_adjacent_clauses: bool = True
    follow_citations: bool = True


class SearchRequest(SearchOptions):
    """Body của POST /internal/v1/rag/search."""

    request_turn_id: str | None = Field(default=None, max_length=128)
    fairness_key: FairnessKey | None = None
    priority: Literal["interactive", "batch"] = "interactive"
    deadline_ms: int | None = Field(default=None, ge=100, le=90000)


class BatchSearchItem(SearchOptions):
    item_id: str
    doc_types: list[str] | None = None
    planned: bool = True


class BatchSearchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    items: list[BatchSearchItem] = Field(min_length=1, max_length=12)
    request_turn_id: str | None = Field(default=None, max_length=128)
    fairness_key: FairnessKey | None = None
    priority: Literal["batch"] = "batch"
    deadline_ms: int | None = Field(default=None, ge=100, le=90000)


class SectionInfo(BaseModel):
    chuong: str | None = None
    muc: str | None = None
    dieu: str | None = None
    khoan: str | None = None
    diem: str | None = None


class EmbeddingUsage(BaseModel):
    """Chi phí embedding online của MỘT request search (đã cộng mọi lần gọi provider)."""

    prompt_tokens: int = 0
    cached_tokens: int = 0  # token của truy vấn lấy từ cache tiến trình, không tính tiền
    attempts: int = 0  # số lần gọi provider thực tế (kể cả lần lỗi)
    cost_nano_usd: int = 0
    budget_capped: bool = False  # truy vấn bị cắt / bỏ bớt vì chạm embedding_token_budget
    # budget_zero | exact_sufficient | embedding_failed | budget_exhausted | None
    skipped_reason: str | None = None
    model: str | None = None
    request_turn_id: str | None = None
    usage_uncertain: bool = False

    @model_serializer(mode="wrap")
    def _serialize_usage(self, serialize):
        data = serialize(self)
        if not self.usage_uncertain and self.skipped_reason != "embedding_failed":
            data.pop("usage_uncertain", None)
        return data


class SearchChunk(BaseModel):
    """Một phần tử của data.chunks. Các trường sau `document` là tuỳ chọn, thêm sau nên tương thích ngược."""

    id: str
    content: str
    score: float
    section_info: dict[str, Any] | None = None
    document: dict[str, Any]
    retrieval_sources: list[dict[str, Any]] | None = None
    rank_sources: list[str] | None = None  # exact | bm25 | vector | citation | adjacent
    rrf_rank: int | None = None  # thứ hạng 1-based trong danh sách cuối (sau RRF + giữ chỗ exact/citation/adjacent)
    document_version: str | None = None  # chưa có cột phiên bản trong legal_documents nên hiện luôn null
    effective_date: str | None = None


class SearchResponse(BaseModel):
    """Phần `data` của phản hồi: {"chunks": [...], "embedding_usage": {...}}."""

    chunks: list[SearchChunk]
    embedding_usage: EmbeddingUsage | None = None


class BatchSearchItemResult(BaseModel):
    item_id: str
    status: Literal["ok", "degraded", "overloaded", "error"]
    chunks: list[SearchChunk] = Field(default_factory=list)
    error_code: str | None = None
    embedding_usage: EmbeddingUsage | None = None


class BatchSearchResponse(BaseModel):
    items: list[BatchSearchItemResult]
    embedding_usage: EmbeddingUsage
