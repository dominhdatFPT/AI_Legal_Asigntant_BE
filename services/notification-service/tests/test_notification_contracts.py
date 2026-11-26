import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from common_auth import CurrentUser
from common_auth.fastapi_dep import get_current_user
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import notification_store as store
from app.core.realtime_broker import realtime_broker
from app.main import app
from app.services import report_service


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "NOTIFICATION_DATABASE_URL",
        f"sqlite:///{tmp_path / 'notifications.db'}",
    )
    monkeypatch.setenv("ATTACHMENTS_DIR", str(tmp_path / "attachments"))
    monkeypatch.setenv("RABBITMQ_ENABLED", "false")
    monkeypatch.setenv("RUN_NOTIFICATION_HUB_IN_API", "false")
    monkeypatch.setattr(store, "_engine", None)
    monkeypatch.setattr(store, "_ro_engine", None)
    monkeypatch.setattr(store, "_SessionFactory", None)
    with report_service._catalog_lock:
        report_service._catalog_cache.clear()

    yield

    app.dependency_overrides.clear()
    if store._engine:
        store._engine.dispose()


def as_user(user_id, role):
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=user_id, role=role, jti=f"jti-{user_id}"
    )


def notification(nid, created_at, user_id="u1"):
    return {
        "id": nid,
        "user_id": user_id,
        "event_type": "test.created",
        "title": "Test notification",
        "message": "Notification test content",
        "created_at": created_at,
    }


def test_unread_count_exceeds_200():
    now = datetime.now(timezone.utc).isoformat()
    for index in range(205):
        store.save_notification(
            notification(f"notif-{index:04}", now)
        )

    as_user("u1", "citizen")
    with TestClient(app) as client:
        response = client.get("/api/v1/notifications/unread-count")
        assert response.status_code == 200
        assert response.json()["data"] == {
            "count": 205,
            "unread_count": 205,
        }

        response = client.post("/api/v1/notifications/read-all")
        assert response.json()["data"]["updated_count"] == 205
        assert client.get(
            "/api/v1/notifications/unread-count"
        ).json()["data"]["count"] == 0


def test_cursor_does_not_skip_equal_timestamps():
    now = datetime.now(timezone.utc).isoformat()
    for index in range(15):
        store.save_notification(
            notification(f"notif-{index:04}", now)
        )

    as_user("u1", "citizen")
    with TestClient(app) as client:
        first = client.get(
            "/api/v1/notifications", params={"limit": 10}
        ).json()["data"]
        second = client.get(
            "/api/v1/notifications",
            params={
                "limit": 10,
                "before": first["next_before"],
                "before_id": first["next_before_id"],
            },
        ).json()["data"]

    ids1 = {item["id"] for item in first["items"]}
    ids2 = {item["id"] for item in second["items"]}

    assert first["has_more"] is True
    assert second["has_more"] is False
    assert not ids1.intersection(ids2)
    assert len(ids1 | ids2) == 15


def test_hidden_groups_do_not_consume_page_limit():
    now = datetime.now(timezone.utc).isoformat()
    for index in range(15):
        store.save_group_notification({
            **notification(f"grp-{index:04}", now),
            "audience": "citizen",
        })

    for index in range(5, 15):
        assert store.hide_notification(
            "u1", f"grp-{index:04}", "citizen"
        )

    items = store.get_user_notifications(
        "u1", limit=5, user_role="citizen"
    )
    assert len(items) == 5
    assert all(item["id"] < "grp-0005" for item in items)


def test_attachment_owner_admin_and_other_user():
    png_bytes = b"\x89PNG\r\n\x1a\nattachment-test-content"
    attachment = report_service.save_attachment(
        "owner",
        "evidence.png",
        "image/png",
        png_bytes,
    )
    attachment_id = attachment["attachment_id"]

    with TestClient(app) as client:
        as_user("owner", "citizen")
        response = client.get(
            f"/api/v1/reports/attachments/{attachment_id}"
        )
        assert response.status_code == 200
        assert response.content == png_bytes

        as_user("other", "lawyer")
        assert client.get(
            f"/api/v1/reports/attachments/{attachment_id}"
        ).status_code == 404

        as_user("admin", "admin")
        assert client.get(
            f"/api/v1/admin/reports/attachments/{attachment_id}"
        ).status_code == 200


