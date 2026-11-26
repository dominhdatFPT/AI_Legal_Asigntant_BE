"""Apply later migrations to the tables recreated by the legacy pgvector fixture."""

from psycopg import connect
import pytest

from tests_3layer.layer2_cross_service.db_bootstrap import database_url, migrations

URL = database_url("rag_legacy_test")


@pytest.hookimpl(wrapper=True)
def pytest_fixture_setup(fixturedef, request):
    is_legacy_pg = fixturedef.argname == "pg" and request.node.path.name == "test_pgvector_integration.py"
    if is_legacy_pg:
        with connect(URL) as db:
            db.execute("DROP TABLE IF EXISTS public.document_versions, public.ingest_log")
    value = yield
    if is_legacy_pg:
        with connect(URL, autocommit=True) as db:
            for migration in migrations("rag")[1:]:
                db.execute(migration.read_text(encoding="utf-8-sig"))
    return value
