import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.deps import get_current_user
from app.core.auth import TokenPayload
from app.core.database import get_db
from app.main import app
from app.models import DocumentChunk, LegalDocument
from app.services.chunking import chunk_legal_text

LIST_URL = "/api/v1/rag/documents"
PREVIEW_URL = "/api/v1/rag/documents/preview"


def _user(role: str) -> TokenPayload:
    now = int(time.time())
    return TokenPayload(sub="user-1", role=role, type="access", iss="test", iat=now, exp=now + 900)


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def as_role(role: str) -> None:
    app.dependency_overrides[get_current_user] = lambda: _user(role)


def add_doc(db, title="Luật A", doc_type="luat", status="active", doc_number=None, content="Điều 1. A\nB"):
    doc = LegalDocument(title=title, doc_type=doc_type, status=status, doc_number=doc_number, content=content)
    db.add(doc)
    db.commit()
    return doc


def counts(db) -> tuple[int, int]:
    return (
        db.scalar(select(func.count()).select_from(LegalDocument)),
        db.scalar(select(func.count()).select_from(DocumentChunk)),
    )


# ── GET danh sách ──────────────────────────────────────────────────────────────

def test_danh_sach_thieu_header_authorization_bi_tu_choi(client):
    r = client.get(LIST_URL)
    # FastAPI 0.143 (HTTPBearer) trả 401 khi thiếu header Authorization
    assert r.status_code == 401


def test_danh_sach_citizen_403(client):
    as_role("citizen")
    r = client.get(LIST_URL)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"


def test_danh_sach_admin_200_dung_phong_bi(client, db):
    as_role("admin")
    add_doc(db, doc_number="01/2099/QH")
    r = client.get(LIST_URL, headers={"X-Request-ID": "req-abc"})
    assert r.status_code == 200
    body = r.json()
    assert body["error"] is None and body["request_id"] == "req-abc"
    assert r.headers["X-Request-ID"] == "req-abc"
    assert body["data"]["total"] == 1 and body["data"]["page"] == 1 and body["data"]["page_size"] == 20
    (item,) = body["data"]["items"]
    assert set(item) == {
        "id", "title", "doc_type", "doc_number", "issuing_body", "effective_date",
        "status", "chunk_count", "is_processed", "ingest_status", "created_at", "updated_at",
    }


def test_danh_sach_khong_tra_content(client, db):
    as_role("admin")
    add_doc(db, content="TOAN VAN RAT DAI")
    r = client.get(LIST_URL)
    assert "content" not in r.json()["data"]["items"][0]
    assert "TOAN VAN" not in r.text


def test_danh_sach_phan_trang(client, db):
    as_role("admin")
    for i in range(3):
        add_doc(db, title=f"Luật {i}")
    p1 = client.get(LIST_URL, params={"page": 1, "page_size": 2}).json()["data"]
    p2 = client.get(LIST_URL, params={"page": 2, "page_size": 2}).json()["data"]
    assert (len(p1["items"]), p1["total"], p1["page"], p1["page_size"]) == (2, 3, 1, 2)
    assert (len(p2["items"]), p2["total"]) == (1, 3)
    ids = [i["id"] for i in p1["items"] + p2["items"]]
    assert len(set(ids)) == 3


def test_danh_sach_loc_status_va_doc_type(client, db):
    as_role("admin")
    add_doc(db, title="Hiệu lực", status="active", doc_type="luat")
    add_doc(db, title="Nháp", status="draft", doc_type="luat")
    add_doc(db, title="Nghị định", status="active", doc_type="nghi_dinh")
    by_status = client.get(LIST_URL, params={"status": "draft"}).json()["data"]
    assert by_status["total"] == 1 and by_status["items"][0]["title"] == "Nháp"
    by_type = client.get(LIST_URL, params={"doc_type": "nghi_dinh"}).json()["data"]
    assert by_type["total"] == 1 and by_type["items"][0]["title"] == "Nghị định"
    both = client.get(LIST_URL, params={"status": "active", "doc_type": "luat"}).json()["data"]
    assert both["total"] == 1 and both["items"][0]["title"] == "Hiệu lực"


@pytest.mark.parametrize(
    "params",
    [{"page_size": 101}, {"page_size": 0}, {"page": 0}, {"status": "xoa"}, {"doc_type": "hien_phap"}],
)
def test_danh_sach_tham_so_sai_422(client, params):
    as_role("admin")
    r = client.get(LIST_URL, params=params)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_danh_sach_rong(client):
    as_role("admin")
    data = client.get(LIST_URL).json()["data"]
    assert data["items"] == [] and data["total"] == 0


