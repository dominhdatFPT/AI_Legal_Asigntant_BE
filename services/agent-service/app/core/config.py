from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Downstream services
    rag_service_url: str = "http://rag-service:8000"
    internal_token: str = ""

    # JWT
    jwt_public_key_path: str = "keys/jwt_public.pem"

    # Database
    agent_database_url: str = ""

    # LLM provider: "gemini" hoặc "groq"
    llm_provider: str = "gemini"

    # Gemini
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    gemini_timeout_seconds: float = 30.0
    gemini_max_retries: int = 2

    # Groq
    groq_api_key: str = ""
    groq_model: str = "qwen/qwen3.8-27b"
    groq_timeout_seconds: float = 30.0
    groq_max_retries: int = 2

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
