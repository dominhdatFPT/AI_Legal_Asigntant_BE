import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from psycopg.types.json import Jsonb

from tests_3layer.layer2_cross_service.db_bootstrap import INFRA_PORT, connect
from tests_3layer.layer2_cross_service.live import live_services, require_consumer, rows, seed_user
from tests_3layer.support import INTERNAL_TOKEN, auth_headers, eventually, response_data, service_module

LAWYERS = "/api/v1/lawyers"
CONSULTATIONS = "/api/v1/consultations"


def pending(user_id, card):
    application_id = uuid4()
    with connect("lawyer_db") as db:
        db.execute("""
            INSERT INTO lawyer_applications
              (id, user_id, type, status, bar_card_number, specialization_codes, card_document_id, submitted_at)
            VALUES (%s, %s, 'new', 'pending', %s, %s, %s, now())
        """, (application_id, user_id, card, Jsonb(["land"]), uuid4()))
    return application_id


def test_approval_notification_and_lock_lifecycle(live_services, tmp_path):
    trace = tmp_path / "cache-invalidation.jsonl"
    live_services.start("user")
    live_services.start("lawyer")
    live_services.start("consultation", LEGAL_TEST_CACHE_TRACE=str(trace))
    live_services.start("notification", RUN_NOTIFICATION_HUB_IN_API="true")
    require_consumer("q.consultation.user_events")
    require_consumer("q.notification.events")

    admin = seed_user(INFRA_PORT, role="admin")
    citizen = seed_user(INFRA_PORT)
    conflict = seed_user(INFRA_PORT)
    candidate = seed_user(INFRA_PORT)
    existing_lawyer = seed_user(INFRA_PORT, role="lawyer")
    admin_auth = auth_headers(live_services.keys, admin, "admin")
    taken = f"{uuid4().hex}/TP"
    with connect("lawyer_db") as db:
        db.execute("""
            INSERT INTO lawyer_profiles (user_id, source, status, bar_card_number)
            VALUES (%s, 'admin_created', 'active', %s)
        """, (existing_lawyer, taken))

    duplicate_application = pending(conflict, taken)
    application = pending(candidate, f"{uuid4().hex}/TP")
    with live_services.client("lawyer") as lawyer:
        blocked = lawyer.post(f"{LAWYERS}/{duplicate_application}/approve", headers=admin_auth)
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["error"]["code"] == "BAR_CARD_NUMBER_TAKEN"
        assert rows("user_db", "SELECT role::text FROM users WHERE id = %s", (conflict,)) == [("citizen",)]
        assert rows("lawyer_db", "SELECT status FROM lawyer_applications WHERE id = %s",
                    (duplicate_application,)) == [("pending",)]

        approved = response_data(lawyer.post(f"{LAWYERS}/{application}/approve", headers=admin_auth), 200)
        assert approved["approval_status"] == "approved"
    assert rows("user_db", "SELECT role::text FROM users WHERE id = %s", (candidate,)) == [("lawyer",)]
    assert rows("lawyer_db", "SELECT status FROM lawyer_applications WHERE id = %s", (application,)) == [("approved",)]

    def approved_event():
        result = rows("lawyer_db", """
            SELECT id, published_at FROM event_outbox WHERE routing_key = 'lawyer.approved'
            AND payload_json::jsonb->'payload'->>'user_id' = %s
        """, (str(candidate),))
        assert len(result) == 1, result
        return result[0][0] if result[0][1] else None

    event_id = eventually(approved_event, description="confirmed lawyer.approved event")
    notifications = eventually(
        lambda: rows("notification_db", "SELECT id FROM notifications WHERE user_id = %s AND event_type = 'lawyer.approved'",
                     (str(candidate),)),
        description="notification hub persist lawyer.approved",
    )
    assert notifications == [(f"notif-{event_id}",)]

    consultation_id = uuid4()
    with connect("consultation_db") as db:
        db.execute("""
            INSERT INTO consultations (id, citizen_user_id, lawyer_user_id, source, question, status, accepted_at)
            VALUES (%s, %s, %s, 'direct', 'Synthetic consultation', 'in_progress', now())
        """, (consultation_id, citizen, candidate))
    path = f"{CONSULTATIONS}/{consultation_id}/messages"
    lawyer_auth = auth_headers(live_services.keys, candidate, "lawyer")
    with live_services.client("consultation") as consultation, live_services.client("user") as users:
        response_data(consultation.post(path, json={"content": "Before locking"}, headers=lawyer_auth), 201)
        response_data(users.post(f"/api/v1/users/{candidate}/lock", headers=admin_auth,
                                 json={"reason": "Synthetic administrative lock", "duration_days": 1}), 200)

        def closed():
            result = rows("consultation_db", "SELECT status, close_reason FROM consultations WHERE id = %s",
                          (consultation_id,))
            return result == [("closed", "locked")]

        eventually(closed, description="consultation closed by user.blocked")
        cache_trace = [json.loads(line) for line in trace.read_text().splitlines()]
        assert {"user": str(candidate), "before": 1, "after": 0} in cache_trace

        def revoked():
            response = consultation.post(path, json={"content": "After locking"}, headers=lawyer_auth)
            return response if response.status_code == 401 else None

        assert eventually(revoked, description="old consultation JWT is revoked", timeout=5).status_code == 401
    assert rows("user_db", "SELECT status::text FROM users WHERE id = %s", (candidate,)) == [("locked",)]
    published = rows("user_db", """
        SELECT published_at IS NOT NULL FROM event_outbox WHERE routing_key = 'user.blocked'
        AND payload_json::jsonb->'payload'->>'user_id' = %s
    """, (str(candidate),))
    assert published == [(True,)]
    assert rows("consultation_db", "SELECT content FROM consultation_messages WHERE consultation_id = %s",
                (consultation_id,)) == [("Before locking",)]


