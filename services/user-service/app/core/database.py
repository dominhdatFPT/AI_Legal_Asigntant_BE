import os
from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_read_session_factory: sessionmaker[Session] | None = None

# Request chỉ đọc → dùng kết nối autocommit (xem get_db).
READ_METHODS = {"GET", "HEAD", "OPTIONS"}

# DB nằm trên Supabase cloud: mỗi lượt hỏi-đáp với DB mất ~100–250 ms.
# Bật pool_pre_ping: keepalive + recycle vẫn để lọt kết nối bị pooler cắt (SSL unexpected eof), request lấy
# trúng sẽ treo rồi lỗi 500. Đổi 1 lượt ping mỗi lần lấy kết nối lấy độ ổn định.
_POOL_OPTIONS = {
    "pool_pre_ping": True,
    "pool_size": 5,
    "max_overflow": 10,
    "pool_recycle": 300,
    "connect_args": {"keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 3},
}


def database_url() -> str:
    url = os.getenv("USER_DATABASE_URL")
    if not url:
        raise RuntimeError("Chưa cấu hình USER_DATABASE_URL")
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def get_engine() -> Engine:
    global _engine, _session_factory, _read_session_factory
    if _engine is None:
        url = database_url()
        _engine = create_engine(url, **_POOL_OPTIONS)
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
        # Autocommit: không có BEGIN/ROLLBACK quanh câu SELECT → mỗi câu chỉ 1 lượt hỏi-đáp.
        # Câu ghi lẻ trong request GET (vd. tự mở khóa đã hết hạn) được lưu ngay khi chạy.
        read_engine = create_engine(url, isolation_level="AUTOCOMMIT", **_POOL_OPTIONS)
        _read_session_factory = sessionmaker(bind=read_engine, expire_on_commit=False)
    return _engine


def get_db(request: Request = None) -> Iterator[Session]:
    """Session cho 1 request: GET/HEAD/OPTIONS dùng kết nối autocommit, còn lại dùng transaction.

    Gọi ngoài FastAPI (script) thì không có request → dùng transaction như trước.
    """
    get_engine()
    read_only = request is not None and request.method in READ_METHODS
    db = (_read_session_factory if read_only else _session_factory)()
    try:
        yield db
    finally:
        db.close()
