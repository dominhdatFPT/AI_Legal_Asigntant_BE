import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.auth import TokenPayload
from app.core.database import get_db
from app.main import app
from app.models import DocumentChunk, LegalDocument
from app.services.chunking import chunk_legal_text
from app.services.embedding import EmbeddingError, FakeEmbedder

URL = "/api/v1/rag/documents"


def _user(role: str) -> TokenPayload:
    now = int(time.time())
    return TokenPayload(sub="admin-1", role=role, type="access", iss="test", iat=now, exp=now + 900)


class BrokenEmbedder(FakeEmbedder):
    def embed_documents(self, texts):
        raise EmbeddingError("hết quota " + "x" * 800)


@pytest.fixture
def client(db, monkeypatch):
    """Hàm nền dùng chung DB SQLite của test; embedder mặc định là fake (không gọi mạng)."""
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr("app.services.document_approval._open_session", lambda: Session(db.get_bind(), expire_on_commit=False))
    monkeypatch.setattr("app.services.document_approval.get_embedder", lambda: FakeEmbedder(768))
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def as_role(role: str) -> None:
    app.dependency_overrides[get_current_user] = lambda: _user(role)


@pytest.fixture
def admin(client):
    as_role("admin")
    return client


def payload(content, **kw):
    return {"title": "  Luật Mẫu  ", "doc_type": "luat", "content": content, **kw}


def create(client, content, **kw) -> str:
    r = client.post(URL, json=payload(content, **kw))
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def counts(db) -> tuple[int, int]:
    return (
        db.scalar(select(func.count()).select_from(LegalDocument)),
        db.scalar(select(func.count()).select_from(DocumentChunk)),
    )


def reload(db, document_id: str) -> LegalDocument:
    db.expire_all()
    return db.get(LegalDocument, uuid.UUID(document_id))


def set_state(db, document_id: str, **fields) -> None:
    doc = db.get(LegalDocument, uuid.UUID(document_id))
    for k, v in fields.items():
        setattr(doc, k, v)
    db.commit()


# ── Tạo bản nháp ───────────────────────────────────────────────────────────────

