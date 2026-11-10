"""Kiểm thử toàn diện cho hệ thống Report (Ticket & Quick Error), Xử lý của Admin (Accept/Reject/Resolve)
và Thông báo toàn hệ thống (Broadcast cho toàn bộ user + lawyer + chính admin đó)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from common_auth import CurrentUser
from common_auth.fastapi_dep import get_current_user
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import notification_store  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    monkeypatch.setenv("NOTIFICATION_DATABASE_URL", f"sqlite:///{tmp_path / 'reports_notif.db'}")
    monkeypatch.setenv("RABBITMQ_ENABLED", "false")
    monkeypatch.setenv("RUN_NOTIFICATION_HUB_IN_API", "false")
    monkeypatch.setattr(notification_store, "_engine", None)
    monkeypatch.setattr(notification_store, "_SessionFactory", None)
    yield
    app.dependency_overrides.clear()


def _as_user(user_id: str, role: str) -> None:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=user_id, role=role, jti=f"jti-{user_id}"
    )


def test_catalog_filtered_by_role():
    client = TestClient(app)

    # Citizen không thấy nhóm LAWYER_PROGRAM nhưng thấy CONSULTATION / AI_ASSISTANT
    _as_user("citizen-1", "citizen")
    r_cit = client.get("/api/v1/reports/catalog")
    assert r_cit.status_code == 200
    cat_codes_cit = {c["code"] for c in r_cit.json()["data"]["categories"]}
    assert "AI_ASSISTANT" in cat_codes_cit
    assert "LAWYER_PROGRAM" not in cat_codes_cit

    # Lawyer thấy cả LAWYER_PROGRAM
    _as_user("lawyer-1", "lawyer")
    r_law = client.get("/api/v1/reports/catalog")
    assert r_law.status_code == 200
    cat_codes_law = {c["code"] for c in r_law.json()["data"]["categories"]}
    assert "LAWYER_PROGRAM" in cat_codes_law


def test_ticket_report_and_admin_accept_notifies_both_sides():
    client = TestClient(app)

    # 1. Người dùng tạo Ticket Report
    _as_user("citizen-10", "citizen")
    res = client.post(
        "/api/v1/reports/tickets",
        json={
            "category_code": "AI_ASSISTANT",
            "reason_code": "AI_WRONG_ANSWER",
            "title": "AI trả lời sai thời hạn đăng ký biến động đất đai",
            "description": "Trợ lý AI trích dẫn điều khoản cũ, thời hạn thực tế là 30 ngày.",
            "target_type": "ai_message",
            "target_id": "msg-999",
        },
    )
    assert res.status_code == 201
    report = res.json()["data"]
    report_id = report["id"]
    assert report["code"].startswith("RPT-")
    assert report["status"] == "pending"
    assert report["priority"] == "high"

    # Người dùng nhận được thông báo xác nhận đã tiếp nhận report
    notifs_cit = client.get("/api/v1/notifications").json()["data"]["items"]
    assert any(report["code"] in n["title"] and n["event_type"] == "report.created" for n in notifs_cit)

    # 2. Admin nhận được thông báo có báo cáo mới (group notification audience='admin')
    _as_user("admin-1", "admin")
    notifs_admin = client.get("/api/v1/notifications").json()["data"]["items"]
    assert any(report["code"] in n["title"] and n["audience"] == "admin" for n in notifs_admin)

    # 3. Admin nhận xử lý (claim) rồi Chấp nhận (accept)
    claim_res = client.post(f"/api/v1/admin/reports/{report_id}/claim", json={"version": 1})
    assert claim_res.status_code == 200
    assert claim_res.json()["data"]["status"] == "in_review"
    assert claim_res.json()["data"]["version"] == 2

    accept_res = client.post(
        f"/api/v1/admin/reports/{report_id}/accept",
        json={"version": 2, "note": "Cảm ơn bạn, đội ngũ RAG đang cập nhật lại văn bản luật mới."},
    )
    assert accept_res.status_code == 200
    assert accept_res.json()["data"]["status"] == "accepted"

    # 4. Người dùng nhận được thông báo báo cáo đã được chấp nhận
    _as_user("citizen-10", "citizen")
    notifs_cit_after = client.get("/api/v1/notifications").json()["data"]["items"]
    accepted_notifs = [n for n in notifs_cit_after if n["event_type"] == "report.accepted"]
    assert len(accepted_notifs) == 1
    assert "đã được chấp nhận" in accepted_notifs[0]["title"]
    assert "cập nhật lại văn bản" in accepted_notifs[0]["message"]


def test_quick_error_report_deduplication_and_admin_reject():
    client = TestClient(app)

    # Luật sư đang thao tác bị lỗi -> bấm chữ "Báo lỗi" (Quick Error)
    _as_user("lawyer-22", "lawyer")
    payload = {
        "reason_code": "QE_SAVE_FAILED",
        "note": "Bấm nút nộp hồ sơ mà hệ thống báo lỗi 502",
        "feature_code": "PROCEDURE_SUBMIT",
        "context": {
            "page": "application-submission",
            "route": "/application-submission?step=3",
            "error_code": "UPSTREAM_ERROR",
            "http_status": 502,
            "request_id": "req-abc-123",
            "authorization": "Bearer secret-jwt-should-be-redacted",
        },
    }
    r1 = client.post("/api/v1/reports/quick-errors", json=payload)
    assert r1.status_code == 201
    data1 = r1.json()["data"]
    assert data1["duplicated"] is False
    assert data1["occurrence_count"] == 1
    # Token nhạy cảm trong context phải bị che ([REDACTED])
    assert data1["context"]["authorization"] == "[REDACTED]"

    # Bấm lần 2 trong vòng 10 phút với cùng lỗi -> gom trùng (200 OK, occurrence_count = 2)
    r2 = client.post("/api/v1/reports/quick-errors", json=payload)
    assert r2.status_code == 200
    data2 = r2.json()["data"]
    assert data2["id"] == data1["id"]
    assert data2["duplicated"] is True
    assert data2["occurrence_count"] == 2

    # Admin vào Từ chối (Reject) kèm lý do
    _as_user("admin-2", "admin")
    rej_res = client.post(
        f"/api/v1/admin/reports/{data1['id']}/reject",
        json={
            "version": 1,
            "reject_reason_code": "REJ_DUPLICATE",
            "note": "Sự cố máy chủ đã được ghi nhận ở mã báo cáo trung tâm.",
        },
    )
    assert rej_res.status_code == 200
    assert rej_res.json()["data"]["status"] == "rejected"

    # Luật sư nhận được thông báo từ chối kèm lý do
    _as_user("lawyer-22", "lawyer")
    notifs_lawyer = client.get("/api/v1/notifications").json()["data"]["items"]
    rejected_notifs = [n for n in notifs_lawyer if n["event_type"] == "report.rejected"]
    assert len(rejected_notifs) == 1
    assert "bị từ chối" in rejected_notifs[0]["title"]
    assert "Trùng với báo cáo đã có" in rejected_notifs[0]["message"]


def test_admin_broadcast_notifies_all_users_and_admin_self():
    client = TestClient(app)

    # Admin phát thông báo cho toàn bộ hệ thống (audience='all')
    _as_user("admin-99", "admin")
    bc_res = client.post(
        "/api/v1/admin/broadcasts",
        json={
            "title": "Thông báo bảo trì nâng cấp AI Pháp lý",
            "content": "Hệ thống sẽ nâng cấp mô hình RAG vào 23:00 tối nay.",
            "audience": "all",
            "priority": "high",
            "channels": ["in_app"],
            "action_url": "/support",
        },
    )
    assert bc_res.status_code == 202
    bc_data = bc_res.json()["data"]
    grp_notif_id = bc_data["group_notification_id"]
    assert grp_notif_id.startswith("grp-bc-")

    # 1. Chính Admin vừa gửi cũng nhận được thông báo đó
    admin_notifs = client.get("/api/v1/notifications").json()["data"]["items"]
    assert any(n["id"] == grp_notif_id and not n["is_read"] for n in admin_notifs)

    # 2. Luật sư nhận được thông báo
    _as_user("lawyer-55", "lawyer")
    lawyer_notifs = client.get("/api/v1/notifications").json()["data"]["items"]
    assert any(n["id"] == grp_notif_id and not n["is_read"] for n in lawyer_notifs)

    # 3. Người dùng (citizen) nhận được thông báo và đánh dấu đã đọc độc lập
    _as_user("citizen-77", "citizen")
    citizen_notifs = client.get("/api/v1/notifications").json()["data"]["items"]
    assert any(n["id"] == grp_notif_id and not n["is_read"] for n in citizen_notifs)

    # Citizen đánh dấu đã đọc -> chỉ Citizen chuyển thành is_read=True, Admin vẫn còn unread
    read_res = client.patch(f"/api/v1/notifications/{grp_notif_id}/read")
    assert read_res.status_code == 200
    assert read_res.json()["data"]["success"] is True

    citizen_notifs_after = client.get("/api/v1/notifications").json()["data"]["items"]
    target_cit = next(n for n in citizen_notifs_after if n["id"] == grp_notif_id)
    assert target_cit["is_read"] is True

    _as_user("admin-99", "admin")
    admin_notifs_after = client.get("/api/v1/notifications").json()["data"]["items"]
    target_adm = next(n for n in admin_notifs_after if n["id"] == grp_notif_id)
    assert target_adm["is_read"] is False
