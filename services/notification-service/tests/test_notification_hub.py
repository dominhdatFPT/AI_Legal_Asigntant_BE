import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import notification_store  # noqa: E402
from app.core.rabbitmq_notification_hub import (  # noqa: E402
    format_admin_report_notification,
    format_notification,
    handle_event,
)


@pytest.fixture(autouse=True)
def sqlite_store(monkeypatch, tmp_path):
    monkeypatch.setenv("NOTIFICATION_DATABASE_URL", f"sqlite:///{tmp_path / 'n.db'}")
    monkeypatch.setattr(notification_store, "_engine", None)
    monkeypatch.setattr(notification_store, "_ro_engine", None)
    monkeypatch.setattr(notification_store, "_SessionFactory", None)


def test_event_khong_can_bao_thi_bo_qua():
    assert format_notification("user.logged_out", {"user_id": "u1"}) is None
    assert format_notification("document.ocr.completed", {}) is None  # thiếu user_id


def test_event_giao_lai_khong_tao_thong_bao_trung():
    event = {"event_id": "evt-1", "event_type": "document.ocr.completed", "payload": {"user_id": "u1", "document_id": "d1"}}
    asyncio.run(handle_event("document.ocr.completed", event))
    asyncio.run(handle_event("document.ocr.completed", event))
    items = notification_store.get_user_notifications("u1")
    assert len(items) == 1 and items[0]["action_url"] == "/detail-information"


def test_hub_routes_match_fe_and_avoid_none_or_nulls():
    doc_ok = format_notification("document.ocr.completed", {"user_id": "u1"}, "e-doc")
    assert doc_ok["action_url"] == "/detail-information"
    assert "None" not in doc_ok["message"]
    assert "source_type" not in doc_ok
    assert "source_id" not in doc_ok

    con_ok = format_notification("contract.generated", {"user_id": "u1"}, "e-con")
    assert con_ok["action_url"] == "/user-dashboard"
    assert "NONE" not in con_ok["message"]

    proc_ok = format_notification("procedure.application.submitted", {"user_id": "u1"}, "e-pro")
    assert proc_ok["action_url"] == "/user-dashboard"
    assert "None" not in proc_ok["message"]

    law_ok = format_notification("lawyer.approved", {"user_id": "u1"}, "e-law")
    assert law_ok["action_url"] == "/user-dashboard"

    tpl_biz = format_notification("procedure.form_template.deleted", {"admin_id": "a1", "domain": "business"}, "e-tpl")
    assert tpl_biz["action_url"] == "/businessforms"
    assert "None" not in tpl_biz["message"]

    rpt_user = format_notification(
        "report.accepted",
        {"reporter_id": "u1", "report_id": "rpt-123", "code": "RPT-2026-000001"},
        "e-rpt",
    )
    assert rpt_user["action_url"] == "/support?tab=mine&report=rpt-123"
    assert rpt_user["source_type"] == "report"
    assert rpt_user["source_id"] == "rpt-123"

    rpt_admin = format_admin_report_notification(
        {"reporter_id": "u1", "reporter_role": "citizen", "report_id": "rpt-123", "code": "RPT-2026-000001", "priority": "critical"},
        "e-rpt",
    )
    assert rpt_admin["action_url"] == "/admin-reports?report=rpt-123"
    assert rpt_admin["icon"] == "alert-octagon"

    asyncio.run(
        handle_event(
            "notification.broadcast.requested",
            {
                "event_id": "e-bc",
                "event_type": "notification.broadcast.requested",
                "payload": {"title": "Bảo trì", "content": "Nội dung", "audience": "all"},
            },
        )
    )
    items = notification_store.get_user_notifications("u1", user_role="citizen")
    bc = next(i for i in items if i["id"] == "grp-e-bc")
    assert bc["action_url"] == ""
    assert "source_type" not in bc
    assert "source_id" not in bc


def test_loi_db_duoc_nem_ra_de_vao_dlq(monkeypatch):
    def broken(_):
        raise RuntimeError("db down")

    monkeypatch.setattr(notification_store, "save_notification", broken)
    with pytest.raises(RuntimeError):
        asyncio.run(handle_event("user.blocked", {"event_id": "e2", "payload": {"user_id": "u1", "reason": "spam"}}))


def test_nhieu_thread_cung_luu_mot_event_luc_khoi_dong():
    from concurrent.futures import ThreadPoolExecutor

    event = {"event_id": "evt-race", "event_type": "user.blocked", "payload": {"user_id": "u7", "reason": "x"}}
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda _: asyncio.run(handle_event("user.blocked", event)), range(8)))
    assert len(notification_store.get_user_notifications("u7")) == 1