def test_tao_nhap_201_khong_chunk_khong_embedding(admin, db, sample_text, monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("tạo nháp không được gọi embedder")

    monkeypatch.setattr("app.services.document_approval.get_embedder", boom)
    monkeypatch.setattr("app.services.embedding.get_embedder", boom)
    r = admin.post(URL, json=payload(sample_text, doc_number="31/2024/QH15", issuing_body="Quốc hội",
                                     issued_date="2024-01-18", effective_date="2025-01-01"),
                   headers={"X-Request-ID": "req-c1"})
    assert r.status_code == 201
    body = r.json()
    assert body["error"] is None and body["request_id"] == "req-c1"
    data = body["data"]
    assert "content" not in data
    assert data["title"] == "Luật Mẫu"
    assert (data["status"], data["ingest_status"], data["is_processed"], data["chunk_count"]) == ("draft", "pending", False, 0)
    assert data["content_length"] == len(sample_text.strip())
    assert (data["issuing_body"], data["issued_date"], data["effective_date"]) == ("Quốc hội", "2024-01-18", "2025-01-01")
    assert data["ingest_error"] is None
    assert counts(db) == (1, 0)


def test_tao_nhap_khong_co_so_hieu_van_duoc(admin, db, sample_text):
    admin.post(URL, json=payload(sample_text, doc_number="   "))
    admin.post(URL, json=payload(sample_text))
    assert counts(db) == (2, 0)
    assert db.scalars(select(LegalDocument.doc_number)).all() == [None, None]


def test_tao_nhap_trung_so_hieu_409(admin, db, sample_text):
    create(admin, sample_text, doc_number="31/2024/QH15", title="Luật Đất đai")
    r = admin.post(URL, json=payload(sample_text, doc_number="31/2024/QH15"))
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "DOCUMENT_EXISTS"
    assert "31/2024/QH15" in err["message"] and "Luật Đất đai" in err["message"]
    assert counts(db) == (1, 0)


def test_tao_nhap_trung_khoa_duy_nhat_luc_ghi_van_409_va_rollback_sach(admin, db, sample_text, monkeypatch):
    """Hai người nạp cùng lúc: kiểm tra trước không thấy, đến lúc ghi mới đụng khóa duy nhất."""
    from app.repositories import document as repo

    create(admin, sample_text, doc_number="31/2024/QH15", title="Luật Đất đai")
    real = repo.get_brief_by_doc_number
    calls = []

    def first_miss(db_, number):
        calls.append(number)
        return None if len(calls) == 1 else real(db_, number)

    monkeypatch.setattr(repo, "get_brief_by_doc_number", first_miss)
    r = admin.post(URL, json=payload(sample_text, doc_number="31/2024/QH15"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "DOCUMENT_EXISTS"
    assert counts(db) == (1, 0)
    assert admin.post(URL, json=payload(sample_text, doc_number="1/1999/QH")).status_code == 201  # session còn dùng được


@pytest.mark.parametrize("content", ["", "   \n\n  "])
def test_tao_nhap_content_rong_422(admin, db, content):
    r = admin.post(URL, json=payload(content))
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "VALIDATION_ERROR" and err["details"][0]["field"] == "content"
    assert counts(db) == (0, 0)


def test_tao_nhap_content_co_nul_422(admin, db, sample_text):
    r = admin.post(URL, json=payload(sample_text + "\u0000"))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert counts(db) == (0, 0)


@pytest.mark.parametrize(
    "bad",
    [{"title": ""}, {"doc_type": "hien_phap"}, {"doc_number": "x" * 101}, {"issuing_body": "x" * 256},
     {"source_url": "x" * 2001}, {"issued_date": "khong-phai-ngay"}],
)
def test_tao_nhap_du_lieu_sai_422(admin, sample_text, bad):
    r = admin.post(URL, json=payload(sample_text, **bad))
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_tao_nhap_citizen_403(client, db, sample_text):
    as_role("citizen")
    r = client.post(URL, json=payload(sample_text))
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"
    assert counts(db) == (0, 0)


# ── Chi tiết ───────────────────────────────────────────────────────────────────

def test_chi_tiet_200_khong_content_co_content_length(admin, db):
    content = "Điều 1. A\nNội dung điều một."
    document_id = create(admin, content)
    r = admin.get(f"{URL}/{document_id}", headers={"X-Request-ID": "req-g1"})
    assert r.status_code == 200
    assert r.json()["request_id"] == "req-g1"
    data = r.json()["data"]
    assert "content" not in data and "Nội dung điều một" not in r.text
    assert data["content_length"] == len(content)
    assert data["id"] == document_id and data["ingest_status"] == "pending"


def test_chi_tiet_khong_keo_cot_content(admin, db):
    from sqlalchemy import event

    document_id = create(admin, "Điều 1. A\nNội dung.")
    sql = []
    event.listen(db.get_bind(), "before_cursor_execute", lambda c, cur, s, *a: sql.append(s))
    admin.get(f"{URL}/{document_id}")
    selects = [s for s in sql if s.lstrip().upper().startswith("SELECT") and "FROM legal_documents" in s]
    assert selects
    for s in selects:
        columns = s.split("FROM legal_documents")[0]
        assert "length(legal_documents.content)" in columns
        assert columns.replace("length(legal_documents.content)", "").count("legal_documents.content") == 0


def test_chi_tiet_id_la_404(admin):
    r = admin.get(f"{URL}/{uuid.uuid4()}")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_chi_tiet_id_sai_dinh_dang_422(admin):
    r = admin.get(f"{URL}/khong-phai-uuid")
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_chi_tiet_citizen_403(client, db):
    as_role("citizen")
    assert client.get(f"{URL}/{uuid.uuid4()}").status_code == 403


# ── Xóa ────────────────────────────────────────────────────────────────────────

def test_xoa_nhap_pending_200(admin, db, sample_text):
    document_id = create(admin, sample_text)
    r = admin.delete(f"{URL}/{document_id}", headers={"X-Request-ID": "req-d1"})
    assert r.status_code == 200
    assert r.json() == {"data": {"deleted": True}, "error": None, "request_id": "req-d1"}
    assert counts(db) == (0, 0)


def test_xoa_nhap_failed_co_chunk_xoa_theo_chunk(admin, db, sample_text):
    document_id = create(admin, sample_text)
    doc = db.get(LegalDocument, uuid.UUID(document_id))
    db.add(DocumentChunk(document_id=doc.id, chunk_index=0, content="x", embedding=[0.0] * 768, embedding_model="m"))
    set_state(db, document_id, ingest_status="failed", ingest_error="loi")
    assert counts(db) == (1, 1)
    assert admin.delete(f"{URL}/{document_id}").status_code == 200
    assert counts(db) == (0, 0)


def test_xoa_van_ban_active_409(admin, db, sample_text):
    document_id = create(admin, sample_text)
    set_state(db, document_id, status="active", ingest_status="done")
    r = admin.delete(f"{URL}/{document_id}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATE"
    assert counts(db)[0] == 1


def test_xoa_van_ban_superseded_409(admin, db, sample_text):
    document_id = create(admin, sample_text)
    set_state(db, document_id, status="superseded", ingest_status="done")
    assert admin.delete(f"{URL}/{document_id}").status_code == 409


def test_xoa_nhap_dang_processing_409(admin, db, sample_text):
    document_id = create(admin, sample_text)
    set_state(db, document_id, ingest_status="processing")
    r = admin.delete(f"{URL}/{document_id}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATE"
    assert counts(db)[0] == 1


def test_xoa_id_la_404_va_citizen_403(admin, client):
    assert admin.delete(f"{URL}/{uuid.uuid4()}").status_code == 404
    as_role("citizen")
    assert client.delete(f"{URL}/{uuid.uuid4()}").status_code == 403


# ── Duyệt ──────────────────────────────────────────────────────────────────────

def test_duyet_thanh_cong(admin, db, sample_text):
    document_id = create(admin, sample_text)
    r = admin.post(f"{URL}/{document_id}/approve", headers={"X-Request-ID": "req-a1"})
    assert r.status_code == 202
    assert r.json()["request_id"] == "req-a1"
    assert r.json()["data"]["ingest_status"] == "processing"  # phản hồi trả ngay, trước khi việc nền xong
    assert "content" not in r.json()["data"]

    doc = reload(db, document_id)
    expected = len(chunk_legal_text(sample_text))
    assert (doc.status, doc.ingest_status, doc.is_processed) == ("active", "done", True)
    assert doc.chunk_count == expected
    assert doc.ingest_error is None
    assert db.scalar(select(func.count()).select_from(DocumentChunk)) == expected
    detail = admin.get(f"{URL}/{document_id}").json()["data"]
    assert (detail["status"], detail["ingest_status"], detail["chunk_count"]) == ("active", "done", expected)


def test_duyet_loi_embedding_giu_nhap_roi_thu_lai_duoc(admin, db, sample_text, monkeypatch):
    document_id = create(admin, sample_text)
    monkeypatch.setattr("app.services.document_approval.get_embedder", lambda: BrokenEmbedder(768))
    assert admin.post(f"{URL}/{document_id}/approve").status_code == 202

    doc = reload(db, document_id)
    assert (doc.status, doc.ingest_status, doc.is_processed) == ("draft", "failed", False)
    assert doc.ingest_error and 0 < len(doc.ingest_error) <= 500
    assert "hết quota" in doc.ingest_error
    assert db.scalar(select(func.count()).select_from(DocumentChunk)) == 0
    assert admin.get(f"{URL}/{document_id}").json()["data"]["ingest_error"] == doc.ingest_error

    monkeypatch.setattr("app.services.document_approval.get_embedder", lambda: FakeEmbedder(768))
    assert admin.post(f"{URL}/{document_id}/approve").status_code == 202
    doc = reload(db, document_id)
    assert (doc.status, doc.ingest_status, doc.ingest_error) == ("active", "done", None)
    assert doc.chunk_count == len(chunk_legal_text(sample_text))


def test_duyet_hai_lan_chi_chay_nen_mot_lan(admin, db, sample_text, monkeypatch):
    document_id = create(admin, sample_text)
    ran = []
    monkeypatch.setattr("app.api.v1.documents.approve_document_background", lambda i, **kw: ran.append(i))
    assert admin.post(f"{URL}/{document_id}/approve").status_code == 202
    r = admin.post(f"{URL}/{document_id}/approve")
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATE"
    assert len(ran) == 1
    assert reload(db, document_id).ingest_status == "processing"


def test_duyet_hai_lan_sau_khi_xong_van_409(admin, db, sample_text):
    document_id = create(admin, sample_text)
    assert admin.post(f"{URL}/{document_id}/approve").status_code == 202
    r = admin.post(f"{URL}/{document_id}/approve")
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATE"


def test_duyet_van_ban_dang_processing_409(admin, db, sample_text):
    document_id = create(admin, sample_text)
    set_state(db, document_id, ingest_status="processing")
    r = admin.post(f"{URL}/{document_id}/approve")
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATE"


def test_duyet_van_ban_active_409(admin, db, sample_text):
    document_id = create(admin, sample_text)
    set_state(db, document_id, status="active", ingest_status="done")
    r = admin.post(f"{URL}/{document_id}/approve")
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATE"
    assert counts(db)[1] == 0


def test_duyet_mat_cuoc_chiem_cho_409(admin, db, sample_text, monkeypatch):
    """Đọc thấy pending nhưng UPDATE có điều kiện không đổi dòng nào (người khác vừa bấm) -> 409, không chạy nền."""
    from app.repositories import document as repo

    document_id = create(admin, sample_text)
    ran = []
    monkeypatch.setattr("app.api.v1.documents.approve_document_background", lambda i, **kw: ran.append(i))
    monkeypatch.setattr(repo, "claim_for_processing", lambda db_, i: False)
    assert admin.post(f"{URL}/{document_id}/approve").status_code == 409
    assert ran == []


def test_duyet_id_la_404_va_citizen_403(admin, client):
    assert admin.post(f"{URL}/{uuid.uuid4()}/approve").status_code == 404
    as_role("citizen")
    assert client.post(f"{URL}/{uuid.uuid4()}/approve").status_code == 403


def test_duyet_id_sai_dinh_dang_422(admin):
    assert admin.post(f"{URL}/abc/approve").status_code == 422
