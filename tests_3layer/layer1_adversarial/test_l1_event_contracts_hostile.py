import asyncio
import copy
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from tests_3layer.support import INTERNAL_TOKEN, service_module


@pytest.mark.parametrize("fault", [
    "top-level-id", "classification-id", "domain", "kind", "created-string", "blank-code",
    "malformed-json", "non-object", "mismatched-code",
])
def test_registry_rejects_malformed_success_responses(monkeypatch, fault):
    with service_module("contract", "app.services.procedure_registry_client") as registry:
        pid = str(uuid4())
        valid = {
            "procedure_id": pid, "created": True, "code": "DD-012",
            "classification": {"procedure_id": pid, "kind": "project_procedure", "domain": "land",
                               "key": "DD-012", "code": "DD-012", "name": "Synthetic procedure"},
        }
        bad = copy.deepcopy(valid)
        if fault == "top-level-id":
            bad["procedure_id"] = str(uuid4())
        elif fault == "classification-id":
            bad["classification"]["procedure_id"] = str(uuid4())
        elif fault == "domain":
            bad["classification"]["domain"] = "business"
        elif fault == "kind":
            bad["classification"]["kind"] = "procedure"
        elif fault == "created-string":
            bad["created"] = "true"
        elif fault == "blank-code":
            bad["code"] = bad["classification"]["code"] = " "
        elif fault == "mismatched-code":
            bad["code"] = "DD-013"

        responses = [httpx.Response(200, json={"data": valid, "error": None})]
        if fault == "malformed-json":
            responses.append(httpx.Response(200, content="{broken"))
        elif fault == "non-object":
            responses.append(httpx.Response(200, json=["synthetic"]))
        else:
            responses.append(httpx.Response(200, json={"data": bad, "error": None}))

        def send(request):
            assert request.method == "POST" and request.url.path == "/internal/v1/procedures"
            return responses.pop(0)

        monkeypatch.setenv("PROCEDURE_SERVICE_URL", "http://procedure.test")
        monkeypatch.setenv("INTERNAL_TOKEN", INTERNAL_TOKEN)
        monkeypatch.setattr(registry, "transport", httpx.MockTransport(send))
        kwargs = dict(procedure_id=pid, name="Synthetic procedure", domain="land", code=None,
                      floor=0, form_template_id=None, pdf_data=None, pdf_filename=None)
        assert registry.register(**kwargs) == valid["classification"]
        with pytest.raises(registry.ContractError) as result:
            registry.register(**kwargs)
        assert (result.value.status, result.value.code) == (503, "PROCEDURE_SERVICE_UNAVAILABLE")
        assert not responses


@pytest.mark.parametrize("timestamp", [
    "2000-01-01T04:05:06", "2000-01-01T04:05:06Z", "2000-01-01T11:05:06+07:00",
    "2099-01-01T11:05:06+07:00", "2099-01-01T04:05:06Z",
])
def test_consultation_user_event_normalizes_time_and_unknown_reason(monkeypatch, timestamp):
    seen = []
    subject = uuid4()
    with service_module("consultation", "app.services.user_events") as events:
        def close(user_id, reason, blocked_at):
            seen.append((user_id, reason, blocked_at))
            return []

        monkeypatch.setattr(events, "_close_in_session", close)
        envelope = {"event_id": str(uuid4()), "timestamp": timestamp,
                    "payload": {"user_id": str(subject), "reason_code": "unrecognized"}}
        asyncio.run(events.handle_user_event("user.blocked", envelope))
        expected = datetime(int(timestamp[:4]), 1, 1, 4, 5, 6, tzinfo=timezone.utc)
        assert seen == [(subject, "locked", expected)]
        assert seen[0][2].utcoffset() == timedelta(0)


