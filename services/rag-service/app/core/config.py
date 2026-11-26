from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # JWT
    jwt_public_key_path: str = "keys/jwt_public.pem"

    # Internal token (agent-service → rag-service), phải khớp INTERNAL_TOKEN bên agent-service
    internal_token: str = ""

    # Database (Supabase PostgreSQL + pgvector)
    rag_database_url: str = ""
    rag_db_concurrency_limit: int = Field(default=4, ge=1, le=4)
    rag_db_queue_max_waiting: int = Field(default=96, ge=0)
    rag_db_queue_wait_seconds: float = Field(default=25.0, gt=0)
    rag_db_interactive_burst: int = Field(default=3, ge=1)
    rag_db_per_tenant_burst: int = Field(default=3, ge=1, le=4)
    # Chống treo khi kết nối DB chết ngầm (Supabase pooler): không có giới hạn thì truy vấn trên kết nối chết chờ mãi.
    # Giá trị <= 0 = bỏ giới hạn (hành vi cũ). Xem app/core/database.py
    # Chờ tối đa khi mở kết nối mới (giây)
    db_connect_timeout_seconds: int = 10
    # Chờ tối đa cho MỘT truy vấn (giây, đặt statement_timeout phía server); quá giờ thì truy vấn báo lỗi -> 503
    db_command_timeout_seconds: int = 20
    # Kết nối trong pool quá tuổi này (giây) thì bỏ và tạo lại
    db_pool_recycle_seconds: int = 300

    # Địa chỉ Redis dùng làm cache. Rỗng = không dùng Redis (cache chạy bằng bộ nhớ process như cũ)
    redis_url: str = ""
    # Thời gian chờ tối đa (giây) cho mỗi lần kết nối/gọi Redis khi tăng số phiên bản kho luật (app/services/corpus_version.py)
    redis_timeout_seconds: float = 0.2
    # Tên khoá Redis giữ số phiên bản kho luật. PHẢI giống CORPUS_VERSION_KEY của agent-service (agent đọc số này để khoá cache)
    corpus_version_key: str = "legal:corpus_version"

    # Embedding
    # "gemini":     gọi Gemini API (cần GEMINI_API_KEY)
    # "openrouter": gọi OpenRouter Embeddings API (cần OPENROUTER_API_KEY)
    # "fake":       vector băm từ khóa, không gọi mạng — chỉ để test / chạy thử offline
    embedding_provider: str = "gemini"
    gemini_api_key: str = ""
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_embedding_model: str = "google/gemini-embedding-001"
    embedding_model: str = "gemini-embedding-001"
    # Phải khớp vector(768) trong database/001_rag_schema.sql
    embedding_dim: int = 768
    # Số đoạn gửi trong 1 request embedding
    embedding_batch_size: int = 20
    # Tự giảm tốc khi nạp văn bản để không vượt giới hạn token/phút của gói free (0 = tắt)
    embedding_tokens_per_minute: int = 25000
    # Embedding CÂU HỎI (online) tính tiền theo từng lần gọi nên KHÔNG thử lại (0). Tách khỏi retry khi nạp văn bản
    # (embed_documents, nạp nền, được phép chờ lâu).
    embedding_online_max_retries: int = 0
    # Đơn giá embedding: nano-USD / token (150 = $0.15 / 1M token), dùng để báo cost_nano_usd trong embedding_usage
    embedding_price_nano_per_token: int = 150
    # Trần token embedding online cho 1 request khi agent-service không gửi embedding_token_budget (tính SAU khi mở rộng truy vấn)
    embedding_default_token_budget: int = 1024

    # Thêm thuật ngữ luật vào câu hỏi trước khi tìm ("sổ đỏ" -> "Giấy chứng nhận quyền sử dụng đất"),
    # xem app/services/query_expansion.py. Tắt (false) để so sánh khi chạy scripts/eval_retrieval.py
    query_expansion_enabled: bool = True
    # Câu hỏi nhiều ý ("tách thửa rồi tặng cho con, thời hạn sử dụng đất thế nào") thì tìm thêm theo từng ý,
    # xem app/services/query_split.py. Tắt (false) để so sánh khi chạy scripts/eval_retrieval.py --multi
    query_split_enabled: bool = True
    # Independent corpus BM25 + vector + exact metadata lookup, fused by RRF.
    # HYBRID_ENABLED=false restores vector-only retrieval.
    hybrid_enabled: bool = True
    # Legacy rerank helper options for offline comparisons; RRF does not use them.
    hybrid_alpha: float = 0.05
    # Legacy rerank candidate pool (0 = all).
    hybrid_pool: int = 0
    # Mở sẵn kết nối DB và gọi thử embedding khi service khởi động, để câu hỏi đầu tiên không bị chậm
    warmup_on_startup: bool = True

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