# ── POST preview ───────────────────────────────────────────────────────────────

def body(content, **kw):
    return {"title": "  Luật Mẫu  ", "doc_type": "luat", "content": content, **kw}


def test_preview_thieu_header_authorization_bi_tu_choi(client, sample_text):
    assert client.post(PREVIEW_URL, json=body(sample_text)).status_code == 401


def test_preview_citizen_403(client, sample_text):
    as_role("citizen")
    r = client.post(PREVIEW_URL, json=body(sample_text))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"


def test_preview_admin_khop_chunk_legal_text(client, sample_text):
    as_role("admin")
    r = client.post(PREVIEW_URL, json=body(sample_text), headers={"X-Request-ID": "req-p1"})
    assert r.status_code == 200
    out = r.json()
    assert out["error"] is None and out["request_id"] == "req-p1"
    data = out["data"]
    chunks = chunk_legal_text(sample_text)
    assert data["chunk_count"] == len(chunks)
    assert data["article_count"] == len({c.section_info["dieu"] for c in chunks})
    assert data["existing_document"] is None
    assert len(data["sample_chunks"]) == min(3, len(chunks))
    assert all(len(s["content"]) <= 300 for s in data["sample_chunks"])


def test_preview_khong_ghi_db_va_khong_goi_embedder(client, db, sample_text, monkeypatch):
    as_role("admin")
    add_doc(db, doc_number="31/2024/QH15")
    before = counts(db)
    called = []

    def boom(*a, **kw):
        called.append(1)
        raise AssertionError("preview không được gọi embedder")

    monkeypatch.setattr("app.services.embedding.get_embedder", boom)
    monkeypatch.setattr("app.main.get_embedder", boom)
    r = client.post(PREVIEW_URL, json=body(sample_text, doc_number="99/2099/QH"))
    assert r.status_code == 200
    assert counts(db) == before
    assert called == []


def test_preview_trung_so_hieu_tra_existing_document(client, db, sample_text):
    as_role("admin")
    doc = add_doc(db, title="Luật Đã Có", status="superseded", doc_number="31/2024/QH15")
    r = client.post(PREVIEW_URL, json=body(sample_text, doc_number=" 31/2024/QH15 "))
    assert r.json()["data"]["existing_document"] == {
        "id": str(doc.id), "title": "Luật Đã Có", "status": "superseded",
    }


def test_preview_so_hieu_khac_thi_existing_null(client, db, sample_text):
    as_role("admin")
    add_doc(db, doc_number="31/2024/QH15")
    r = client.post(PREVIEW_URL, json=body(sample_text, doc_number="1/1999/QH"))
    assert r.json()["data"]["existing_document"] is None


@pytest.mark.parametrize("content", ["", "   \n\n  "])
def test_preview_content_rong_422(client, content):
    as_role("admin")
    r = client.post(PREVIEW_URL, json=body(content))
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert err["message"] and err["details"][0]["field"] == "content"


@pytest.mark.parametrize(
    "payload",
    [
        {"doc_type": "luat", "content": "x"},
        {"title": "", "doc_type": "luat", "content": "x"},
        {"title": "x" * 501, "doc_type": "luat", "content": "x"},
        {"title": "T", "doc_type": "hien_phap", "content": "x"},
        {"title": "T", "doc_type": "luat"},
        {"title": "T", "doc_type": "luat", "content": "x" * 3_000_001},
    ],
)
def test_preview_du_lieu_sai_422(client, payload):
    as_role("admin")
    r = client.post(PREVIEW_URL, json=payload)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_preview_canh_bao_nhay_coc(client):
    as_role("admin")
    text = "Điều 1. A\nNội dung.\nĐiều 2. B\nNội dung.\nĐiều 4. D\nNội dung."
    warnings = client.post(PREVIEW_URL, json=body(text)).json()["data"]["warnings"]
    assert any("nhảy cóc" in w and "Điều 3" in w for w in warnings)


def test_preview_khong_co_dieu_canh_bao_cat_theo_doan_van(client):
    as_role("admin")
    r = client.post(PREVIEW_URL, json=body("Đoạn một không có cấu trúc.\n\nĐoạn hai."))
    data = r.json()["data"]
    assert data["article_count"] == 0
    assert any("cắt theo đoạn văn" in w for w in data["warnings"])
