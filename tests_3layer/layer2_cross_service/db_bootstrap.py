"""Bootstrap only the loopback Postgres instances owned by the 3-layer runner."""

import argparse
import re
from pathlib import Path

import psycopg
from psycopg import sql

BE = Path(__file__).resolve().parents[2]
INFRA_PORT, E2E_PORT = 55432, 55433
SERVICES = (
    "agent", "consultation", "contract", "document", "lawyer",
    "location", "notification", "procedure", "rag", "user",
)
EXTRA_DATABASES = {"document_schema_test", "reference_upload_tests", "auth_refresh_test", "rag_legacy_test"}
DEMO_USER = "4f251dcc-02b6-40fb-a36c-dc4366482995"

SUPABASE_SQL = """
    CREATE SCHEMA IF NOT EXISTS auth;
    CREATE OR REPLACE FUNCTION auth.jwt() RETURNS jsonb LANGUAGE sql STABLE AS $$
      SELECT COALESCE(NULLIF(current_setting('request.jwt.claims', true), '')::jsonb, '{}'::jsonb)
    $$;
    CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $$
      SELECT COALESCE(NULLIF(current_setting('request.jwt.claim.sub', true), ''), auth.jwt()->>'sub')::uuid
    $$;
    CREATE OR REPLACE FUNCTION auth.role() RETURNS text LANGUAGE sql STABLE AS $$
      SELECT COALESCE(NULLIF(current_setting('request.jwt.claim.role', true), ''), auth.jwt()->>'role')
    $$;
    GRANT USAGE ON SCHEMA auth TO anon, authenticated, service_role;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO anon, authenticated, service_role;
"""
STORAGE_SQL = """
    CREATE SCHEMA storage;
    CREATE TABLE storage.buckets (
        id text PRIMARY KEY, name text NOT NULL UNIQUE, public boolean NOT NULL DEFAULT false,
        file_size_limit bigint, allowed_mime_types text[]
    );
    CREATE TABLE storage.objects (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        bucket_id text REFERENCES storage.buckets(id), name text
    );
    ALTER TABLE storage.objects ENABLE ROW LEVEL SECURITY;
    GRANT USAGE ON SCHEMA storage TO anon, authenticated, service_role;
    GRANT SELECT, INSERT, UPDATE, DELETE ON storage.objects TO anon, authenticated, service_role;
"""
# These legacy reference relations have runtime callers and a legacy integration
# fixture, but the numbered contract migrations do not create them.
REFERENCE_CATALOG_SQL = """
    CREATE TABLE public.reference_classifications (
        key text PRIMARY KEY, code text NOT NULL, name text NOT NULL, domain text NOT NULL,
        kind text NOT NULL, procedure_id uuid, created_at timestamptz DEFAULT now(),
        updated_at timestamptz DEFAULT now()
    );
    CREATE TABLE public.contract_reference_catalog (
        id uuid PRIMARY KEY, number integer, name text, description text, domain text, procedure_key text,
        procedure_code text, procedure_name text, procedures jsonb, page_count integer, format text,
        pdf_url text, pages jsonb, page_dimensions jsonb, text_content text, content_status text,
        review_notes jsonb, source_id text, source_filename text, source_url text, source_sha256 text,
        pdf_sha256 text, view_only boolean, can_fill boolean
    );
    INSERT INTO public.reference_classifications (key, code, name, domain, kind)
    VALUES ('test-seed', 'TEST-SEED', 'Synthetic reference category', 'land', 'procedure');
"""


def database_url(name, port=INFRA_PORT):
    if port not in (INFRA_PORT, E2E_PORT):
        raise ValueError("Expected the provisioned loopback Postgres port")
    if name not in {"postgres", *(service + "_db" for service in SERVICES), *EXTRA_DATABASES}:
        raise ValueError(f"Unknown disposable database: {name}")
    return f"postgresql://postgres:postgres@127.0.0.1:{port}/{name}"


def connect(name, port=INFRA_PORT, **options):
    return psycopg.connect(database_url(name, port), connect_timeout=5, **options)


def migrations(service):
    path = BE / "services" / f"{service}-service" / "database"
    return sorted(p for p in path.rglob("*.sql") if re.match(r"\d{3}_", p.name))


def migration_text(path):
    script = path.read_text(encoding="utf-8-sig")
    if path.name == "005_seed_demo_applications.sql":
        script = script.replace(
            "v_user_id     uuid := '00000000-0000-0000-0000-000000000000'",
            f"v_user_id     uuid := '{DEMO_USER}'",
            1,
        )
    return script


def create_database(server, name):
    if server.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
        raise RuntimeError(f"{name} already exists. Start with fresh test containers.")
    server.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))


def apply_migrations(db, service):
    db.execute(SUPABASE_SQL)
    if service == "document":
        db.execute(STORAGE_SQL)
    if service == "contract":
        db.execute(REFERENCE_CATALOG_SQL)
    if service == "user":
        db.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")
    for migration in migrations(service):
        print(f"{service}_db: {migration.name}", flush=True)
        try:
            db.execute(migration_text(migration))
        except psycopg.Error as error:
            raise RuntimeError(f"Migration failed in {service}_db: {migration}") from error


def check_identity(server, port):
    expected = "legal-test-infra" if port == INFRA_PORT else "legal-test-e2e"
    actual, = server.execute("SELECT current_setting('cluster_name')").fetchone()
    if actual != expected:
        raise RuntimeError(f"PostgreSQL on port {port} is not the expected disposable cluster")


def bootstrap(port):
    with connect("postgres", port, autocommit=True) as server:
        check_identity(server, port)
        for role in ("anon", "authenticated", "service_role"):
            if not server.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
                bypass = sql.SQL("BYPASSRLS") if role == "service_role" else sql.SQL("NOBYPASSRLS")
                server.execute(sql.SQL("CREATE ROLE {} NOLOGIN {}").format(sql.Identifier(role), bypass))
        for service in SERVICES:
            name = f"{service}_db"
            create_database(server, name)
            with connect(name, port, autocommit=True) as db:
                apply_migrations(db, service)
        if port == INFRA_PORT:
            for name in ("document_schema_test", "reference_upload_tests", "auth_refresh_test", "rag_legacy_test"):
                create_database(server, name)
            # These two legacy document suites both require the same database name.
            # The runner resets it between suites to match their different bootstrap contracts.


def reset_document_schema():
    with connect("postgres", autocommit=True) as server:
        check_identity(server, INFRA_PORT)
        server.execute("DROP DATABASE document_schema_test WITH (FORCE)")
        create_database(server, "document_schema_test")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--e2e", action="store_true")
    parser.add_argument("--reset-legacy-document", action="store_true")
    args = parser.parse_args()
    if args.reset_legacy_document:
        reset_document_schema()
    else:
        bootstrap(E2E_PORT if args.e2e else INFRA_PORT)
