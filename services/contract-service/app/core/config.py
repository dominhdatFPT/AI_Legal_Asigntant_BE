"""Lazy backend configuration. No automatic connection or migrations."""
import os
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

from app.core.errors import ContractError


def configuration_error():
    return ContractError("CONFIGURATION_ERROR", "Cấu hình Contract chưa sẵn sàng.")


def service_base(value, internal_host):
    try:
        parsed = urlsplit(value)
        if (not value or any(ch.isspace() for ch in value) or not parsed.hostname
            or parsed.username or parsed.password or parsed.path.strip("/") or parsed.query or parsed.fragment
            or parsed.scheme not in {"http", "https"}
            or parsed.scheme == "http" and parsed.hostname not in {internal_host, "localhost", "127.0.0.1"}):
            raise ValueError
        parsed.port
        return value.rstrip("/")
    except (ValueError, TypeError):
        raise configuration_error() from None


def allowed_origins():
    result = set()
    for item in os.getenv("CONTRACT_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(","):
        item = item.strip().rstrip("/")
        try:
            parsed = urlsplit(item)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.path
                or parsed.username or parsed.password or parsed.query or parsed.fragment or "*" in item):
                raise ValueError
            parsed.port
        except (ValueError, TypeError):
            raise configuration_error() from None
        result.add(item)
    return result


@dataclass(frozen=True)
class IntegrationSettings:
    user_url: str
    document_url: str
    procedure_url: str
    internal_token: str = field(repr=False)

    @classmethod
    def from_env(cls):
        token = os.getenv("INTERNAL_TOKEN", "")
        if not token or any(ch.isspace() or ord(ch) < 32 or ord(ch) > 126 for ch in token):
            raise configuration_error()
        return cls(service_base(os.getenv("USER_SERVICE_URL", ""), "user-service"),
            service_base(os.getenv("DOCUMENT_SERVICE_URL", ""), "document-service"),
            service_base(os.getenv("PROCEDURE_SERVICE_URL", ""), "procedure-service"), token)


@dataclass(frozen=True)
class DatabaseSettings:
    database_url: str = field(repr=False)

    @classmethod
    def from_env(cls, *, default_tls=False):
        # No fallback to USER/DOCUMENT_DATABASE_URL, no connection/migration here.
        value = os.getenv("CONTRACT_DATABASE_URL", "")
        try:
            parsed = urlsplit(value)
            options = parse_qs(parsed.query, keep_blank_values=True)
            if default_tls and parsed.hostname not in {'localhost', '127.0.0.1'} and 'sslmode' not in options:
                value += ('&' if parsed.query else '?') + 'sslmode=require'
                options['sslmode'] = ['require']
            if (parsed.scheme not in {"postgresql", "postgresql+psycopg"} or not parsed.hostname
                or not parsed.username or not parsed.path.strip("/") or parsed.fragment
                or any(ch.isspace() for ch in value) or set(options) - {"sslmode"}):
                raise ValueError
            parsed.port
            if parsed.hostname not in {"localhost", "127.0.0.1"} and options.get("sslmode") not in [["require"], ["verify-ca"], ["verify-full"]]:
                raise ValueError
        except (ValueError, TypeError):
            raise configuration_error() from None
        return cls(value)
