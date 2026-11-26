import json
from uuid import uuid4
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.pool import StaticPool

from app.api import procedures
from app.core import event_outbox
from app.services import procedure_service
from test_procedure_api import _seed_purge_data, client

ACTOR = "11111111-1111-4111-8111-111111111111"
PURGE_URL = "/api/v1/procedures/admin/DD-001"


@pytest.fixture
def pending_outbox(monkeypatch, db_engine):
    monkeypatch.setenv("OUTBOX_ENABLED", "true")
    event_outbox.EVENT_OUTBOX.create(db_engine)
    kick = Mock()
    monkeypatch.setattr(event_outbox.relay, "kick", kick)
    return kick


def _outbox_rows(engine):
    with engine.connect() as conn:
        return conn.execute(select(event_outbox.EVENT_OUTBOX)).all()


def test_purge_queues_contract_cleanup_in_the_transaction(auth, db_engine, pending_outbox, monkeypatch):
    monkeypatch.delenv("RABBITMQ_URL", raising=False)
    proc_id, _, _ = _seed_purge_data()
    publish = Mock()
    monkeypatch.setattr(procedures.procedure_mq, "publish_form_template_deleted", publish)
    response = client.delete(PURGE_URL, headers={**auth(ACTOR, "admin"), "X-Request-ID": "req-purge"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "success" and body["warning"] is None
    assert "_contract_cleanup" not in body["data"]
    cleanup = body["data"]["contract_cleanup"]
    row, = _outbox_rows(db_engine)
    assert cleanup["status"] == "queued" and cleanup["event_id"] == row.id
    assert row.routing_key == "procedure.purged"
    assert row.published_at is None
    envelope = json.loads(row.payload_json)
    assert envelope["request_id"] == envelope["correlation_id"] == "req-purge"
    assert envelope["event_id"] == row.id
    payload = envelope["payload"]
    assert set(payload) == {"procedure_id", "code", "keys", "identifier", "actor_id"}
    assert (payload["procedure_id"], payload["code"], payload["identifier"], payload["actor_id"]) == (
        proc_id, "DD-001", "DD-001", ACTOR,
    )
    assert {"DD-001", "land-purge-alias"} <= set(payload["keys"])
    pending_outbox.assert_called_once_with()
    publish.assert_not_called()


def test_shared_template_conflict_does_not_enqueue(auth, db_engine, pending_outbox):
    _, template_id, _ = _seed_purge_data()
    with db_engine.begin() as conn:
        conn.execute(text("UPDATE public.procedures SET form_template_id = :id WHERE code = 'DD-002'"),
                     {"id": template_id})
    response = client.delete(PURGE_URL, headers=auth(ACTOR, "admin"))
    assert response.status_code == 409, response.text
    assert _outbox_rows(db_engine) == []
    pending_outbox.assert_not_called()


def test_purge_with_contract_db_cleans_directly_and_enqueues(auth, db_engine, pending_outbox, monkeypatch):
    proc_id, _, _ = _seed_purge_data()
    new_proc_id = str(uuid4())
    remote = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(remote, "connect")
    def attach_public(conn, _):
        conn.execute("ATTACH DATABASE ':memory:' AS public")

    try:
        with remote.begin() as conn:
            conn.execute(text("""
                CREATE TABLE public.contract_reference_uploads (id TEXT PRIMARY KEY, procedure_key TEXT, metadata TEXT)
            """))
            conn.execute(text("""
                INSERT INTO public.contract_reference_uploads VALUES (:id, 'DD-001', '{}')
            """), {"id": str(uuid4())})
            conn.execute(text("""
                CREATE TABLE public.reference_classifications
                  (key TEXT PRIMARY KEY, code TEXT, kind TEXT, procedure_id TEXT)
            """))
            conn.execute(text("""
                INSERT INTO public.reference_classifications VALUES
                  ('old-alias', 'DD-001', 'project_procedure', :old),
                  ('DD-001', 'DD-001', 'project_procedure', :new)
            """), {"old": proc_id, "new": new_proc_id})
            for owner in (proc_id, new_proc_id):
                conn.execute(text("""
                    INSERT INTO public.contract_reference_uploads VALUES (:id, 'DD-001', :metadata)
                """), {"id": str(uuid4()), "metadata": json.dumps({"procedure_id": owner})})
        monkeypatch.setenv("CONTRACT_DATABASE_URL", "postgresql://unused/contract")
        monkeypatch.setattr(procedure_service, "_purge_contract_connection", remote.begin)
        response = client.delete(PURGE_URL, headers=auth(ACTOR, "admin"))
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "success" and body["warning"] is None
        assert body["data"]["contract_cleanup"]["status"] == "completed"
        assert body["data"]["contract_cleanup"]["mode"] == "direct"
        assert body["data"]["contract_cleanup"]["sources"]["contract"]["deleted"] == 1
        assert len(_outbox_rows(db_engine)) == 1
        with remote.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM public.contract_reference_uploads")).scalar_one() == 2
            assert conn.execute(text("""
                SELECT json_extract(metadata, '$.procedure_id') FROM public.contract_reference_uploads
                WHERE json_extract(metadata, '$.procedure_id') IS NOT NULL
            """)).scalar_one() == new_proc_id
            assert conn.execute(text("""
                SELECT procedure_id FROM public.reference_classifications WHERE key = 'DD-001'
            """)).scalar_one() == new_proc_id
    finally:
        remote.dispose()


def test_purge_with_outbox_disabled_preserves_legacy_response(auth, db_engine, pending_outbox, monkeypatch):
    monkeypatch.setenv("OUTBOX_ENABLED", "false")
    _seed_purge_data()
    response = client.delete(PURGE_URL, headers=auth(ACTOR, "admin"))
    assert response.status_code == 200, response.text
    body = response.json()
    cleanup = body["data"]["contract_cleanup"]
    reason = "CONTRACT_DATABASE_URL is not configured"
    assert body["status"] == "success" and body["warning"] == body["message"]
    assert cleanup == {
        "status": "skipped", "reason": reason,
        "sources": {"contract": {"status": "skipped", "reason": reason},
                    "procedure": {"status": "completed", "deleted": 1}},
    }
    assert _outbox_rows(db_engine) == []
    pending_outbox.assert_not_called()
