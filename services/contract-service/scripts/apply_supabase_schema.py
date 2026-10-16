"""Explicit C3/C4 deployment, pinned to a supplied Supabase project. No secret output."""
import argparse
import hashlib
import os
from pathlib import Path
import re
import sys
from urllib.parse import parse_qs, urlsplit

from dotenv import dotenv_values
import psycopg

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = Path(__file__).resolve().parents[1] / "database/001_contract_schema.sql"
ADMIN_SCHEMA = SCHEMA.with_name("002_admin_catalog.sql")
TABLES = (
    "contract_schema_versions", "contract_templates", "contract_template_versions",
    "contract_template_fields", "contract_drafts", "contract_draft_documents",
    "contract_generation_jobs", "contract_generation_results", "contract_analysis_jobs",
    "contract_notification_outbox",
)


def pinned_url(project):
    value = dotenv_values(ROOT / ".env", interpolate=False).get("CONTRACT_DATABASE_URL") or ""
    value = value.replace("postgresql+psycopg://", "postgresql://", 1)
    parsed = urlsplit(value)
    direct = parsed.hostname == f"db.{project}.supabase.co" and parsed.username == "postgres"
    pooled = bool(parsed.hostname and parsed.hostname.endswith(".pooler.supabase.com") and parsed.username == f"postgres.{project}")
    options = parse_qs(parsed.query, keep_blank_values=True)
    if (parsed.scheme != "postgresql" or not (direct or pooled) or parsed.port not in (None,5432)
        or parsed.path != "/postgres" or parsed.fragment or not parsed.password
        or set(options) - {"sslmode"} or options.get("sslmode", ["require"]) not in [["require"],["verify-ca"],["verify-full"]]
        or any(ch.isspace() for ch in value)
        or any(marker in value for marker in ("YOUR-PASSWORD", "YOUR_PASSWORD", "[PASSWORD]"))):
        raise ValueError("Invalid project-pinned configuration")
    return value


def existing_objects(conn):
    with conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        schemas = conn.execute("SELECT count(*) FROM pg_namespace WHERE nspname='contract_private'").fetchone()[0]
        tables = conn.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname LIKE 'contract_%'").fetchone()[0]
        return schemas, tables


def verify(conn):
    def require(condition):
        if not condition:
            raise ValueError("Schema verification failed")
    with conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        require(conn.execute("SELECT version FROM public.contract_schema_versions").fetchall() == [(1,)])
        found = conn.execute("SELECT c.relname,c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r' AND c.relname=ANY(%s)", (list(TABLES),)).fetchall()
        require(set(name for name, _ in found) == set(TABLES) and all(rls for _,rls in found))
        admin = conn.execute("SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='contract_templates' AND column_name='last_version' AND data_type='integer' AND is_nullable='NO')").fetchone()[0]
        columns = 92 if admin else 91
        require(conn.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name=ANY(%s)", (list(TABLES),)).fetchone()[0] == columns)
        denied = conn.execute("SELECT bool_and(NOT has_table_privilege(r,t,p)) FROM unnest(ARRAY['anon','authenticated']) r CROSS JOIN unnest(%s::text[]) t CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE']) p", (["public." + t for t in TABLES],)).fetchone()[0]
        require(denied is True)
        deferred = conn.execute("SELECT count(*) FROM pg_trigger WHERE tgname IN ('contract_job_result_consistency','contract_result_job_consistency') AND tgdeferrable AND tginitdeferred AND tgenabled='O'").fetchone()[0]
        require(deferred == 2)
    return {"tables":10,"columns":columns,"rls":True,"client_access_denied":True,"deferred_completion_guard":True,"schema_version":1,"admin_catalog":admin}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-ref", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--apply-admin", action="store_true", help="Apply only additive C4 migration after C3")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--readiness", action="store_true", help="Also call production database_readiness read-only")
    args = parser.parse_args()
    if sum((args.apply, args.apply_admin, args.verify)) > 1:
        raise SystemExit("FAIL: choose one operation")
    if not re.fullmatch(r"[a-z]{20}", args.project_ref):
        raise SystemExit("FAIL: invalid project reference")
    try:
        url = pinned_url(args.project_ref)
        mode = parse_qs(urlsplit(url).query).get("sslmode", ["require"])[0]
        with psycopg.connect(url, sslmode=mode, connect_timeout=10, prepare_threshold=None,
                             autocommit=True, options="-c statement_timeout=20000 -c lock_timeout=5000") as conn:
            if not conn.pgconn.ssl_in_use:
                raise ValueError("TLS required")
            present = existing_objects(conn)
            print("Project pinned; TLS active; existing Contract objects:", present)
            if args.verify:
                print("VERIFY PASS:", verify(conn))
            elif args.apply_admin:
                before = verify(conn)
                if before["admin_catalog"]:
                    raise ValueError("C4 already installed; refusing repeat migration")
                sql = ADMIN_SCHEMA.read_text(encoding="utf-8")
                print("C4 migration SHA256:", hashlib.sha256(sql.encode("utf-8")).hexdigest())
                try:
                    conn.execute(sql, prepare=False)
                except psycopg.Error:
                    conn.execute("ROLLBACK")
                    raise
                print("C4 APPLIED; VERIFY PASS:", verify(conn))
            elif args.apply:
                if present != (0,0):
                    raise ValueError("Existing schema; refusing migration or overwrite")
                sql = SCHEMA.read_text(encoding="utf-8")
                print("Migration SHA256:", hashlib.sha256(sql.encode("utf-8")).hexdigest())
                try:
                    conn.execute(sql, prepare=False)
                except psycopg.Error:
                    conn.execute("ROLLBACK")
                    raise
                print("APPLIED; VERIFY PASS:", verify(conn))
            else:
                print("READ-ONLY preflight complete; no migration applied")
        if args.readiness:
            # Use env URI exactly as configured, without injecting sslmode into it.
            os.environ["CONTRACT_DATABASE_URL"] = url
            sys.path.insert(0, str(SCHEMA.parent.parent))
            from app.core.database import database_readiness
            print("BACKEND READINESS:", database_readiness())
    except Exception as exc:
        # Never print exceptions, SQL parameters, env values, password or URI.
        print("FAIL:", type(exc).__name__, "(details suppressed to protect credentials)")
        raise SystemExit(1)
