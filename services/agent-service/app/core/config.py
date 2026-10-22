from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Downstream services
    rag_service_url: str = "http://rag-service:8000"
    internal_token: str = ""
    # Lấy chữ đã trích của tài liệu người dùng upload (gọi bằng token của chính người dùng)
    document_service_url: str = "http://document-service:8000"

    # JWT
    jwt_public_key_path: str = "keys/jwt_public.pem"

    # Database
    agent_database_url: str = ""

    # LLM provider: "gemini" hoặc "groq"
    llm_provider: str = "gemini"

    # Gemini
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    gemini_timeout_seconds: float = 15.0
    gemini_max_retries: int = 1

    # Groq
    groq_api_key: str = ""
    groq_model: str = "qwen/qwen3.8-27b"
    groq_timeout_seconds: float = 15.0
    groq_max_retries: int = 1

    # Câu trả lời mong muốn ~150-200 chữ; giới hạn này là chốt chặn khi model vẫn muốn viết dài
    llm_max_output_tokens: int = 800
    # Số đoạn luật lấy từ rag-service đưa vào prompt
    rag_top_k: int = 4

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
