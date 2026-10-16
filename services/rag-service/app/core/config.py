from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # JWT
    jwt_public_key_path: str = "keys/jwt_public.pem"

    # Internal token (agent-service → rag-service), phải khớp INTERNAL_TOKEN bên agent-service
    internal_token: str = ""

    # Database (Supabase PostgreSQL + pgvector)
    rag_database_url: str = ""

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

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