def test_recipient_lookup_uses_two_user_selects_for_150_rows(live_services, monkeypatch):
    live_services.start("user")
    active = [uuid4() for _ in range(147)]
    expired, locked, deleted = uuid4(), uuid4(), uuid4()
    ids = [*active[:99], expired, *active[99:], locked, deleted]
    assert len(ids) == 150
    with connect("user_db") as db:
        with db.cursor() as cursor:
            cursor.executemany("INSERT INTO users (id, email) VALUES (%s, %s)",
                               [(uid, f"{uid}@example.test") for uid in ids])
        db.execute("UPDATE users SET status = 'locked', locked_until = now() - interval '1 day' WHERE id = %s",
                   (expired,))
        db.execute("UPDATE users SET status = 'locked', locked_until = now() + interval '1 day' WHERE id = %s",
                   (locked,))
        db.execute("UPDATE users SET deleted_at = now() WHERE id = %s", (deleted,))

    def select_count():
        result = rows("user_db", r"""
            SELECT COALESCE(sum(calls), 0) FROM pg_stat_statements
            WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
              AND query ~* '^select\s' AND query ~* '\mfrom\s+(public\.)?users\M'
        """)
        return result[0][0]

    before = select_count()
    request = SimpleNamespace(headers=httpx.Headers({"X-Request-ID": "test-l2-recipient-batches"}))
    monkeypatch.setenv("INTERNAL_TOKEN", INTERNAL_TOKEN)
    monkeypatch.setenv("USER_SERVICE_URL", live_services.url("user"))
    with service_module("notification", "app.services.recipient_service") as recipients:
        async def scenario():
            try:
                with pytest.raises(recipients.ReportDomainError) as result:
                    await recipients.validate_recipients(list(map(str, ids)), "citizen", request)
                assert (result.value.status_code, result.value.code) == (422, "INVALID_RECIPIENT")
                assert select_count() - before == 2
                valid = await recipients.validate_recipients(list(map(str, [*active, expired])), "citizen", request)
                assert [item["id"] for item in valid] == list(map(str, [*active, expired]))
                for uid in (locked, deleted):
                    with pytest.raises(recipients.ReportDomainError) as invalid:
                        await recipients.validate_recipients([str(uid)], "citizen", request)
                    assert (invalid.value.status_code, invalid.value.code) == (422, "INVALID_RECIPIENT")
            finally:
                await recipients.close_http_client()

        asyncio.run(scenario())

    assert rows("user_db", "SELECT status::text, locked_until FROM users WHERE id = %s", (expired,)) == [
        ("active", None),
    ]
    assert rows("user_db", "SELECT status::text FROM users WHERE id = %s", (locked,)) == [("locked",)]
