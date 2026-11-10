from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def database_url() -> str:
    url = get_settings().rag_database_url
    if not url:
        raise RuntimeError("Chưa cấu hình RAG_DATABASE_URL")
    # Dùng driver psycopg 3 (giống user-service, agent-service)
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    elif url.startswith("postgresql+asyncpg://"):
        url = "postgresql+psycopg://" + url[len("postgresql+asyncpg://"):]
    return url


def engine_options(url: str | None = None) -> dict:
    """Tham số create_engine, nơi duy nhất tạo chúng. url để trống hoặc là Postgres; url sqlite chỉ có pool_pre_ping
    (sqlite không hiểu connect_timeout, keepalives, pool_size...). Cấu hình timeout <= 0 thì không thêm tham số tương ứng.

    - pool_pre_ping: kiểm tra kết nối còn sống mỗi lần lấy từ pool, chết thì tạo lại.
    - pool_size/max_overflow nhỏ: Supabase pooler giới hạn số kết nối đồng thời.
    - prepare_threshold=None: tắt prepared statement của psycopg 3, vì Supabase pooler (chế độ transaction) không hỗ trợ.
    - keepalives_*: TCP keepalive để phát hiện sớm kết nối chết ngầm (mạng rớt, pooler tự ngắt kết nối nhàn rỗi).
    - connect_timeout, pool_recycle: giới hạn thời gian chờ nối và tuổi kết nối, tránh treo vì kết nối chết ngầm."""
    options: dict = {"pool_pre_ping": True}
    if url is not None and url.startswith("sqlite"):
        return options
    settings = get_settings()
    options["pool_size"] = 3
    options["max_overflow"] = 2
    if settings.db_pool_recycle_seconds > 0:
        options["pool_recycle"] = settings.db_pool_recycle_seconds
    connect_args: dict = {
        "prepare_threshold": None,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 3,
    }
    if settings.db_connect_timeout_seconds > 0:
        connect_args["connect_timeout"] = settings.db_connect_timeout_seconds
    options["connect_args"] = connect_args
    return options


def _apply_statement_timeout(engine: Engine, seconds: int) -> None:
    """Đặt statement_timeout cho mỗi kết nối mới: truy vấn quá giờ bị server huỷ, psycopg ném QueryCanceled, SQLAlchemy đổi
    thành OperationalError (một SQLAlchemyError) nên đi qua đường xử lý lỗi DB sẵn có (503). Dùng câu SET sau khi kết nối
    thay vì tham số `options` lúc mở kết nối, vì pooler (pgbouncer, Supavisor) có thể từ chối tham số khởi động lạ."""
    if seconds <= 0:
        return
    millis = int(seconds) * 1000

    @event.listens_for(engine, "connect")
    def _set_statement_timeout(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f"SET statement_timeout = {millis}")
        finally:
            cursor.close()
        dbapi_connection.commit()  # không commit thì lần rollback khi trả kết nối về pool sẽ xoá SET


def get_engine() -> Engine:
    global _engine, _session_factory
    if _engine is None:
        url = database_url()
        if url.startswith("sqlite"):
            _engine = create_engine(url, **engine_options(url))
        else:
            _engine = create_engine(url, **engine_options())
            _apply_statement_timeout(_engine, get_settings().db_command_timeout_seconds)
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_session() -> Session:
    """Mở session dùng ngoài FastAPI (script nạp dữ liệu). Nhớ đóng sau khi dùng."""
    get_engine()
    return _session_factory()


def get_db() -> Iterator[Session]:
    db = get_session()
    try:
        yield db
    finally:
        db.close()
