import asyncio
import json
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api import reference_catalog as rc  # noqa: E402
from app.repositories import reference_uploads  # noqa: E402
from app.services import procedure_events as consumer  # noqa: E402

OLD_ID = str(uuid4())
PAYLOAD = {"keys": ["DD-003", "", "alias"], "identifier": "identifier", "code": "DD-003", "procedure_id": OLD_ID}


def test_purge_executes_both_deletes_in_same_transaction(monkeypatch):
    conn = Mock()
    conn.execute.side_effect = [None] * 3 + [SimpleNamespace(rowcount=3), SimpleNamespace(rowcount=1)]
    opened = []

    @contextmanager
    def connection():
        opened.append("begin")
        yield conn
        opened.append("commit")

    monkeypatch.setattr(reference_uploads, "connection", connection)
    assert consumer.purge_reference_data(PAYLOAD) == {"uploads": 3, "classifications": 1}
    assert opened == ["begin", "commit"]
    *locks, uploads, classifications = [call.args for call in conn.execute.call_args_list]
    assert [args for sql, args in locks if "pg_advisory_xact_lock" in sql] == [
        ("DD-003",), ("alias",), ("identifier",),
    ]
    keys = ["DD-003", "alias", "identifier"]
    assert "DELETE FROM public.contract_reference_uploads" in uploads[0]
    assert uploads[1] == (OLD_ID, keys, "identifier", keys, keys, OLD_ID)
    assert "DELETE FROM public.reference_classifications AS rc" in classifications[0]
    assert "kind = 'project_procedure'" in classifications[0] and "NOT EXISTS" in classifications[0]
    assert classifications[1] == (OLD_ID, keys, keys)


def test_handler_invalidates_cache_on_success_and_ignores_other_routing_keys(monkeypatch):
    purge = Mock(return_value={"uploads": 1, "classifications": 1})
    monkeypatch.setattr(consumer, "purge_reference_data", purge)
    monkeypatch.setattr(rc, "_CACHE", rc._CACHE.copy())
    generation = rc._CACHE["generation"]
    asyncio.run(consumer.handle_procedure_event("unrelated", {}))
    purge.assert_not_called()
    assert rc._CACHE["generation"] == generation
    asyncio.run(consumer.handle_procedure_event("procedure.purged", {"event_id": "evt-one", "payload": PAYLOAD}))
    purge.assert_called_once_with(PAYLOAD)
    assert rc._CACHE["generation"] == generation + 1


def test_empty_payload_is_rejected_without_opening_db(monkeypatch):
    connection = Mock()
    monkeypatch.setattr(reference_uploads, "connection", connection)
    with pytest.raises(ValueError):
        asyncio.run(consumer.handle_procedure_event("procedure.purged", {"payload": {"keys": [""]}}))
    connection.assert_not_called()


@pytest.fixture
def contract_db(monkeypatch):
    db = sqlite3.connect(":memory:")
    db.execute("ATTACH DATABASE ':memory:' AS public")
    db.executescript("""
        CREATE TABLE public.contract_reference_uploads (id text, procedure_key text, metadata text);
        CREATE TABLE public.reference_classifications (key text, code text, kind text, procedure_id text);
    """)

    class Connection:
        def execute(self, sql, params):
            if "pg_advisory_xact_lock" in sql:
                return None
            # Preserve predicates and run both DELETEs against rows with SQLite JSON support.
            sql = sql.replace("= ANY(%s)", "IN (SELECT value FROM json_each(?))")
            sql = sql.replace("%s::uuid", "?").replace("%s", "?")
            return db.execute(sql, tuple(json.dumps(p) if isinstance(p, list) else p for p in params))

    @contextmanager
    def connection():
        with db:
            yield Connection()

    monkeypatch.setattr(reference_uploads, "connection", connection)
    try:
        yield db
    finally:
        db.close()


@pytest.mark.parametrize("ambiguous_upload", [False, True])
def test_late_and_redelivered_purge_keep_new_uuid_and_its_legacy_alias(contract_db, ambiguous_upload):
    new_id = str(uuid4())
    with contract_db:
        contract_db.executemany("INSERT INTO public.contract_reference_uploads VALUES (?, ?, ?)", [
            ("old", "DD-003", json.dumps({"procedure_id": OLD_ID, "procedure": "DD-003"})),
            ("new", "DD-003", json.dumps({"procedure_id": new_id, "procedure": "DD-003"})),
            ("legacy", "alias", json.dumps({"procedure_id": "", "procedure": "alias"})),
        ])
        if ambiguous_upload:
            contract_db.execute("INSERT INTO public.contract_reference_uploads VALUES (?, ?, ?)",
                                ("ambiguous", "DD-003", json.dumps({"procedure": "DD-003"})))
        contract_db.executemany("INSERT INTO public.reference_classifications VALUES (?, ?, ?, ?)", [
            ("DD-003", "DD-003", "project_procedure", new_id),
            ("old-key", "DD-003", "project_procedure", OLD_ID),
            ("alias", "alias", "project_procedure", None),
            ("protected-kind", "alias", "official", None),
        ])
    assert consumer.purge_reference_data(PAYLOAD) == {"uploads": 2, "classifications": 2 - int(ambiguous_upload)}
    assert consumer.purge_reference_data(PAYLOAD) == {"uploads": 0, "classifications": 0}
    uploads, classifications = {("new",)}, {("DD-003",), ("protected-kind",)}
    if ambiguous_upload:
        uploads.add(("ambiguous",))
        classifications.add(("old-key",))
    assert set(contract_db.execute("SELECT id FROM public.contract_reference_uploads")) == uploads
    assert set(contract_db.execute("SELECT key FROM public.reference_classifications")) == classifications


def test_classification_delete_error_rolls_back_upload_delete(contract_db):
    with contract_db:
        contract_db.execute("INSERT INTO public.contract_reference_uploads VALUES ('old', 'DD-003', ?)",
                            (json.dumps({"procedure_id": OLD_ID}),))
        contract_db.execute("INSERT INTO public.reference_classifications VALUES ('DD-003', 'DD-003', 'project_procedure', ?)",
                            (OLD_ID,))
        contract_db.execute("""
            CREATE TRIGGER public.fail_delete BEFORE DELETE ON reference_classifications
            BEGIN SELECT RAISE(ABORT, 'classification delete failed'); END
        """)
    with pytest.raises(sqlite3.IntegrityError):
        consumer.purge_reference_data(PAYLOAD)
    assert list(contract_db.execute("SELECT id FROM public.contract_reference_uploads")) == [("old",)]
