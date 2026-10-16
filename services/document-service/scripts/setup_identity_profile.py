"""Guarded additive Task 5 migration; default verifies without writing."""
import argparse
from pathlib import Path
import sys

from dotenv import load_dotenv

SERVICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE))
from app.core.config import ConfigurationError, Settings
from app.core.errors import DocumentError
from app.repositories.database import Database


def verify(conn):
    table = "public.user_identity_profiles"
    checks = conn.execute("SELECT conname FROM pg_constraint WHERE conrelid=%s::regclass", (table,)).fetchall()
    if not {"document_profile_source_xor", "document_profile_address_limit"} <= {r["conname"] for r in checks}:
        raise DocumentError("DATABASE_SCHEMA_ERROR", "Profile thiếu ràng buộc Task 5; không tự sửa schema hiện hữu.")
    fk = conn.execute("SELECT 1 FROM pg_constraint WHERE conrelid=%s::regclass AND contype='f' "
                      "AND confrelid='public.document_identity_ocr_sessions'::regclass AND confdeltype='r'", (table,)).fetchone()
    trigger = conn.execute("SELECT 1 FROM pg_trigger WHERE tgrelid='public.document_identity_ocr_sessions'::regclass "
                           "AND tgname='document_guard_identity_session' AND tgenabled='O'").fetchone()
    profile_trigger = conn.execute("SELECT 1 FROM pg_trigger WHERE tgrelid=%s::regclass AND NOT tgisinternal "
                                   "AND tgfoid='document_private.guard_profile()'::regprocedure AND tgenabled='O'", (table,)).fetchone()
    if not fk or not trigger or not profile_trigger:
        raise DocumentError("DATABASE_SCHEMA_ERROR", "Profile thiếu FK/trigger Task 5.")
    for name in (table, "public.document_identity_ocr_sessions"):
        if not conn.execute("SELECT relrowsecurity FROM pg_class WHERE oid=%s::regclass", (name,)).fetchone()["relrowsecurity"]:
            raise DocumentError("DATABASE_SCHEMA_ERROR", "Thiếu RLS.")
        for role in ("anon", "authenticated"):
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                if conn.execute("SELECT has_table_privilege(%s,%s,%s) AS allowed", (role, name, privilege)).fetchone()["allowed"]:
                    raise DocumentError("DATABASE_SCHEMA_ERROR", "Browser role không được CRUD dữ liệu CCCD.")
        for privilege in ("SELECT", "INSERT", "UPDATE"):
            if not conn.execute("SELECT has_table_privilege('service_role',%s,%s) AS allowed", (name, privilege)).fetchone()["allowed"]:
                raise DocumentError("DATABASE_SCHEMA_ERROR", "Thiếu quyền backend.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-ref", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    load_dotenv(SERVICE.parents[1] / ".env", override=False)
    try:
        settings = Settings.from_env()
        if settings.supabase_url != f"https://{args.project_ref}.supabase.co":
            raise ConfigurationError("Project không khớp Document; không thay đổi gì.")
        with Database(settings).connection(read_only=not args.apply) as conn:
            exists = conn.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
                                  "AND table_name='user_identity_profiles' AND column_name='source_session_id'").fetchone()
            if not exists:
                if not args.apply:
                    print("Task 5 absent; no changes. Apply only after local tests.")
                    return 0
                if not conn.execute("SELECT to_regclass('public.document_identity_ocr_sessions') AS name").fetchone()["name"]:
                    raise DocumentError("DATABASE_SCHEMA_ERROR", "Cần migration 001/003 trước Task 5.")
                if conn.execute("SELECT 1 FROM public.user_identity_profiles WHERE char_length(permanent_address)>2000 LIMIT 1").fetchone():
                    raise DocumentError("DATABASE_SCHEMA_ERROR", "Dữ liệu cũ vượt giới hạn; dừng, không cắt dữ liệu.")
                before = conn.execute("SELECT count(*) AS n FROM public.user_identity_profiles").fetchone()["n"]
                conn.execute((SERVICE / "database/004_identity_profile_session.sql").read_text(encoding="utf-8"))
                if conn.execute("SELECT count(*) AS n FROM public.user_identity_profiles").fetchone()["n"] != before:
                    raise DocumentError("DATABASE_SCHEMA_ERROR", "Profile count changed unexpectedly.")
            verify(conn)
        print("PASS: Task 5 profile/session constraints, triggers, RLS and backend/browser privileges; existing profiles preserved, bucket unchanged.")
    except (ConfigurationError, DocumentError):
        print("FAIL: Task 5 setup/config/schema check; no secrets or database rows printed.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
