import logging
import os

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine

logger = logging.getLogger(__name__)

# Tải cấu hình từ file .env ở thư mục gốc BE hoặc thư mục hiện tại
load_dotenv(os.path.join(os.path.dirname(__file__), "../../../.env"))
load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env"))
load_dotenv()

_engine: Engine | None = None


def database_url() -> str:
    """Connection string PostgreSQL từ biến PROCEDURE_DATABASE_URL (chỉ lấy từ môi trường)."""
    url = os.getenv("PROCEDURE_DATABASE_URL", "")
    if url.startswith("postgresql://"):
        # Sử dụng psycopg driver tương thích SQLAlchemy 2.0
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def get_engine() -> Engine | None:
    """Engine dùng chung; None khi chưa cấu hình PROCEDURE_DATABASE_URL."""
    global _engine
    if _engine is None:
        url = database_url()
        if not url:
            return None
        _engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=10, connect_args={"connect_timeout": 5})
    return _engine
