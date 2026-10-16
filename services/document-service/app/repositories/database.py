from contextlib import contextmanager
from threading import Lock

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.core.config import Settings
from app.core.errors import DocumentError

# Database ở Supabase cloud: mở 1 kết nối mới (TCP + TLS + xác thực) mất ~1–2 giây, nên giữ kết nối trong pool
# và dùng lại thay vì mở/đóng mỗi lần. Mỗi DSN một pool, dùng chung cho mọi request trong process.
_pools: dict[str, ConnectionPool] = {}
_pools_lock = Lock()


def _pool(url: str) -> ConnectionPool:
    with _pools_lock:
        pool = _pools.get(url)
        if pool is None:
            pool = ConnectionPool(
                url,
                min_size=1,
                max_size=10,
                timeout=10,
                max_idle=300,
                max_lifetime=1800,
                # Kiểm tra kết nối còn sống trước khi giao (1 lượt hỏi-đáp, rẻ hơn nhiều so với mở kết nối mới).
                check=ConnectionPool.check_connection,
                kwargs={
                    "connect_timeout": 10,
                    "row_factory": dict_row,
                    "options": "-c statement_timeout=10000",
                    # Không tự prepare câu lệnh: an toàn cả khi đi qua pooler của Supabase.
                    "prepare_threshold": None,
                    "keepalives": 1,
                    "keepalives_idle": 30,
                    "keepalives_interval": 10,
                    "keepalives_count": 3,
                },
                open=True,
            )
            _pools[url] = pool
        return pool


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings

    @contextmanager
    def connection(self, *, read_only=False):
        # Giống psycopg.connect(): xong khối thì commit, có lỗi thì rollback; sau đó kết nối quay về pool.
        try:
            with _pool(self.settings.database_url).connection() as conn:
                # psycopg mở transaction bằng "BEGIN READ ONLY" luôn, khỏi tốn thêm 1 lượt SET TRANSACTION.
                # Đặt lại mỗi lần vì kết nối được dùng lại giữa các request.
                conn.read_only = read_only
                yield conn
        except psycopg.Error:
            # No DSN or SQL details in client response/logs. PoolTimeout cũng là psycopg.Error.
            raise DocumentError("DATABASE_UNAVAILABLE", "Không thể truy cập database Document.") from None
