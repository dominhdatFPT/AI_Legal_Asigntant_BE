"""C3 read-only readiness and thread-safe connection pool. No schema creation, cloud fallback or secret logging."""
from contextlib import contextmanager
import os
from queue import Empty, LifoQueue
import threading
import time
import psycopg
from psycopg.rows import dict_row

from app.core.config import DatabaseSettings
from app.core.errors import ContractError

TABLES = (
    "contract_schema_versions", "contract_templates", "contract_template_versions",
    "contract_template_fields", "contract_drafts", "contract_draft_documents",
    "contract_generation_jobs", "contract_generation_results", "contract_analysis_jobs",
    "contract_notification_outbox",
)

_POOL_MAX_SIZE = int(os.getenv("CONTRACT_DB_POOL_SIZE", "4"))
_POOL_IDLE_TTL = 240.0
_POOLS_LOCK = threading.Lock()
_POOLS: dict[tuple[str, bool], LifoQueue] = {}


def _open_connection(url: str, connect_timeout: int, use_dict_row: bool):
    clean_url = url.replace("postgresql+psycopg://", "postgresql://", 1)
    kwargs = {
        "connect_timeout": connect_timeout,
        "prepare_threshold": None,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 3,
    }
    if use_dict_row:
        kwargs["row_factory"] = dict_row
    return psycopg.connect(clean_url, **kwargs)


@contextmanager
def pooled_connection(
    url: str | None = None,
    *,
    connect_timeout: int = 5,
    use_dict_row: bool = True,
):
    """Thread-safe connection pool with PgBouncer/Supavisor transaction-mode compatibility."""
    target_url = (url or os.getenv("CONTRACT_DATABASE_URL", "")).strip()
    if not target_url:
        target_url = DatabaseSettings.from_env().database_url
    target_url = target_url.replace("postgresql+psycopg://", "postgresql://", 1)
    key = (target_url, use_dict_row)
    with _POOLS_LOCK:
        pool = _POOLS.get(key)
        if pool is None:
            pool = LifoQueue(maxsize=_POOL_MAX_SIZE)
            _POOLS[key] = pool

    conn = None
    now = time.monotonic()
    while True:
        try:
            candidate, returned_at = pool.get_nowait()
        except Empty:
            break
        if (
            candidate is not None
            and not candidate.closed
            and not candidate.broken
            and (now - returned_at) < _POOL_IDLE_TTL
        ):
            try:
                candidate.execute("SELECT 1")
                candidate.rollback()
                conn = candidate
                break
            except Exception:
                try:
                    candidate.close()
                except Exception:
                    pass
        else:
            try:
                candidate.close()
            except Exception:
                pass

    if conn is None:
        conn = _open_connection(target_url, connect_timeout, use_dict_row)

    try:
        with conn:
            yield conn
    except Exception:
        if not conn.closed and not conn.broken:
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    finally:
        if conn is not None and not conn.closed and not conn.broken:
            try:
                pool.put_nowait((conn, time.monotonic()))
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
        elif conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def database_readiness():
    settings = DatabaseSettings.from_env()
    url = settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    try:
        # No prepared statement cache: compatible with transaction pooler.
        with psycopg.connect(url, connect_timeout=5, prepare_threshold=None,
                             options="-c statement_timeout=5000 -c lock_timeout=2000") as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            row = conn.execute("SELECT version FROM public.contract_schema_versions").fetchall()
            if row != [(1,)]:
                raise ContractError("DATABASE_SCHEMA_NOT_READY", "Schema Contract chưa sẵn sàng.")
            for table in TABLES:
                ready = conn.execute(
                    "SELECT relrowsecurity FROM pg_catalog.pg_class WHERE oid=to_regclass(%s)",
                    ("public." + table,),
                ).fetchone()
                if ready != (True,):
                    raise ContractError("DATABASE_SCHEMA_NOT_READY", "Schema Contract chưa sẵn sàng.")
            column = conn.execute("SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='contract_templates' AND column_name='last_version' AND data_type='integer' AND is_nullable='NO')").fetchone()
            if column != (True,):
                raise ContractError("DATABASE_SCHEMA_NOT_READY", "Migration quản trị Contract chưa sẵn sàng.")
        return {"database": "ready", "schema_version": 1}
    except psycopg.Error:
        raise ContractError("DATABASE_UNAVAILABLE", "Không thể truy cập database Contract.") from None
