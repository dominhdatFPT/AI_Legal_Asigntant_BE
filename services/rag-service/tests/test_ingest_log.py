"""Nhật ký nạp (bảng ingest_log) và GET /api/v1/rag/documents/logs."""

import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.auth import TokenPayload
from app.core.database import get_db
from app.main import app
from app.models import IngestLog, LegalDocument
from app.services import document_approval, ingest_log
from app.services.chunking import chunk_legal_text
from app.services.embedding import EmbeddingError, FakeEmbedder

URL = "/api/v1/rag/documents"
CONTENT = "Điều 1. A\nNội dung điều một.\nĐiều 2. B\nNội dung điều hai."


def _user(role: str) -> TokenPayload:
    now = int(time.time())
    return TokenPayload(sub="admin-1", role=role, type="access", iss="test", iat=now, exp=now + 900)


@pytest.fixture
def client(db, monkeypatch):
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(document_approval, "_open_session", lambda: Session(db.get_bind(), expire_on_commit=False))
    monkeypatch.setattr(document_approval, "get_embedder", lambda: FakeEmbedder(768))
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def as_role(role: str) -> None:
    app.dependency_overrides[get_current_user] = lambda: _user(role)


@pytest.fixture
def admin(client):
    as_role("admin")
    return client


def create(client, content=CONTENT, **kw) -> str:
    body = {"title": "  Luật Mẫu  ", "doc_type": "luat", "content": content, **kw}
    r = client.post(URL, json=body)
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def rows(db) -> list[IngestLog]:
    db.expire_all()
    return list(db.scalars(select(IngestLog).order_by(IngestLog.created_at, IngestLog.id)).all())


def actions(db) -> list[str]:
    return [r.action for r in rows(db)]


def set_state(db, document_id: str, **fields) -> None:
    doc = db.get(LegalDocument, uuid.UUID(document_id))
    for k, v in fields.items():
        setattr(doc, k, v)
    db.commit()


# ── Ghi nhật ký ────────────────────────────────────────────────────────────────

def test_tao_nhap_ghi_dung_mot_dong_create_draft(admin, db):
    document_id = create(admin, doc_number="31/2024/QH15")
    (row,) = rows(db)
    assert row.action == "create_draft"
    assert (row.actor_user_id, row.actor_role) == ("admin-1", "admin")
    assert str(row.document_id) == document_id
    assert row.doc_number == "31/2024/QH15"
    assert row.title == "Luật Mẫu"
    assert row.created_at is not None
    assert (row.detail, row.chunk_count, row.duration_ms) == (None, None, None)


def test_duyet_thanh_cong_ghi_requested_roi_done(admin, db):
    document_id = create(admin, doc_number="1/2020/QH")
    assert admin.post(f"{URL}/{document_id}/approve").status_code == 202
    created, requested, done = rows(db)
    assert [created.action, requested.action, done.action] == ["create_draft", "approve_requested", "approve_done"]
    for row in (requested, done):
        assert (row.actor_user_id, row.actor_role) == ("admin-1", "admin")
        assert str(row.document_id) == document_id
        assert (row.doc_number, row.title) == ("1/2020/QH", "Luật Mẫu")
    assert (requested.chunk_count, requested.duration_ms) == (None, None)
    assert done.chunk_count == len(chunk_legal_text(CONTENT))
    assert done.duration_ms is not None and done.duration_ms >= 0
    assert done.detail is None


