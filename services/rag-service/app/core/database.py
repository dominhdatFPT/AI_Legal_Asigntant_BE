from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
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


def get_engine() -> Engine:
    global _engine, _session_factory
    if _engine is None:
        _engine = create_engine(database_url(), pool_pre_ping=True)
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