@pytest.mark.parametrize(
    "extra",
    [
        {"scheduled_at": "2030-01-01T00:00:00Z"},
        {"expires_at": "2030-01-01T00:00:00Z"},
        {"recipient": "someone@example.com"},
    ],
)
def test_removed_or_ambiguous_fields_are_rejected(extra):
    as_user("admin", "admin")
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/admin/broadcasts",
            json={
                "title": "Test announcement",
                "content": "Test announcement content",
                **extra,
            },
        )
    assert response.status_code == 422


def test_personal_broadcast_persists_and_routes_only_to_user():
    async def _test():
        from app.schemas.reports import BroadcastCreateRequest
        from app.services.broadcast_service import create_broadcast

        target = str(uuid4())
        other = str(uuid4())
        target_sub = realtime_broker.subscribe(target, "citizen")
        other_sub = realtime_broker.subscribe(other, "citizen")

        try:
            result = await create_broadcast(
                "admin",
                BroadcastCreateRequest(
                    title="Personal announcement",
                    content="Only the selected account receives this.",
                    recipient_id=target,
                    audience="citizen",
                ),
            )

            assert result["status"] == "sent"
            assert result["group_notification_id"] is None

            event = await asyncio.wait_for(target_sub[2].get(), 1)
            assert event["id"] == result["personal_notification_id"]
            assert other_sub[2].empty()

            assert len(store.get_user_notifications(
                target, user_role="citizen"
            )) == 1
            assert store.get_user_notifications(
                other, user_role="citizen"
            ) == []

            with store.get_session() as session:
                assert session.query(store.BroadcastRecord).count() == 1
                assert session.query(
                    store.GroupNotificationRecord
                ).count() == 0
                assert session.query(
                    store.NotificationOutboxRecord
                ).count() == 1
        finally:
            realtime_broker.unsubscribe(target_sub)
            realtime_broker.unsubscribe(other_sub)

    asyncio.run(_test())

def test_multiple_personal_broadcast_persists_and_routes_to_all_recipients():
    async def _test():
        from app.schemas.reports import BroadcastCreateRequest
        from app.services.broadcast_service import create_broadcast

        target1 = str(uuid4())
        target2 = str(uuid4())
        other = str(uuid4())
        sub1 = realtime_broker.subscribe(target1, "citizen")
        sub2 = realtime_broker.subscribe(target2, "lawyer")
        sub_other = realtime_broker.subscribe(other, "citizen")

        try:
            result = await create_broadcast(
                "admin",
                BroadcastCreateRequest(
                    title="Multiple announcement",
                    content="Both selected accounts receive this announcement.",
                    recipient_ids=[target1, target2],
                    audience="all",
                ),
            )

            assert result["status"] == "sent"
            assert result["group_notification_id"] is None
            assert result["recipient_id"] == "2 người nhận"

            ev1 = await asyncio.wait_for(sub1[2].get(), 1)
            assert ev1["user_id"] == target1
            ev2 = await asyncio.wait_for(sub2[2].get(), 1)
            assert ev2["user_id"] == target2
            assert sub_other[2].empty()

            assert len(store.get_user_notifications(target1, user_role="citizen")) == 1
            assert len(store.get_user_notifications(target2, user_role="lawyer")) == 1
            assert store.get_user_notifications(other, user_role="citizen") == []
        finally:
            realtime_broker.unsubscribe(sub1)
            realtime_broker.unsubscribe(sub2)
            realtime_broker.unsubscribe(sub_other)

    asyncio.run(_test())


