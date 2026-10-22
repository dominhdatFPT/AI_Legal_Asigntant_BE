import os
import logging
import threading
import time
from collections.abc import Iterator
from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

logger = logging.getLogger(__name__)

# Tải cấu hình: ưu tiên .env nội bộ của procedure-service, sau đó mới bổ sung từ BE/.env
_service_env = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
_be_env = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.env"))
_root_env = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.env"))

if os.path.exists(_service_env):
    load_dotenv(_service_env, override=True)
if os.path.exists(_be_env):
    load_dotenv(_be_env, override=False)
if os.path.exists(_root_env):
    load_dotenv(_root_env, override=False)
load_dotenv()


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_engine_lock = threading.Lock()
_postgres_disabled_until: float = 0.0


def mark_postgres_failed(cooldown_seconds: float = 60.0):
    """Đánh dấu lỗi kết nối PostgreSQL để kích hoạt circuit-breaker tạm thời."""
    global _postgres_disabled_until
    _postgres_disabled_until = time.time() + cooldown_seconds
    logger.warning(f"PostgreSQL circuit-breaker kích hoạt. Tạm ngừng kết nối trực tiếp trong {cooldown_seconds}s.")


def is_postgres_circuit_open() -> bool:
    """Kiểm tra xem kết nối PostgreSQL có đang bị tạm dừng do lỗi hay không."""
    global _postgres_disabled_until
    return time.time() < _postgres_disabled_until


def database_url() -> str:
    """
    Lấy connection string PostgreSQL từ biến môi trường PROCEDURE_DATABASE_URL.
    Tuyệt đối không hardcode mật khẩu hay credentials trong source code.
    """
    url = os.getenv("PROCEDURE_DATABASE_URL", "")
    if not url:
        logger.warning("Biến môi trường PROCEDURE_DATABASE_URL chưa được thiết lập.")
        return ""
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def get_engine() -> Engine | None:
    """
    Khởi tạo hoặc lấy SQLAlchemy Engine kết nối tới PostgreSQL (Thread-safe).
    Sử dụng double-checked locking để ngăn ngừa race condition.
    """
    global _engine, _session_factory
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                db_url = database_url()
                if not db_url:
                    return None
                try:
                    _engine = create_engine(
                        db_url,
                        pool_pre_ping=True,
                        pool_size=5,
                        max_overflow=10,
                        connect_args={"connect_timeout": 5},
                    )
                    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
                    logger.info("Khởi tạo SQLAlchemy Engine thành công với PROCEDURE_DATABASE_URL")
                except Exception as e:
                    logger.error(f"Không thể khởi tạo Engine PostgreSQL: {e}")
                    _engine = None
                    _session_factory = None
    return _engine


def get_db() -> Iterator[Session]:
    """Dependency cung cấp Session database cho các endpoint FastAPI kèm auto rollback khi lỗi."""
    engine = get_engine()
    if engine is None or _session_factory is None:
        raise RuntimeError("Cơ sở dữ liệu PostgreSQL chưa được khởi tạo thành công.")
    db = _session_factory()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_supabase_config() -> tuple[str, str]:
    """
    Lấy Supabase REST URL và Anon Key phục vụ giao tiếp trực tiếp qua PostgREST.
    Tuyệt đối không hardcode Anon Key hay URL trong source code trừ giá trị dự phòng mặc định khi môi trường thiếu.
    """
    url = (os.getenv("SUPABASE_URL") or "").strip()
    anon_key = (os.getenv("SUPABASE_ANON_KEY") or "").strip()
    if not url or not anon_key:
        if os.path.exists(_service_env):
            from dotenv import dotenv_values
            vals = dotenv_values(_service_env)
            if not url and vals.get("SUPABASE_URL"):
                url = (vals["SUPABASE_URL"] or "").strip()
            if not anon_key and vals.get("SUPABASE_ANON_KEY"):
                anon_key = (vals["SUPABASE_ANON_KEY"] or "").strip()
    if not url:
        url = "https://qqwuhiymixomwrdunfkz.supabase.co"
    if not anon_key:
        anon_key = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFxd3VoaXltaXhvbXdyZHVuZmt6Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTE0MzgxNzAsImV4cCI6MjEwNzAxNDE3MH0."
            "SHNvLsI4fVZTbm1gS0pidZWjOLJnQBTgbQ-chLA5aTA"
        )
    return url, anon_key
