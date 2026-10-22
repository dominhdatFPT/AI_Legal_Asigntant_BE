import os
from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.responses import LAWYER_DB_UNAVAILABLE, ApiError


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None

# DB ở Supabase cloud: giống User Service, pool_pre_ping + bỏ kết nối cũ quá 5 phút + TCP keepalive
# (thiếu pre_ping thì kết nối bị pooler cắt vẫn nằm trong pool, request lấy trúng sẽ treo rồi lỗi).
_POOL_OPTIONS = {
    "pool_pre_ping": True,
    "pool_size": 5,
    "max_overflow": 10,
    "pool_recycle": 300,
    "connect_args": {"keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 3},
}


def database_url() -> str | None:
    url = os.getenv("LAWYER_DATABASE_URL")
    if url and url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url or None


def get_engine() -> Engine:
    global _engine, _session_factory
    if _engine is None:
        url = database_url()
        if url is None:
            raise ApiError(503, LAWYER_DB_UNAVAILABLE)
        _engine = create_engine(url, **_POOL_OPTIONS)
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_db(request: Request = None) -> Iterator[Session]:
    get_engine()
    db = _session_factory()
    try:
        yield db
    finally:
        db.close()