def test_notifications_feed_restful_params_no_null_fields_and_expiry():
    now = datetime.now(timezone.utc)
    with TestClient(app) as client:
        as_user("empty-user", "citizen")
        empty_res = client.get("/api/v1/notifications")
        assert empty_res.status_code == 200
        assert empty_res.headers["cache-control"] == "private, no-store"
        assert empty_res.json()["data"] == {"items": [], "has_more": False, "unread_count": 0}

        store.save_notification(notification("n-unread", now.isoformat(), user_id="u-clean"))
        store.save_notification({
            **notification("n-read-src", (now - timedelta(minutes=1)).isoformat(), user_id="u-clean"),
            "source_type": "report",
            "source_id": "rpt-1",
            "is_read": True,
        })
        store.save_group_notification({
            **notification("grp-expired", (now - timedelta(minutes=2)).isoformat()),
            "audience": "all",
            "expires_at": (now - timedelta(minutes=1)).isoformat(),
        })
        store.save_group_notification({
            **notification("grp-active", (now - timedelta(minutes=3)).isoformat()),
            "audience": "citizen",
            "expires_at": (now + timedelta(hours=1)).isoformat(),
        })

        as_user("u-clean", "citizen")
        feed_res = client.get("/api/v1/notifications")
        assert feed_res.status_code == 200
        data = feed_res.json()["data"]
        assert data["has_more"] is False
        assert "next_before" not in data
        assert "next_before_id" not in data
        assert data["unread_count"] == 2
        assert [item["id"] for item in data["items"]] == ["n-unread", "n-read-src", "grp-active"]

        by_id = {item["id"]: item for item in data["items"]}
        assert "source_type" not in by_id["n-unread"]
        assert "source_id" not in by_id["n-unread"]
        assert "audience" not in by_id["n-unread"]
        assert by_id["n-read-src"]["source_type"] == "report"
        assert by_id["n-read-src"]["source_id"] == "rpt-1"
        assert "user_id" not in by_id["grp-active"]
        assert "source_type" not in by_id["grp-active"]

        unread_only = client.get("/api/v1/notifications", params={"is_read": "false", "include_unread_count": "false"}).json()["data"]
        assert "unread_count" not in unread_only
        assert [item["id"] for item in unread_only["items"]] == ["n-unread", "grp-active"]

        read_only = client.get("/api/v1/notifications", params={"is_read": "true"}).json()["data"]
        assert [item["id"] for item in read_only["items"]] == ["n-read-src"]


def test_admin_broadcasts_restful_pagination_summary_and_read_counts():
    async def _seed():
        from app.schemas.reports import BroadcastCreateRequest
        from app.services.broadcast_service import create_broadcast

        grp = await create_broadcast(
            "admin-1",
            BroadcastCreateRequest(
                title="System notice",
                content="Scheduled maintenance tonight at 23:00.",
                audience="all",
            ),
        )
        personal = await create_broadcast(
            "admin-1",
            BroadcastCreateRequest(
                title="Personal notice",
                content="Direct message to two recipients only.",
                recipient_ids=["u-a", "u-b"],
                audience="all",
            ),
        )
        return grp, personal

    grp_bc, personal_bc = asyncio.run(_seed())
    assert "recipient_count" not in grp_bc["stats"]
    assert "recipient_ids" not in grp_bc["stats"]

    with TestClient(app) as client:
        as_user("reader-1", "citizen")
        assert client.patch(f"/api/v1/notifications/{grp_bc['group_notification_id']}/read").status_code == 200

        as_user("u-a", "citizen")
        u_a_id = client.get("/api/v1/notifications").json()["data"]["items"][0]["id"]
        assert client.patch(f"/api/v1/notifications/{u_a_id}/read").status_code == 200

        as_user("u-b", "citizen")
        u_b_id = client.get("/api/v1/notifications").json()["data"]["items"][0]["id"]
        assert client.patch(f"/api/v1/notifications/{u_b_id}/read").status_code == 200
        assert client.delete(f"/api/v1/notifications/{u_b_id}").status_code == 200

        as_user("admin-1", "admin")
        p1 = client.get("/api/v1/admin/broadcasts", params={"page": 1, "page_size": 1})
        assert p1.status_code == 200
        assert p1.headers["cache-control"] == "private, no-store"
        d1 = p1.json()["data"]
        assert d1["pagination"] == {"page": 1, "page_size": 1, "total": 2, "total_pages": 2}
        assert d1["summary"] == {"sent": 2}
        assert len(d1["items"]) == 1
        assert d1["items"][0]["id"] == personal_bc["id"]
        assert d1["items"][0]["read_count"] == 1

        p2 = client.get("/api/v1/admin/broadcasts", params={"page": 2, "page_size": 1}).json()["data"]
        assert len(p2["items"]) == 1
        assert p2["items"][0]["id"] == grp_bc["id"]
        assert p2["items"][0]["read_count"] == 1
        assert "recipient_count" not in p2["items"][0]["stats"]

        detail = client.get(f"/api/v1/admin/broadcasts/{personal_bc['id']}").json()["data"]
        assert detail["read_count"] == 1


