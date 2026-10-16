"""Apply only the additive Task 4 session migration to the guarded Document project."""
import argparse
from pathlib import Path
import sys

from dotenv import load_dotenv

SERVICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE))
from app.core.config import ConfigurationError, Settings
from app.core.errors import DocumentError
from app.repositories.database import Database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-ref", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    load_dotenv(SERVICE.parents[1] / ".env", override=False)
    try:
        settings = Settings.from_env()
        if settings.supabase_url != f"https://{args.project_ref}.supabase.co":
            raise ConfigurationError("Project không khớp cấu hình Document; không thay đổi gì.")
        with Database(settings).connection(read_only=not args.apply) as conn:
            exists = conn.execute("SELECT to_regclass('public.document_identity_ocr_sessions') AS name").fetchone()["name"]
            if args.apply and not exists:
                conn.execute((SERVICE / "database/003_identity_ocr_sessions.sql").read_text(encoding="utf-8"))
            elif not exists:
                print("Session table absent. No changes; use --apply after local tests.")
                return 0
            row = conn.execute("SELECT relrowsecurity FROM pg_class WHERE oid='public.document_identity_ocr_sessions'::regclass").fetchone()
            if not row["relrowsecurity"]:
                raise DocumentError("DATABASE_SCHEMA_ERROR", "Session table thiếu RLS.")
            columns = conn.execute("SELECT column_name FROM information_schema.columns "
                                   "WHERE table_schema='public' AND table_name='document_identity_ocr_sessions'").fetchall()
            required = {"id", "owner_user_id", "idempotency_key", "request_hash", "status", "raw_front", "raw_back",
                        "fields", "warnings", "verification_status", "error_code", "error_status",
                        "front_storage_key", "back_storage_key", "front_sha256", "back_sha256",
                        "front_size_bytes", "back_size_bytes", "created_at", "updated_at"}
            if not required <= {value["column_name"] for value in columns}:
                raise DocumentError("DATABASE_SCHEMA_ERROR", "Session table thiếu cột; không tự sửa bảng hiện có.")
            for role in ("anon", "authenticated"):
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                    allowed = conn.execute("SELECT has_table_privilege(%s,'public.document_identity_ocr_sessions',%s) AS allowed",
                                           (role, privilege)).fetchone()["allowed"]
                    if allowed:
                        raise DocumentError("DATABASE_SCHEMA_ERROR", "Browser role không được truy cập session table.")
            for privilege in ("SELECT", "INSERT", "UPDATE"):
                if not conn.execute("SELECT has_table_privilege('service_role','public.document_identity_ocr_sessions',%s) AS allowed",
                                    (privilege,)).fetchone()["allowed"]:
                    raise DocumentError("DATABASE_SCHEMA_ERROR", "Session table thiếu quyền backend.")
        print("PASS: identity OCR session schema/RLS/backend privileges; browser access revoked; original tables/bucket unchanged.")
    except (ConfigurationError, DocumentError) as exc:
        print(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
