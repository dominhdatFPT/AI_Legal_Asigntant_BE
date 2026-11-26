import json
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import procedures
from app.core import event_outbox
from app.main import app
from form_samples import valid_form

BASE = "/api/v1/procedures/applications"
OWNER = "11111111-1111-4111-8111-111111111111"


@pytest.mark.parametrize("mode", ["enabled", "enqueue-fails", "disabled"])
def test_submit_uses_outbox_or_legacy_background_task(auth, db_engine, monkeypatch, mode):
    event_outbox.EVENT_OUTBOX.create(db_engine)
    monkeypatch.setenv("OUTBOX_ENABLED", "false" if mode == "disabled" else "true")
    kick = Mock()
    publish = AsyncMock()
    monkeypatch.setattr(event_outbox.relay, "kick", kick)
    monkeypatch.setattr(procedures.procedure_mq, "publish_application_submitted", publish)
    if mode == "enqueue-fails":
        monkeypatch.setattr(procedures.outbox, "enqueue", Mock(side_effect=RuntimeError("outbox unavailable")))

    client = TestClient(app)
    headers = {**auth(OWNER), "X-Request-ID": "req-submit-outbox"}
    draft = client.post(BASE, headers=headers, json={
        "procedure_code": "DD-001", "status": "draft", "form_data": valid_form("DD-001"),
    })
    assert draft.status_code == 200, draft.text
    record_id = draft.json()["data"]["id"]
    submitted = client.post(f"{BASE}/land/{record_id}/submit", headers=headers)
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["data"]["status"] == "submitted"
    payload = {"record_id": record_id, "domain": "land", "user_id": OWNER}

    with db_engine.connect() as conn:
        rows = conn.execute(select(event_outbox.EVENT_OUTBOX)).all()
    if mode == "enabled":
        row, = rows
        assert row.routing_key == "procedure.application.submitted"
        event = json.loads(row.payload_json)
        assert event["event_type"] == row.routing_key
        assert event["request_id"] == "req-submit-outbox"
        assert event["payload"] == payload
        kick.assert_called_once_with()
        publish.assert_not_awaited()
    else:
        assert rows == []
        kick.assert_not_called()
        publish.assert_awaited_once_with(**payload)