def test_attachment_rejects_spoofed_magic_bytes_and_oversized_files():
    as_user("citizen-1", "citizen")
    with TestClient(app) as client:
        bad = client.post(
            "/api/v1/reports/attachments",
            files={"file": ("evil.png", b"<html><script>alert(1)</script></html>", "image/png")},
        )
        assert bad.status_code == 415
        assert bad.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"

        empty = client.post(
            "/api/v1/reports/attachments",
            files={"file": ("empty.png", b"", "image/png")},
        )
        assert empty.status_code == 422
        assert empty.json()["error"]["code"] == "INVALID_FILE_SIZE"

        too_big = client.post(
            "/api/v1/reports/attachments",
            files={"file": ("big.png", b"\x89PNG\r\n\x1a\n" + (b"0" * (report_service.MAX_ATTACHMENT_BYTES + 1)), "image/png")},
        )
        assert too_big.status_code == 413
        assert too_big.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_report_catalog_returns_nested_reasons_and_quick_error_reasons():
    as_user("citizen-1", "citizen")
    with TestClient(app) as client:
        ticket_cat = client.get("/api/v1/reports/catalog", params={"kind": "ticket"})
        assert ticket_cat.status_code == 200
        assert ticket_cat.headers["cache-control"] == "private, no-store"
        tdata = ticket_cat.json()["data"]
        assert all(isinstance(c.get("reasons"), list) and len(c["reasons"]) > 0 for c in tdata["categories"])

        qe_cat = client.get("/api/v1/reports/catalog", params={"kind": "quick_error"})
        assert qe_cat.status_code == 200
        qdata = qe_cat.json()["data"]
        assert len(qdata["reasons"]) > 0
        assert all(r["kind"] == "quick_error" for r in qdata["reasons"])


