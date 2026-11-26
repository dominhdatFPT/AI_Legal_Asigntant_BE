import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))


@pytest.fixture(autouse=True)
def settings_env(monkeypatch):
    """Cấu hình giả cho test, không đọc key thật trong .env."""
    from app.core.config import Settings, get_settings

    # Không nạp .env của máy dev: giá trị trong đó (timeout, key, LLM_BYPASS...) làm kết quả test khác nhau giữa các máy
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("RAG_SERVICE_URL", "http://rag-test:8000")
    monkeypatch.setenv("DOCUMENT_SERVICE_URL", "http://document-test:8000")
    monkeypatch.setenv("INTERNAL_TOKEN", "test-internal-token")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    # Existing regression fixtures exercise the legacy rollout profile.
    # test_adaptive_rag explicitly enables the production-default adaptive profile.
    monkeypatch.setenv("ADAPTIVE_RAG_ENABLED", "false")
    monkeypatch.setenv("OPENROUTER_AUX_MODEL", "")  # test chung dùng fallback; test model phụ tự đặt rõ
    # Test thử lại / timeout tính theo 15 giây và 1 lần thử lại, không theo mặc định thật trong config.py
    monkeypatch.setenv("GEMINI_TIMEOUT_SECONDS", "15")
    monkeypatch.setenv("GEMINI_MAX_RETRIES", "1")
    # Hai cache (tìm luật, lịch sử) BẬT mặc định trong code; test chung chạy với cache tắt cho độc lập nhau, test cache tự bật rõ ràng
    # Trần $0.01 mặc định BẬT trong code (10000 micro-USD); các test cũ chạy với provider giả nên tắt trần, test cost gate tự bật rõ ràng
    monkeypatch.setenv("CASE_COST_BUDGET_MICRO_USD", "0")
    monkeypatch.setenv("MINUTES_COST_BUDGET_MICRO_USD", "0")
    # Mặc định production mới (chấm liên quan + dedup bật, cap 3500...) không áp cho ~1.100 test cũ: chúng chạy profile cũ
    monkeypatch.setenv("RELEVANCE_CHECK", "false")
    monkeypatch.setenv("CASE_RELEVANCE_DEDUP_ENABLED", "false")
    monkeypatch.setenv("DAILY_TOKEN_QUOTA_ENABLED", "false")
    monkeypatch.setenv("RAG_CACHE_ENABLED", "false")
    monkeypatch.setenv("HISTORY_CACHE_ENABLED", "false")  # cache lịch sử cũng bật mặc định; test cache lịch sử tự bật rõ ràng
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def reset_rag_breaker():
    """Circuit breaker của rag_client giữ trạng thái ở cấp module: mỗi test bắt đầu với breaker đóng."""
    from app.services import rag_client

    rag_client._fails, rag_client._open_until = 0, 0.0
    yield
    rag_client._fails, rag_client._open_until = 0, 0.0


@pytest.fixture(autouse=True)
def reset_cache_state():
    """Trạng thái cache giữ ở cấp module (số phiên bản kho luật, ngắt mạch, cache lịch sử): mỗi test bắt đầu và kết thúc sạch."""
    from common_auth import revocation
    from app.api.v1 import conversations
    from app.services import cache, history_cache, quota

    conversations.reset_idempotency()
    cache.reset_corpus_version()
    cache.reset_breakers()
    history_cache.reset()
    quota.reset_local_state()
    revocation.clear()
    yield
    conversations.reset_idempotency()
    cache.reset_corpus_version()
    cache.reset_breakers()
    history_cache.reset()
    quota.reset_local_state()
    revocation.clear()


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
