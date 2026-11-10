from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # JWT
    jwt_public_key_path: str = "keys/jwt_public.pem"

    # Internal token (agent-service → rag-service), phải khớp INTERNAL_TOKEN bên agent-service
    internal_token: str = ""

    # Database (Supabase PostgreSQL + pgvector)
    rag_database_url: str = ""
    # Chống treo khi kết nối DB chết ngầm (Supabase pooler): không có giới hạn thì truy vấn trên kết nối chết chờ mãi.
    # Giá trị <= 0 = bỏ giới hạn (hành vi cũ). Xem app/core/database.py
    # Chờ tối đa khi mở kết nối mới (giây)
    db_connect_timeout_seconds: int = 10
    # Chờ tối đa cho MỘT truy vấn (giây, đặt statement_timeout phía server); quá giờ thì truy vấn báo lỗi -> 503
    db_command_timeout_seconds: int = 20
    # Kết nối trong pool quá tuổi này (giây) thì bỏ và tạo lại
    db_pool_recycle_seconds: int = 300

    # Embedding
    # "gemini": gọi Gemini API (cần GEMINI_API_KEY, gói free đủ dùng khi phát triển)
    # "fake":   vector băm từ khóa, không gọi mạng — chỉ để test / chạy thử offline
    embedding_provider: str = "gemini"
    gemini_api_key: str = ""
    embedding_model: str = "gemini-embedding-001"
    # Phải khớp vector(768) trong database/001_rag_schema.sql
    embedding_dim: int = 768
    # Số đoạn gửi trong 1 request embedding
    embedding_batch_size: int = 20
    # Tự giảm tốc khi nạp văn bản để không vượt giới hạn token/phút của gói free (0 = tắt)
    embedding_tokens_per_minute: int = 25000

    # Thêm thuật ngữ luật vào câu hỏi trước khi tìm ("sổ đỏ" -> "Giấy chứng nhận quyền sử dụng đất"),
    # xem app/services/query_expansion.py. Tắt (false) để so sánh khi chạy scripts/eval_retrieval.py
    query_expansion_enabled: bool = True
    # Câu hỏi nhiều ý ("tách thửa rồi tặng cho con, thời hạn sử dụng đất thế nào") thì tìm thêm theo từng ý,
    # xem app/services/query_split.py. Tắt (false) để so sánh khi chạy scripts/eval_retrieval.py --multi
    query_split_enabled: bool = True
    # Mở sẵn kết nối DB và gọi thử embedding khi service khởi động, để câu hỏi đầu tiên không bị chậm
    warmup_on_startup: bool = True

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
