import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))


@pytest.fixture(autouse=True)
def settings_env(monkeypatch):
    """Cấu hình giả cho test: không đọc .env thật, không gọi Gemini."""
    from app.core.config import get_settings
    from app.services.embedding import get_embedder

    monkeypatch.setenv("INTERNAL_TOKEN", "test-internal-token")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("EMBEDDING_DIM", "768")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    get_settings.cache_clear()
    get_embedder.cache_clear()
    yield
    get_settings.cache_clear()
    get_embedder.cache_clear()


@pytest.fixture
def sample_text():
    return (FIXTURES / "van_ban_mau.txt").read_text(encoding="utf-8")


@pytest.fixture
def db():
    """Database SQLite tạm trong RAM (không có pgvector: cột embedding lưu dạng JSON)."""
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    from app.core.database import Base
    import app.models  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()
