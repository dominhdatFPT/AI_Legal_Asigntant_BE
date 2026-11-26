import os
from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator


class Settings(BaseSettings):
    _root = Path(__file__).resolve().parents[2]
    # .parent dừng ở "/" thay vì IndexError (container: _root=/app); file không tồn tại sẽ bị bỏ qua
    model_config = SettingsConfigDict(
        env_file=None if os.getenv("PROCEDURE_SKIP_DOTENV") == "1" else (
            _root.parent.parent.parent / ".env", _root.parent.parent / ".keys" / "onlyoffice.env",
            _root.parent.parent / ".env", _root / ".env",
        ),
        env_file_encoding="utf-8", extra="ignore",
    )

    ENVIRONMENT: str = Field(default="development", description="development | test | production")
    ALLOW_DEV_AUTH: bool = Field(default=False, description="Bật cờ cho phép header X-User-Id khi test local")
    PROCEDURE_SERVICE_PORT: int = Field(default=8000)
    PROCEDURE_DATABASE_URL: str = Field(default="")
    SUPABASE_URL: str = Field(default="")
    SUPABASE_ANON_KEY: str = Field(default="")
    JWT_PUBLIC_KEY: str | None = None
    JWT_PUBLIC_KEY_PATH: str | None = None
    CORS_ORIGINS: str | List[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8000",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ]

    @field_validator("CORS_ORIGINS", mode="after")
    @classmethod
    def assemble_cors_origins(cls, v: str | List[str]) -> List[str]:
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                import json
                try:
                    return json.loads(v)
                except Exception:
                    pass
            return [i.strip() for i in v.split(",") if i.strip()]
        return v


settings = Settings()
