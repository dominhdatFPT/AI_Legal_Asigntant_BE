"""Validated backend-only configuration. Never include secrets in repr/errors."""
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo


class ConfigurationError(Exception):
    pass


def require_session_database(settings):
    """Session advisory locks must not use Supabase transaction pooler (6543)."""
    info = conninfo_to_dict(settings.database_url)
    if str(info.get('port', '5432')) != '5432':
        raise ConfigurationError('Document worker cần Direct/Session pooler port 5432.')


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    supabase_url: str
    storage_key: str = field(repr=False)
    storage_bucket: str = "document-files"
    timeout_seconds: float = 20

    @classmethod
    def from_env(cls):
        base = os.getenv("DOCUMENT_SUPABASE_URL", "").rstrip("/")
        try:
            parsed = urlsplit(base)
        except ValueError:
            raise ConfigurationError("DOCUMENT_SUPABASE_URL không hợp lệ.") from None
        match = re.fullmatch(r"([a-z0-9]{20})\.supabase\.co", parsed.netloc)
        if parsed.scheme != "https" or not match or parsed.path or parsed.query or parsed.fragment:
            raise ConfigurationError("DOCUMENT_SUPABASE_URL phải là HTTPS project Supabase hợp lệ.")
        project_ref = match.group(1)
        key = os.getenv("DOCUMENT_SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not key or any(ch.isspace() for ch in key) or key.startswith("sb_publishable_"):
            raise ConfigurationError("Thiếu hoặc sai backend key DOCUMENT_SUPABASE_SERVICE_ROLE_KEY.")
        bucket = os.getenv("DOCUMENT_STORAGE_BUCKET", "document-files")
        if bucket != "document-files":
            raise ConfigurationError("DOCUMENT_STORAGE_BUCKET phải là document-files.")
        value = os.getenv("DOCUMENT_DATABASE_URL", "").replace("postgresql+psycopg://", "postgresql://", 1)
        try:
            info = conninfo_to_dict(value)
        except psycopg.Error:
            raise ConfigurationError("DOCUMENT_DATABASE_URL không hợp lệ.") from None
        host, user = info.get("host", ""), info.get("user", "")
        direct = host == f"db.{project_ref}.supabase.co"
        pooled = bool(re.fullmatch(r"[a-z0-9-]+\.pooler\.supabase\.com", host)) and user == f"postgres.{project_ref}"
        if not value or not (direct or pooled) or info.get("hostaddr"):
            raise ConfigurationError("Database và Storage phải thuộc cùng project Document.")
        sslmode = info.get("sslmode", "require")
        if sslmode not in {"require", "verify-ca", "verify-full"}:
            raise ConfigurationError("Database phải dùng TLS require/verify-ca/verify-full.")
        override = os.getenv("DOCUMENT_DATABASE_POOLER_HOST", "").strip()
        updates = {"sslmode": sslmode}
        if override:
            if not re.fullmatch(r"[a-z0-9-]+\.pooler\.supabase\.com", override):
                raise ConfigurationError("DOCUMENT_DATABASE_POOLER_HOST không hợp lệ.")
            updates.update(host=override, port="5432", user=f"postgres.{project_ref}")
        try:
            timeout = float(os.getenv("DOCUMENT_HTTP_TIMEOUT_SECONDS", "20"))
            if not 0 < timeout <= 60:
                raise ValueError
        except ValueError:
            raise ConfigurationError("DOCUMENT_HTTP_TIMEOUT_SECONDS phải lớn hơn 0 và tối đa 60.") from None
        settings = cls(make_conninfo(value, **updates), base, key, bucket, timeout)
        # Upload/profile also use session advisory locks, not just workers.
        require_session_database(settings)
        return settings

    @staticmethod
    def allowed_origins():
        raw = os.getenv("DOCUMENT_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
        origins = set()
        for item in raw.split(","):
            item = item.strip().rstrip("/")
            try:
                parsed = urlsplit(item)
            except ValueError:
                raise ConfigurationError("DOCUMENT_ALLOWED_ORIGINS không hợp lệ.") from None
            if (parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username
                    or parsed.password or parsed.path or parsed.query or parsed.fragment):
                raise ConfigurationError("DOCUMENT_ALLOWED_ORIGINS phải chứa origin cụ thể, không dùng wildcard.")
            origins.add(item)
        return origins