def test_duyet_loi_embedding_ghi_failed_detail_da_che_khoa(admin, db, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("GEMINI_API_KEY", "khoa-bi-mat-123")
    get_settings.cache_clear()

    class Leaky(FakeEmbedder):
        def embed_documents(self, texts):
            raise EmbeddingError("khoa-bi-mat-123 AIzaSyA1234567890123456789012345 key=abc postgresql://u:p@h/db " + "y" * 900)

    monkeypatch.setattr(document_approval, "get_embedder", lambda: Leaky(768))
    document_id = create(admin)
    assert admin.post(f"{URL}/{document_id}/approve").status_code == 202
    failed = rows(db)[-1]
    assert failed.action == "approve_failed"
    assert (failed.actor_user_id, failed.actor_role) == ("admin-1", "admin")
    assert str(failed.document_id) == document_id
    assert failed.duration_ms is not None and failed.duration_ms >= 0
    assert failed.chunk_count is None
    assert failed.detail and len(failed.detail) <= 500
    for secret in ("khoa-bi-mat-123", "AIzaSy", "key=abc", "postgresql://"):
        assert secret not in failed.detail


def test_xoa_nhap_ghi_delete_draft(admin, db):
    document_id = create(admin, doc_number="9/2021/QH")
    assert admin.delete(f"{URL}/{document_id}").status_code == 200
    assert actions(db) == ["create_draft", "delete_draft"]
    deleted = rows(db)[-1]
    assert (deleted.actor_user_id, deleted.actor_role) == ("admin-1", "admin")
    assert str(deleted.document_id) == document_id
    assert (deleted.doc_number, deleted.title) == ("9/2021/QH", "Luật Mẫu")
    assert db.get(LegalDocument, uuid.UUID(document_id)) is None  # nhật ký còn dù văn bản đã xóa (không khóa ngoại)


def test_thao_tac_bi_tu_choi_khong_ghi_nhat_ky(admin, db):
    first = create(admin, doc_number="31/2024/QH15")
    assert actions(db) == ["create_draft"]

    # trùng số hiệu 409, nội dung rỗng 422
    assert admin.post(URL, json={"title": "X", "doc_type": "luat", "content": CONTENT, "doc_number": "31/2024/QH15"}).status_code == 409
    assert admin.post(URL, json={"title": "X", "doc_type": "luat", "content": "   "}).status_code == 422
    # id lạ 404
    assert admin.post(f"{URL}/{uuid.uuid4()}/approve").status_code == 404
    assert admin.delete(f"{URL}/{uuid.uuid4()}").status_code == 404
    # văn bản active: duyệt và xóa đều 409
    set_state(db, first, status="active", ingest_status="done")
    assert admin.post(f"{URL}/{first}/approve").status_code == 409
    assert admin.delete(f"{URL}/{first}").status_code == 409
    # đang processing: duyệt và xóa đều 409
    set_state(db, first, status="draft", ingest_status="processing")
    assert admin.post(f"{URL}/{first}/approve").status_code == 409
    assert admin.delete(f"{URL}/{first}").status_code == 409

    assert actions(db) == ["create_draft"]


def test_thao_tac_chi_doc_khong_ghi_nhat_ky(admin, db):
    document_id = create(admin)
    admin.get(URL)
    admin.get(f"{URL}/{document_id}")
    admin.post(f"{URL}/preview", json={"title": "X", "doc_type": "luat", "content": CONTENT})
    assert actions(db) == ["create_draft"]


def test_nhat_ky_khong_chua_noi_dung_van_ban(admin, db):
    document_id = create(admin, content="Điều 1. A\nNOI-DUNG-RIENG-TU-XYZ")
    admin.post(f"{URL}/{document_id}/approve")
    assert "NOI-DUNG-RIENG-TU-XYZ" not in repr([(r.title, r.detail, r.doc_number) for r in rows(db)])


# ── Lỗi ghi nhật ký không làm hỏng việc chính ──────────────────────────────────

def test_khong_mo_duoc_session_nhat_ky_tao_nhap_van_201(admin, db, monkeypatch):
    def broken():
        raise RuntimeError("DB chết postgresql://u:matkhau@h/db")

    monkeypatch.setattr(document_approval, "_open_session", broken)
    assert admin.post(URL, json={"title": "X", "doc_type": "luat", "content": CONTENT}).status_code == 201


def test_thieu_bang_nhat_ky_tao_duyet_xoa_van_chay(admin, db, caplog):
    """Chưa chạy 003_ingest_log.sql (không có bảng): mọi việc chính vẫn xong, chỉ có cảnh báo trong log."""
    IngestLog.__table__.drop(db.get_bind())
    with caplog.at_level("WARNING"):
        document_id = create(admin)
        assert admin.post(f"{URL}/{document_id}/approve").status_code == 202
        doc = db.get(LegalDocument, uuid.UUID(document_id))
        db.refresh(doc)
        assert (doc.status, doc.ingest_status) == ("active", "done")

        other = create(admin, doc_number="2/2022/QH")
        assert admin.delete(f"{URL}/{other}").status_code == 200
    warnings = [r.getMessage() for r in caplog.records if "nhật ký nạp" in r.getMessage()]
    assert len(warnings) >= 5
    assert not any("matkhau" in w for w in warnings)


def test_record_khong_bao_gio_nem_ngoai_le(monkeypatch):
    def broken():
        raise RuntimeError("không có DB")

    monkeypatch.setattr(document_approval, "_open_session", broken)
    ingest_log.record("create_draft", actor_user_id="u", document_id=uuid.uuid4())  # không ném


def test_record_cat_do_dai_va_che_khoa(db, monkeypatch):
    monkeypatch.setattr(document_approval, "_open_session", lambda: Session(db.get_bind(), expire_on_commit=False))
    ingest_log.record(
        "approve_failed", actor_user_id="u" * 100, actor_role="r" * 50, doc_number="n" * 200, title="t" * 900,
        detail="key=abc " + "d" * 900,
    )
    (row,) = rows(db)
    assert len(row.actor_user_id) == 64 and len(row.actor_role) == 20
    assert len(row.doc_number) == 100 and len(row.title) == 500
    assert len(row.detail) == 500 and "key=abc" not in row.detail


# ── GET /logs ──────────────────────────────────────────────────────────────────

def test_logs_citizen_403(client):
    as_role("citizen")
    r = client.get(f"{URL}/logs")
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"


def test_logs_admin_200_khong_bi_hieu_nham_la_document_id(admin, db):
    r = admin.get(f"{URL}/logs", headers={"X-Request-ID": "req-l1"})
    assert r.status_code == 200
    assert r.json() == {"data": {"items": [], "total": 0, "page": 1, "page_size": 20}, "error": None, "request_id": "req-l1"}


def test_logs_moi_nhat_truoc_va_co_du_truong(admin, db):
    document_id = create(admin, doc_number="1/2020/QH")
    admin.post(f"{URL}/{document_id}/approve")
    data = admin.get(f"{URL}/logs").json()["data"]
    assert data["total"] == 3
    assert [i["action"] for i in data["items"]] == ["approve_done", "approve_requested", "create_draft"]
    item = data["items"][0]
    assert set(item) == {
        "id", "created_at", "actor_user_id", "actor_role", "action", "document_id", "doc_number", "title",
        "detail", "chunk_count", "duration_ms",
    }
    assert item["document_id"] == document_id and item["actor_user_id"] == "admin-1"
    assert item["chunk_count"] == len(chunk_legal_text(CONTENT))


def test_logs_phan_trang(admin, db):
    for i in range(5):
        create(admin, title=f"Luật {i}")
    first = admin.get(f"{URL}/logs", params={"page": 1, "page_size": 2}).json()["data"]
    second = admin.get(f"{URL}/logs", params={"page": 2, "page_size": 2}).json()["data"]
    third = admin.get(f"{URL}/logs", params={"page": 3, "page_size": 2}).json()["data"]
    assert (first["total"], first["page"], first["page_size"]) == (5, 1, 2)
    assert [len(p["items"]) for p in (first, second, third)] == [2, 2, 1]
    ids = [i["id"] for p in (first, second, third) for i in p["items"]]
    assert len(set(ids)) == 5  # không lặp, không sót
    assert [i["title"] for i in first["items"]] == ["Luật 4", "Luật 3"]  # mới nhất trước


def test_logs_loc_theo_document_id_va_action(admin, db):
    a = create(admin, doc_number="1/2020/QH")
    b = create(admin, doc_number="2/2020/QH")
    admin.post(f"{URL}/{a}/approve")

    by_doc = admin.get(f"{URL}/logs", params={"document_id": a}).json()["data"]
    assert by_doc["total"] == 3 and {i["document_id"] for i in by_doc["items"]} == {a}
    by_action = admin.get(f"{URL}/logs", params={"action": "create_draft"}).json()["data"]
    assert by_action["total"] == 2 and {i["action"] for i in by_action["items"]} == {"create_draft"}
    both = admin.get(f"{URL}/logs", params={"document_id": b, "action": "create_draft"}).json()["data"]
    assert both["total"] == 1 and both["items"][0]["document_id"] == b
    none = admin.get(f"{URL}/logs", params={"document_id": b, "action": "approve_done"}).json()["data"]
    assert (none["total"], none["items"]) == (0, [])


@pytest.mark.parametrize("params", [
    {"page_size": 101}, {"page_size": 0}, {"page": 0}, {"action": "khong-co"}, {"document_id": "khong-phai-uuid"},
])
def test_logs_tham_so_sai_422(admin, params):
    r = admin.get(f"{URL}/logs", params=params)
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_logs_page_size_100_hop_le(admin):
    assert admin.get(f"{URL}/logs", params={"page_size": 100}).status_code == 200
