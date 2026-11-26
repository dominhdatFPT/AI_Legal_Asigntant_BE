"""Unit tests for lightweight listing projection and connection lifecycle in custom_form_templates."""

from contextlib import contextmanager
import json
from types import SimpleNamespace
from unittest.mock import patch

import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from app.api import custom_form_templates as cft
from app.repositories import procedure_repository as repo


def _create_sqlite_templates_engine():
    engine = sa.create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                CREATE TABLE custom_form_templates (
                    id TEXT PRIMARY KEY,
                    template_group_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    name TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    procedure_id TEXT,
                    content TEXT NOT NULL,
                    word_data BLOB,
                    reference_pdf BLOB,
                    word_filename TEXT,
                    reference_pdf_filename TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT
                )
                """
            )
        )
    return engine


def test_project_list_content_strips_heavy_fields():
    raw = {
        "code": "DD-01",
        "description": "Mo ta ngan",
        "sections": [
            {"title": "Thong tin nguoi nop", "fields": [{"key": "name", "type": "text"}] * 20},
            {"title": "Thong tin thua dat", "fields": [{"key": "parcel", "type": "text"}] * 10},
        ],
        "extra_payload": "x" * 5000,
    }
    assert cft._project_list_content(raw, include_public_content=False) == {"code": "DD-01"}
    assert cft._project_list_content(raw, include_public_content=True) == {
        "code": "DD-01",
        "description": "Mo ta ngan",
        "sections": [
            {"title": "Thong tin nguoi nop"},
            {"title": "Thong tin thua dat"},
        ],
    }


def test_list_template_rows_paginates_across_multiple_batches():
    engine = _create_sqlite_templates_engine()
    with engine.begin() as conn:
        for i in range(25):
            payload = {
                "code": f"CODE-{i:02d}",
                "description": f"Desc {i}",
                "sections": [{"title": f"Sec {i}", "fields": [{"k": "v"}] * 5}],
                "heavy": "z" * 1000,
            }
            conn.execute(
                sa.text(
                    """
                    INSERT INTO custom_form_templates (
                        id, template_group_id, version, status, name, domain, procedure_id,
                        content, created_at, updated_at
                    ) VALUES (
                        :id, :gid, 1, 'published', :name, 'dat_dai', NULL,
                        :content, '2026-01-01T00:00:00Z', :updated_at
                    )
                    """
                ),
                {
                    "id": f"00000000-0000-4000-8000-{i:012d}",
                    "gid": f"10000000-0000-4000-8000-{i:012d}",
                    "name": f"Mau {i:02d}",
                    "content": json.dumps(payload),
                    "updated_at": f"2026-01-{(i % 28) + 1:02d}T00:00:00Z",
                },
            )

    seen_calls: list[tuple[bool, bool]] = []

    @contextmanager
    def _fake_db_connection(write: bool = False, fresh: bool = False):
        seen_calls.append((write, fresh))
        with engine.connect() as conn:
            # SQLite schema does not use "public." prefix; strip it cleanly in test wrapper.
            orig_execute = conn.execute

            def _exec(stmt, params=None):
                sql_text = str(stmt).replace("public.custom_form_templates", "custom_form_templates")
                return orig_execute(sa.text(sql_text), params or {})

            conn.execute = _exec  # type: ignore[method-assign]
            yield conn

    with patch.object(cft, "_BUILTIN_SEEDED", True), patch.object(
        cft, "db_connection", side_effect=_fake_db_connection
    ):
        admin_items, total = cft._list_template_rows(["domain='dat_dai'"], {}, "updated_at DESC", include_public_content=False)
        assert len(admin_items) == 25
        assert total == 25
        assert all(set(item["content"].keys()) == {"code"} for item in admin_items)
        assert seen_calls == [(False, True)]

        seen_calls.clear()
        public_items, total = cft._list_template_rows(["domain='dat_dai'"], {}, "name", include_public_content=True)
        assert len(public_items) == 25
        assert total == 25
        assert public_items[0]["content"] == {
            "code": "CODE-00",
            "description": "Desc 0",
            "sections": [{"title": "Sec 0"}],
        }
        assert seen_calls == [(False, True)]

        page_items, total = cft._list_template_rows(["domain='dat_dai'"], {}, "name", page=2, page_size=10)
        assert len(page_items) == 10 and total == 25
        assert all('_total' not in item for item in page_items)
        empty_items, total = cft._list_template_rows(["domain='dat_dai'"], {}, "name", page=4, page_size=10)
        assert empty_items == [] and total == 25


def test_list_template_rows_uses_jsonb_projection_on_postgresql():
    executed_sql: list[str] = []

    class _FakeResult:
        def mappings(self):
            return self

        def all(self):
            return [
                {
                    "id": "00000000-0000-4000-8000-000000000001",
                    "_total": 1,
                    "template_group_id": "10000000-0000-4000-8000-000000000001",
                    "version": 1,
                    "status": "published",
                    "name": "Mau PG",
                    "domain": "dat_dai",
                    "procedure_id": None,
                    "content": {"code": "PG-01"},
                    "has_word_file": True,
                    "has_reference_pdf": False,
                    "word_filename": "a.docx",
                    "reference_pdf_filename": None,
                    "created_at": "2026-01-01",
                    "updated_at": "2026-01-01",
                    "deleted_at": None,
                }
            ]

    class _FakePgConn:
        dialect = SimpleNamespace(name="postgresql")

        def execute(self, stmt, params=None):
            executed_sql.append(str(stmt))
            return _FakeResult()

    @contextmanager
    def _fake_db_connection(write: bool = False, fresh: bool = False):
        assert write is False
        assert fresh is True
        yield _FakePgConn()

    with patch.object(cft, "_BUILTIN_SEEDED", True), patch.object(
        cft, "db_connection", side_effect=_fake_db_connection
    ):
        admin_rows, admin_total = cft._list_template_rows(["1=1"], {}, "updated_at DESC", include_public_content=False)
        public_rows, public_total = cft._list_template_rows(["1=1"], {}, "name", include_public_content=True)

    assert len(admin_rows) == 1
    assert len(public_rows) == 1
    assert admin_total == public_total == 1
    assert "jsonb_build_object('code', COALESCE(content->>'code', '')) AS content" in executed_sql[0]
    assert "jsonb_array_elements" in executed_sql[1]


def test_db_connection_autocommit_fresh_and_invalidate_on_pg():
    events: list[str] = []

    class _FakeConn:
        def __init__(self, should_fail: bool = False):
            self._should_fail = should_fail

        def __enter__(self):
            events.append("enter")
            return self

        def __exit__(self, exc_type, exc, tb):
            events.append("exit")
            return False

        def execution_options(self, **kwargs):
            events.append(f"exec_opts:{kwargs.get('isolation_level')}")
            return self

        def invalidate(self):
            events.append("invalidate")

    class _FakeEngine:
        dialect = SimpleNamespace(name="postgresql")

        def connect(self):
            events.append("connect")
            return _FakeConn()

        def begin(self):
            events.append("begin")
            return _FakeConn()

    with patch.object(repo, "get_engine", return_value=_FakeEngine()):
        with repo.db_connection(write=False, fresh=True):
            events.append("body")
        assert events == ["connect", "exec_opts:AUTOCOMMIT", "enter", "body", "invalidate", "exit"]

        events.clear()
        try:
            with repo.db_connection(write=True):
                raise sa.exc.OperationalError("stmt", {}, RuntimeError("boom"))
        except repo.ProcedureDbUnavailable:
            pass
        assert "invalidate" in events


def test_row_projects_metadata_without_selecting_bytea():
    engine = _create_sqlite_templates_engine()
    tid = "00000000-0000-4000-8000-000000000099"
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                INSERT INTO custom_form_templates (
                    id, template_group_id, version, status, name, domain, procedure_id,
                    content, word_data, reference_pdf, word_filename, reference_pdf_filename,
                    created_at, updated_at
                ) VALUES (
                    :id, :gid, 1, 'published', 'Mau lon', 'dat_dai', NULL,
                    :content, :word_data, :pdf_data, 'mau.docx', 'ref.pdf',
                    '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'
                )
                """
            ),
            {
                "id": tid,
                "gid": "10000000-0000-4000-8000-000000000099",
                "content": json.dumps({"code": "DD-37", "sections": []}),
                "word_data": b"W" * 35000,
                "pdf_data": b"%PDF-" + b"P" * 130000,
            },
        )

    executed_sql: list[str] = []
    with engine.connect() as conn:
        orig_execute = conn.execute

        def _exec(stmt, params=None):
            sql_text = str(stmt)
            executed_sql.append(sql_text)
            return orig_execute(
                sa.text(sql_text.replace("public.custom_form_templates", "custom_form_templates")),
                params or {},
            )

        conn.execute = _exec  # type: ignore[method-assign]
        row = cft._row(conn, tid)
        word_bytes = cft._read_template_bytea(tid, "word_data", conn=conn)
        digest = cft._template_word_sha256(conn, tid, has_word_file=True)

    assert "SELECT *" not in executed_sql[0]
    assert "has_word_file" in row and "has_reference_pdf" in row
    assert "word_data" not in row and "reference_pdf" not in row
    view = cft._view(row)
    assert view["has_word_file"] is True
    assert view["has_reference_pdf"] is True
    assert word_bytes == b"W" * 35000
    assert len(digest) == 64