def test_admin_reports_filters_date_range_search_sort_pagination_and_summary():
    from app.schemas.reports import TicketCreateRequest

    async def _seed():
        r1 = await report_service.create_ticket_report(
            "citizen-a",
            "citizen",
            TicketCreateRequest(
                category_code="SUGGESTION",
                reason_code="SUG_FEATURE",
                title="Đề xuất giao diện tối cho trang hỗ trợ",
                description="Mong muốn thêm từ khóa timkiem_mota để lọc dễ hơn trên bảng quản trị.",
            ),
        )
        r2 = await report_service.create_ticket_report(
            "lawyer-b",
            "lawyer",
            TicketCreateRequest(
                category_code="SECURITY",
                reason_code="SEC_DATA_LEAK",
                title="Cảnh báo rò rỉ phiên người dùng nghiêm trọng",
                description="Phát hiện tài khoản thấy nhầm phiên khác trên màn hình cấu hình bảo mật.",
            ),
        )
        return r1, r2

    r_low, r_crit = asyncio.run(_seed())

    with store.get_session() as session:
        rec_low = session.get(store.ReportRecord, r_low["id"])
        rec_low.created_at = datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)
        rec_crit = session.get(store.ReportRecord, r_crit["id"])
        rec_crit.created_at = datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)
        session.commit()

    as_user("admin-1", "admin")
    with TestClient(app) as client:
        # sort=priority returns critical before low even with default/mixed timestamps
        sorted_res = client.get("/api/v1/admin/reports", params={"sort": "priority", "page": 1, "page_size": 1})
        assert sorted_res.status_code == 200
        assert sorted_res.headers["cache-control"] == "private, no-store"
        sd = sorted_res.json()["data"]
        assert sd["pagination"] == {"page": 1, "page_size": 1, "total": 2, "total_pages": 2}
        assert sd["items"][0]["id"] == r_crit["id"]

        # q searches description too
        q_res = client.get("/api/v1/admin/reports", params={"q": "timkiem_mota"}).json()["data"]
        assert [i["id"] for i in q_res["items"]] == [r_low["id"]]

        # Date range filters in ICT (2026-11-10 excludes 2026-11-01)
        range_res = client.get("/api/v1/admin/reports", params={"from": "2026-11-10", "to": "2026-11-10"}).json()["data"]
        assert [i["id"] for i in range_res["items"]] == [r_crit["id"]]

        bad_range = client.get("/api/v1/admin/reports", params={"from": "2026-11-15", "to": "2026-11-10"})
        assert bad_range.status_code == 422
        assert bad_range.json()["error"]["code"] == "INVALID_DATE_RANGE"

        summary = client.get("/api/v1/admin/reports/summary")
        assert summary.status_code == 200
        assert summary.headers["cache-control"] == "private, no-store"
        sdata = summary.json()["data"]
        assert sdata["pending"] == 2
        assert sdata["critical_open"] == 1
        assert sdata["high_open"] == 0

        detail = client.get(f"/api/v1/admin/reports/{r_crit['id']}")
        assert detail.status_code == 200
        assert len(detail.json()["data"]["history"]) == 1


def test_validate_recipient_rejects_non_uuid_before_http(monkeypatch):
    from starlette.requests import Request
    from app.services import recipient_service

    called = False

    class FakeClient:
        is_closed = False

        async def get(self, *args, **kwargs):
            nonlocal called
            called = True
            raise AssertionError("HTTP should not be called for non-UUID recipient")

    monkeypatch.setattr(recipient_service, "_get_http_client", lambda: FakeClient())
    req = Request({"type": "http", "headers": []})

    for bad_id in ("me", "../admin", "not-a-uuid"):
        with pytest.raises(report_service.ReportDomainError) as exc_info:
            asyncio.run(recipient_service.validate_recipient(bad_id, "all", req))
        assert exc_info.value.status_code == 422
        assert exc_info.value.code == "INVALID_RECIPIENT"
    assert called is False


def test_mark_as_read_and_hide_propagate_db_outage_as_503_and_enforce_expiry(monkeypatch):
    from sqlalchemy.exc import OperationalError

    now = datetime.now(timezone.utc)
    store.save_group_notification({
        **notification("grp-expired-test", (now - timedelta(minutes=2)).isoformat()),
        "audience": "all",
        "expires_at": (now - timedelta(minutes=1)).isoformat(),
    })

    as_user("u1", "citizen")
    with TestClient(app) as client:
        assert client.patch("/api/v1/notifications/grp-expired-test/read").status_code == 404
        assert client.delete("/api/v1/notifications/grp-expired-test").status_code == 404

        def broken_engine():
            raise OperationalError("SELECT 1", {}, Exception("db unreachable"))

        monkeypatch.setattr(store, "get_engine", broken_engine)
        err_read = client.patch("/api/v1/notifications/n-1/read")
        assert err_read.status_code == 503
        assert err_read.json()["error"]["code"] == "DATABASE_UNAVAILABLE"

        err_hide = client.delete("/api/v1/notifications/n-1")
        assert err_hide.status_code == 503
        assert err_hide.json()["error"]["code"] == "DATABASE_UNAVAILABLE"


