from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


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
    global _engine, _session_factory
    if _engine is None:
        url = database_url()
        _engine = (
            create_engine(url, pool_pre_ping=True)
            if url.startswith("sqlite")
            else create_engine(url, **_POOL_OPTIONS)
        )
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_db() -> Iterator[Session]:
    get_engine()
    db = _session_factory()
    try:
        yield db
    finally:
        db.close()
