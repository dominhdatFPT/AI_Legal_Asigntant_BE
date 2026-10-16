"""C3 read-only readiness. No schema creation, cloud fallback or secret logging."""
import psycopg

from app.core.config import DatabaseSettings
from app.core.errors import ContractError

TABLES = (
    "contract_schema_versions", "contract_templates", "contract_template_versions",
    "contract_template_fields", "contract_drafts", "contract_draft_documents",
    "contract_generation_jobs", "contract_generation_results", "contract_analysis_jobs",
    "contract_notification_outbox",
)


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