def test_broadcast_default_empty_action_url_recipient_labels_and_validation_envelope(monkeypatch):
    from app.services import recipient_service

    uid = str(uuid4())

    async def fake_validate_recipients(recipient_ids, audience, request):
        return [{"id": uid, "full_name": "Nguyễn Văn A", "email": "a@example.com", "role": "citizen", "status": "active"}]

    monkeypatch.setattr("app.api.admin_broadcasts.validate_recipients", fake_validate_recipients)

    as_user("admin-1", "admin")
    with TestClient(app) as client:
        res = client.post(
            "/api/v1/admin/broadcasts",
            json={
                "title": "Thông báo cá nhân",
                "content": "Nội dung gửi đích danh không gắn link mặc định.",
                "audience": "all",
                "recipient_ids": [uid, uid],
            },
            headers={"X-Request-ID": "req-custom-123"},
        )
        assert res.status_code == 202
        assert res.headers["x-request-id"] == "req-custom-123"
        data = res.json()["data"]
        assert data["action_url"] == ""
        assert data["stats"]["recipient_labels"] == ["Nguyễn Văn A"]

        bad = client.post(
            "/api/v1/admin/broadcasts",
            json={"title": "  ab  ", "content": "123"},
        )
        assert bad.status_code == 422
        body = bad.json()
        assert body["data"] is None
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert len(body["error"]["details"]) >= 1
        assert any("Tiêu đề phải có ít nhất 5 ký tự." in d["message"] for d in body["error"]["details"])
        assert body["request_id"]
        assert body["request_id"] == bad.headers["x-request-id"]
        assert bad.headers["cache-control"] == "private, no-store"


def test_admin_report_mutation_request_id_matches_header_and_body():
    with TestClient(app) as client:
        as_user("citizen-req", "citizen")
        created = client.post(
            "/api/v1/reports/tickets",
            json={
                "category_code": "SUGGESTION",
                "reason_code": "SUG_FEATURE",
                "title": "Lỗi hiển thị trang hồ sơ",
                "description": "Nội dung mô tả lỗi chi tiết để admin xử lý.",
            },
        )
        assert created.status_code == 201
        assert created.json()["request_id"] == created.headers["x-request-id"]
        rid = created.json()["data"]["id"]

        as_user("admin-req", "admin")
        claimed = client.post(f"/api/v1/admin/reports/{rid}/claim", json={"version": 1})
        assert claimed.status_code == 200
        assert claimed.json()["request_id"] == claimed.headers["x-request-id"]

        released = client.post(f"/api/v1/admin/reports/{rid}/release", json={"version": 2})
        assert released.status_code == 200
        assert released.json()["request_id"] == released.headers["x-request-id"]

        claimed2 = client.post(f"/api/v1/admin/reports/{rid}/claim", json={"version": 3})
        assert claimed2.status_code == 200
        assert claimed2.json()["request_id"] == claimed2.headers["x-request-id"]

        accepted = client.post(
            f"/api/v1/admin/reports/{rid}/accept",
            json={"version": 4, "note": "Đã tiếp nhận kiểm tra"},
        )
        assert accepted.status_code == 200
        assert accepted.json()["request_id"] == accepted.headers["x-request-id"]

        resolved = client.post(
            f"/api/v1/admin/reports/{rid}/resolve",
            json={"version": 5, "note": "Đã khắc phục hoàn tất"},
        )
        assert resolved.status_code == 200
        assert resolved.json()["request_id"] == resolved.headers["x-request-id"]