def test_read_template_bytea_chunks_across_fresh_pg_connections():
    payload = bytes((i % 251) for i in range(120000))
    calls: list[tuple[str, dict]] = []
    conn_flags: list[tuple[bool, bool]] = []

    class _FakeMappingResult:
        def __init__(self, row):
            self._row = row

        def first(self):
            return self._row

    class _FakeExecResult:
        def __init__(self, row=None, scalar=None):
            self._row = row
            self._scalar = scalar

        def mappings(self):
            return _FakeMappingResult(self._row)

        def scalar_one_or_none(self):
            return self._scalar

    class _FakePgConn:
        dialect = SimpleNamespace(name="postgresql")

        def execute(self, stmt, params=None):
            sql = str(stmt)
            p = dict(params or {})
            calls.append((sql, p))
            return _FakeExecResult(scalar=payload)

    @contextmanager
    def _fake_db_connection(write: bool = False, fresh: bool = False):
        conn_flags.append((write, fresh))
        yield _FakePgConn()

    with patch.object(cft, "db_connection", side_effect=_fake_db_connection):
        reassembled = cft._read_template_bytea(
            "fdef2ae9-559b-440a-8e52-b5e7aca49b9b",
            "reference_pdf",
            public=True,
        )

    assert reassembled == payload
    assert conn_flags == [(False, False)]
    assert len(calls) == 1
    sql = calls[0][0].lower().replace(" ", "")
    assert "status='published'" in sql
    assert "deleted_atisnull" in sql
