"""Read-only check of runtime settings, DB and private Storage. No secrets printed."""
import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

SERVICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE))

from app.core.config import ConfigurationError, Settings
from app.core.errors import DocumentError
from app.repositories.database import Database
from app.services.storage import Storage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=SERVICE.parents[1] / ".env")
    args = parser.parse_args()
    load_dotenv(args.env_file, override=False)
    try:
        settings = Settings.from_env()
        tables = ["user_files", "document_processing_jobs", "document_extraction_results", "user_identity_profiles"]
        with Database(settings).connection(read_only=True) as conn:
            rows = conn.execute("SELECT c.relname, c.relrowsecurity FROM pg_class c "
                                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                                "WHERE n.nspname='public' AND c.relname=ANY(%s)", (tables,)).fetchall()
            if len(rows) != 4 or not all(row["relrowsecurity"] for row in rows):
                raise DocumentError("DATABASE_SCHEMA_ERROR", "Thiếu bảng Document hoặc RLS.")
        Storage(settings).check_bucket()
    except ConfigurationError as exc:
        print(str(exc))  # All configuration errors are constant sanitized messages.
        return 1
    except DocumentError as exc:
        print(f"{exc.code}: {exc.message}")
        return 1
    print("PASS: runtime configuration, DB TLS/read-only query, four RLS tables, private Storage bucket.")
    print("No records, migrations, bucket settings or files were changed. JWT is tested separately.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