@pytest.mark.parametrize("fail_at", [1, 2])
def test_consultation_redelivery_finishes_pending_rooms(monkeypatch, fail_at):
    lawyer, citizen = uuid4(), uuid4()
    consultations = [{"id": uuid4(), "lawyer_id": lawyer, "citizen_id": citizen} for _ in range(3)]
    delivered = Counter()
    close_count = 0
    failed = False
    with service_module("consultation", "app.services.user_events") as events:
        def close(user_id, reason, blocked_at):
            nonlocal close_count
            close_count += 1
            return consultations

        async def publish(event_type, view, *rooms):
            nonlocal failed
            assert event_type == "consultation.closed"
            assert rooms == (f"chat:{view['id']}", f"me:{citizen}", f"me:{lawyer}")
            for index, room in enumerate(rooms):
                if view["id"] == consultations[1]["id"] and index == fail_at and not failed:
                    failed = True
                    raise ConnectionError("Synthetic realtime failure after a partial fanout")
                delivered[view["id"], room] += 1

        monkeypatch.setattr(events, "_close_in_session", close)
        monkeypatch.setattr(events.realtime, "publish", publish)
        envelope = {"event_id": str(uuid4()), "timestamp": "2026-01-01T00:00:00Z",
                    "payload": {"user_id": str(lawyer), "reason_code": "locked"}}

        async def deliver():
            with pytest.raises(RuntimeError, match="pending"):
                await events.handle_user_event("user.blocked", envelope)
            assert failed
            await events.handle_user_event("user.blocked", envelope)
            checkpoint = delivered.copy()
            await events.handle_user_event("user.blocked", envelope)
            assert delivered == checkpoint

        asyncio.run(deliver())
        assert close_count == 1
        for index, view in enumerate(consultations):
            counts = [delivered[view["id"], room] for room in
                      (f"chat:{view['id']}", f"me:{citizen}", f"me:{lawyer}")]
            assert all(count >= 1 for count in counts)
            if index != 1:
                assert counts == [1, 1, 1]


@pytest.mark.parametrize("locked", [False, True])
def test_recipient_lookup_canonicalizes_uuids_and_sends_no_caller_credentials(monkeypatch, locked):
    # 205 distinct UUIDs, plus textual aliases, duplicates and blank entries.
    ids = [str(uuid4()) for _ in range(205)]
    raw = [value for uid in ids for value in (f"  {uid.upper()} ", uid, uid)] + [" ", ""]
    caller = SimpleNamespace(headers=httpx.Headers({
        "Cookie": "caller_session=synthetic", "Authorization": "Bearer synthetic",
        "X-Request-ID": "recipient-test",
    }))
    batches = []
    now = datetime.now(timezone.utc)

    with service_module("notification", "app.services.recipient_service") as recipients:
        async def lookup(request):
            assert request.url.path == "/internal/v1/users/lookup"
            assert request.headers["X-Internal-Token"] == INTERNAL_TOKEN
            assert request.headers["X-Request-ID"] == "recipient-test"
            assert "Cookie" not in request.headers and "Authorization" not in request.headers
            batch = json.loads(request.content)["user_ids"]
            batches.append(batch)
            users = []
            for uid in batch:
                until = now - timedelta(seconds=1) if uid == ids[0] else now + timedelta(hours=1)
                status = "locked" if locked and uid == ids[-1] else "active"
                users.append({"id": uid, "role": "citizen", "status": status,
                              "locked_until": until.isoformat() if uid in (ids[0], ids[-1]) else None})
            return httpx.Response(200, json={"data": {"items": users}})

        async def scenario():
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(lookup), cookies={"shared_session": "synthetic"},
                auth=("synthetic", "synthetic"),
            ) as upstream:
                monkeypatch.setattr(recipients, "_get_http_client", lambda: upstream)
                monkeypatch.setenv("INTERNAL_TOKEN", INTERNAL_TOKEN)
                monkeypatch.setenv("USER_SERVICE_URL", "http://users.test")
                if locked:
                    with pytest.raises(recipients.ReportDomainError) as result:
                        await recipients.validate_recipients(raw, "citizen", caller)
                    assert (result.value.status_code, result.value.code) == (422, "INVALID_RECIPIENT")
                else:
                    result = await recipients.validate_recipients(raw, "citizen", caller)
                    assert [user["id"] for user in result] == ids

        asyncio.run(scenario())
    assert [len(batch) for batch in batches] == [100, 100, 5]
    assert [uid for batch in batches for uid in batch] == ids
