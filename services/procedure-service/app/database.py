import os
import logging
import threading
import time
from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

logger = logging.getLogger(__name__)

# Tải cấu hình: ưu tiên .env nội bộ của procedure-service, sau đó mới bổ sung từ BE/.env
_service_env = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
_be_env = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.env"))
_keys_env = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.keys/onlyoffice.env"))
_root_env = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.env"))

if os.getenv("PROCEDURE_SKIP_DOTENV") != "1":
    for env_path in (_service_env, _be_env, _keys_env, _root_env):
        if os.path.exists(env_path):
            load_dotenv(env_path, override=False)


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


def _attach_pool_guards(engine: Engine) -> None:
    """
    Giới hạn số truy vấn, số dòng và tuổi thọ mỗi kết nối TCP PostgreSQL trong pool.
    Tránh một kết nối tái sử dụng tích luỹ vượt ngưỡng nghẽn TCP trên một số mạng.
    Không áp dụng cho SQLite StaticPool (dùng trong unit test).
    """
    if getattr(getattr(engine, "dialect", None), "name", "") != "postgresql":
        return

    import time
    from sqlalchemy import event

    max_queries = 500
    max_rows = 5000
    max_age_seconds = 300.0

    @event.listens_for(engine.pool, "connect")
    def _on_connect(dbapi_connection, connection_record):
        connection_record.info["created_at"] = time.monotonic()
        connection_record.info["queries"] = 0
        connection_record.info["rows"] = 0
        connection_record.info["retire"] = False

    @event.listens_for(engine, "after_cursor_execute")
    def _after_execute(conn, cursor, statement, parameters, context, executemany):
        proxy = getattr(conn, "connection", None)
        rec = getattr(proxy, "_connection_record", None)
        info = rec.info if rec is not None else getattr(proxy, "record_info", None)
        if info is None:
            return
        info["queries"] = info.get("queries", 0) + 1
        rc = getattr(cursor, "rowcount", 0)
        info["rows"] = info.get("rows", 0) + (rc if isinstance(rc, int) and rc > 0 else 1)
        if info["queries"] >= max_queries or info["rows"] >= max_rows:
            info["retire"] = True

    @event.listens_for(engine.pool, "checkin")
    def _on_checkin(dbapi_connection, connection_record):
        info = connection_record.info
        created_at = info.get("created_at")
        expired = created_at is not None and (time.monotonic() - created_at) >= max_age_seconds
        if info.get("retire") or expired:
            connection_record.invalidate()


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
                    import sys

                    from sqlalchemy.engine import make_url
                    url = make_url(db_url)
                    if url.get_backend_name() == "sqlite":
                        _engine = create_engine(db_url)
                        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
                        return _engine
                    transaction_pooler = url.port == 6543 and "pooler" in (url.host or "").lower()
                    connect_args = {
                        "connect_timeout": 3,
                        "keepalives": 1,
                        "keepalives_idle": 5,
                        "keepalives_interval": 3,
                        "keepalives_count": 3,
                    }
                    if transaction_pooler and url.get_driver_name() == "psycopg":
                        connect_args["prepare_threshold"] = None
                    if sys.platform.startswith("linux"):
                        connect_args["tcp_user_timeout"] = 10000

                    _engine = create_engine(
                        db_url,
                        pool_pre_ping=False,
                        pool_use_lifo=True,
                        pool_size=3 if transaction_pooler else 2,
                        max_overflow=1,
                        pool_timeout=3,
                        pool_recycle=600,
                        connect_args=connect_args,
                    )
                    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
                    logger.info("Khởi tạo SQLAlchemy Engine thành công với PROCEDURE_DATABASE_URL")
                except Exception as e:
                    logger.error(f"Không thể khởi tạo Engine PostgreSQL: {e}")
                    _engine = None
                    _session_factory = None
    return _engine


def get_supabase_config() -> tuple[str, str]:
    """Đọc cấu hình Supabase REST từ môi trường."""
    url = (os.getenv("SUPABASE_URL") or "").strip()
    anon_key = (os.getenv("SUPABASE_ANON_KEY") or "").strip()
    return (url, anon_key) if url and anon_key else ("", "")
