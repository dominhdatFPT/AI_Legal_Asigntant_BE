import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import notification_store  # noqa: E402
from app.core.rabbitmq_notification_hub import format_notification, handle_event  # noqa: E402


@pytest.fixture(autouse=True)
def sqlite_store(monkeypatch, tmp_path):
    monkeypatch.setenv("NOTIFICATION_DATABASE_URL", f"sqlite:///{tmp_path / 'n.db'}")
    monkeypatch.setattr(notification_store, "_engine", None)
    monkeypatch.setattr(notification_store, "_SessionFactory", None)


def test_event_khong_can_bao_thi_bo_qua():
    assert format_notification("user.logged_out", {"user_id": "u1"}) is None
    assert format_notification("document.ocr.completed", {}) is None  # thiếu user_id


def test_event_giao_lai_khong_tao_thong_bao_trung():
    event = {"event_id": "evt-1", "event_type": "document.ocr.completed", "payload": {"user_id": "u1", "document_id": "d1"}}
    asyncio.run(handle_event("document.ocr.completed", event))
    asyncio.run(handle_event("document.ocr.completed", event))
    items = notification_store.get_user_notifications("u1")
    assert len(items) == 1 and items[0]["action_url"] == "/documents/d1"


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
