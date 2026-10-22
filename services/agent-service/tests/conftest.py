import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))


@pytest.fixture(autouse=True)
def settings_env(monkeypatch):
    """Cấu hình giả cho test, không đọc key thật trong .env."""
    from app.core.config import get_settings

    monkeypatch.setenv("RAG_SERVICE_URL", "http://rag-test:8000")
    monkeypatch.setenv("DOCUMENT_SERVICE_URL", "http://document-test:8000")
    monkeypatch.setenv("INTERNAL_TOKEN", "test-internal-token")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def chunk():
    """Một chunk mẫu đúng hợp đồng rag-service (services/rag-service/openapi.yaml)."""
    return {
        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "content": "Điều 188. Điều kiện thực hiện các quyền chuyển đổi, chuyển nhượng...",
        "score": 0.92,
        "section_info": {"chuong": None, "dieu": "188", "khoan": "1", "diem": None},
        "document": {
            "id": "f1e2d3c4-b5a6-7890-abcd-ef1234567890",
            "title": "Luật Đất đai 2024",
            "doc_number": "31/2024/QH15",
            "doc_type": "luat",
        },
    }


@pytest.fixture
def db():
    """Database SQLite tạm trong RAM, tạo bảng từ models (không cần Supabase)."""
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    from app.core.database import Base
    import app.models  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _):
        # SQLite mặc định tắt khóa ngoại; bật để ON DELETE CASCADE chạy giống PostgreSQL
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()
