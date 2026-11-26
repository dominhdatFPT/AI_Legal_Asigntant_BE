import threading
from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_read_session_factory: sessionmaker[Session] | None = None
_engine_lock = threading.Lock()

READ_METHODS = {"GET", "HEAD", "OPTIONS"}


def database_url() -> str:
    url = get_settings().agent_database_url
    if not url:
        raise RuntimeError("Chưa cấu hình AGENT_DATABASE_URL")
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


_POOL_OPTIONS = {
    "pool_pre_ping": True,
    "pool_size": 3,
    "max_overflow": 2,
    "pool_recycle": 300,
    "connect_args": {
        "prepare_threshold": None,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 3,
    },
}


def get_engine() -> Engine:
    global _engine, _session_factory, _read_session_factory
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                url = database_url()
                if url.startswith("sqlite"):
                    engine = read_engine = create_engine(url, pool_pre_ping=True)
                else:
                    engine = create_engine(url, **_POOL_OPTIONS)
                    # Write (3+2=5) + Read (2+1=3) = tối đa 8 kết nối/instance.
                    read_pool_options = {**_POOL_OPTIONS, "pool_size": 2, "max_overflow": 1}
                    read_engine = create_engine(url, isolation_level="AUTOCOMMIT", **read_pool_options)
                _session_factory = sessionmaker(bind=engine, expire_on_commit=False)
                _read_session_factory = (
                    _session_factory if read_engine is engine else sessionmaker(bind=read_engine, expire_on_commit=False)
                )
                _engine = engine  # Publish after both factories are ready.
    return _engine


def get_db(request: Request = None) -> Iterator[Session]:
    """GET/HEAD/OPTIONS dùng kết nối autocommit, còn lại dùng transaction; ngoài FastAPI (request None) → transaction."""
    get_engine()
    read_only = request is not None and request.method in READ_METHODS
    db = (_read_session_factory if read_only else _session_factory)()
    try:
        yield db
    finally:
        db.close()
