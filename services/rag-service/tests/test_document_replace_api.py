"""POST /api/v1/rag/documents/{id}/replace/preview và /replace (qua HTTP, TestClient)."""

import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.auth import TokenPayload
from app.core.config import get_settings
from app.core.database import get_db
from app.main import app
from app.models import DocumentChunk, DocumentVersion, IngestLog, LegalDocument
from app.services import document_approval as approval
from app.services import document_replace as replace
from app.services.chunking import chunk_legal_text
from app.services.embedding import EmbeddingError, FakeEmbedder
from app.services.ingest import ingest_document

URL = "/api/v1/rag/documents"
OLD = (
    "Điều 1. Phạm vi\nNội dung điều một.\n"
    "Điều 2. Đối tượng\nNội dung điều hai.\n"
    "Điều 3. Giải thích\nNội dung điều ba.\n"
    "Điều 4. Hiệu lực\nNội dung điều bốn."
)
NEW = OLD.replace("Nội dung điều hai.", "Nội dung điều hai đã được sửa đổi.")


def _user(role: str) -> TokenPayload:
    now = int(time.time())
    return TokenPayload(sub="admin-1", role=role, type="access", iss="test", iat=now, exp=now + 900)


class Counting(FakeEmbedder):
    def __init__(self, calls):
        super().__init__(768)
        self.calls = calls

    def embed_documents(self, texts):
        self.calls.append(list(texts))
        return super().embed_documents(texts)


@pytest.fixture
def embed_calls(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(replace, "get_embedder", lambda: Counting(calls))
    return calls


@pytest.fixture
def client(db, monkeypatch, embed_calls):
    # Xem trước so khớp theo model trong cấu hình; chunk dựng bằng FakeEmbedder mang model "fake-hash-768"
    monkeypatch.setenv("EMBEDDING_MODEL", FakeEmbedder(768).model_name)
    get_settings.cache_clear()
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(approval, "_open_session", lambda: Session(db.get_bind(), expire_on_commit=False))
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def as_role(role: str) -> None:
    app.dependency_overrides[get_current_user] = lambda: _user(role)


@pytest.fixture
def admin(client):
    as_role("admin")
    return client


@pytest.fixture
def doc_id(db) -> str:
    doc = ingest_document(db, FakeEmbedder(768), title="Luật Mẫu", doc_type="luat", content=OLD, doc_number="1/2020/QH")
    return str(doc.id)


def counts(db) -> tuple[int, int, int, int]:
    return tuple(
        db.scalar(select(func.count()).select_from(model))
        for model in (LegalDocument, DocumentChunk, DocumentVersion, IngestLog)
    )


def reload(db, document_id: str) -> LegalDocument:
    db.expire_all()
    return db.get(LegalDocument, uuid.UUID(document_id))


def set_state(db, document_id: str, **fields) -> None:
    doc = db.get(LegalDocument, uuid.UUID(document_id))
    for k, v in fields.items():
        setattr(doc, k, v)
    db.commit()


def actions(db) -> list[str]:
    db.expire_all()
    return [r.action for r in db.scalars(select(IngestLog).order_by(IngestLog.created_at, IngestLog.id))]


def preview(client, document_id, content, **kw):
    return client.post(f"{URL}/{document_id}/replace/preview", json={"content": content}, **kw)


def do_replace(client, document_id, content, **kw):
    return client.post(f"{URL}/{document_id}/replace", json={"content": content}, **kw)


# ── Xem trước ──────────────────────────────────────────────────────────────────

def test_preview_doi_mot_dieu(admin, db, doc_id, embed_calls):
    r = preview(admin, doc_id, NEW, headers={"X-Request-ID": "req-p1"})
    assert r.status_code == 200
    assert r.json()["request_id"] == "req-p1" and r.json()["error"] is None
    data = r.json()["data"]
    cmp = data["comparison"]
    assert (cmp["old_chunk_count"], cmp["new_chunk_count"]) == (4, 4)
    assert (cmp["reused_chunk_count"], cmp["embed_chunk_count"], cmp["removed_chunk_count"]) == (3, 1, 1)
    assert cmp["changed_articles"] == ["2"] and cmp["changed_articles_truncated"] is False
    assert cmp["embed_estimate"]["batch_count"] == 1 and cmp["embed_estimate"]["total_chars"] > 0
    # kết quả analyze_document cho bản mới vẫn có đủ
    assert data["article_count"] == 4 and data["chunk_count"] == 4
    for key in ("total_chars", "chunks_by_level", "split_articles", "warnings", "embedding_estimate", "sample_chunks"):
        assert key in data
    assert data["replace_warnings"] == []
    assert embed_calls == []


def test_preview_khong_goi_embedder_va_khong_ghi_gi_vao_db(admin, db, doc_id, monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("xem trước không được gọi embedder")

    monkeypatch.setattr(replace, "get_embedder", boom)
    monkeypatch.setattr(approval, "get_embedder", boom)
    monkeypatch.setattr("app.services.embedding.get_embedder", boom)
    before_counts = counts(db)
    before_updated = reload(db, doc_id).updated_at
    assert preview(admin, doc_id, NEW).status_code == 200
    assert preview(admin, doc_id, OLD).status_code == 200
    assert counts(db) == before_counts
    doc = reload(db, doc_id)
    assert (doc.content, doc.updated_at, doc.ingest_status) == (OLD, before_updated, "done")


def test_preview_citizen_403(client, doc_id):
    as_role("citizen")
    assert preview(client, doc_id, NEW).status_code == 403


def test_preview_van_ban_khong_co_404_va_id_sai_422(admin):
    r = preview(admin, uuid.uuid4(), NEW)
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    assert preview(admin, "abc", NEW).status_code == 422


@pytest.mark.parametrize("fields", [{"status": "draft", "ingest_status": "pending"}, {"status": "superseded"}])
def test_preview_van_ban_khong_active_409(admin, db, doc_id, fields):
    set_state(db, doc_id, **fields)
    r = preview(admin, doc_id, NEW)
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATE"


def test_preview_van_ban_dang_processing_409(admin, db, doc_id):
    set_state(db, doc_id, ingest_status="processing")
    r = preview(admin, doc_id, NEW)
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATE"


def test_preview_noi_dung_giong_het_co_canh_bao(admin, doc_id):
    data = preview(admin, doc_id, OLD + "\n\n").json()["data"]
    assert data["comparison"]["embed_chunk_count"] == 0 and data["comparison"]["reused_chunk_count"] == 4
    assert "Nội dung mới giống hệt nội dung hiện tại, không có gì để thay." in data["replace_warnings"]


def test_preview_canh_bao_it_dieu_hon_va_doi_hoan_toan(admin, doc_id):
    data = preview(admin, doc_id, "Điều 1. Khác hẳn\nChuyện hoàn toàn khác.").json()["data"]
    warnings = data["replace_warnings"]
    assert any("ít hơn" in w for w in warnings)
    assert any("chênh" in w for w in warnings)
    assert any("Không có đoạn nào dùng lại" in w for w in warnings)
    assert data["comparison"]["reused_chunk_count"] == 0 and data["comparison"]["embed_chunk_count"] == 1


@pytest.mark.parametrize("content", ["", "   \n\n  "])
def test_preview_noi_dung_rong_422(admin, doc_id, content):
    r = preview(admin, doc_id, content)
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert r.json()["error"]["details"][0]["field"] == "content"


def test_preview_noi_dung_co_nul_hoac_thieu_422(admin, doc_id):
    assert preview(admin, doc_id, NEW + "\u0000").status_code == 422
    assert admin.post(f"{URL}/{doc_id}/replace/preview", json={}).status_code == 422


def test_preview_chunk_cu_khac_model_khong_tinh_la_dung_lai(admin, db, doc_id):
    for chunk in db.scalars(select(DocumentChunk)):
        chunk.embedding_model = "model-cu"
    db.commit()
    cmp = preview(admin, doc_id, NEW).json()["data"]["comparison"]
    assert (cmp["reused_chunk_count"], cmp["embed_chunk_count"]) == (0, 4)


# ── Thay thế ───────────────────────────────────────────────────────────────────

def test_replace_doi_mot_dieu_thanh_cong(admin, db, doc_id, embed_calls):
    r = do_replace(admin, doc_id, NEW, headers={"X-Request-ID": "req-r1"})
    assert r.status_code == 202
    assert r.json()["request_id"] == "req-r1"
    data = r.json()["data"]
    assert data["status"] == "active" and data["ingest_status"] == "processing"  # phản hồi trả ngay, trước khi việc nền xong
    assert "content" not in data

    doc = reload(db, doc_id)
    assert (doc.status, doc.ingest_status, doc.ingest_error, doc.is_processed) == ("active", "done", None, True)
    assert doc.content == NEW and doc.chunk_count == len(chunk_legal_text(NEW))
    assert len(embed_calls) == 1 and len(embed_calls[0]) == 1 and "đã được sửa đổi" in embed_calls[0][0]  # chỉ chunk đổi
    (version,) = db.scalars(select(DocumentVersion)).all()
    assert (str(version.document_id), version.content, version.chunk_count) == (doc_id, OLD, 4)
    assert version.replaced_by_user_id == "admin-1"
    assert actions(db) == ["replace_requested", "replace_done"]
    detail = admin.get(f"{URL}/{doc_id}").json()["data"]
    assert (detail["status"], detail["ingest_status"], detail["content_length"]) == ("active", "done", len(NEW))


def test_replace_nhat_ky_va_loc_logs_theo_action_moi(admin, db, doc_id):
    do_replace(admin, doc_id, NEW)
    items = admin.get(f"{URL}/logs", params={"action": "replace_done"}).json()["data"]["items"]
    assert [i["action"] for i in items] == ["replace_done"]
    assert items[0]["actor_user_id"] == "admin-1" and items[0]["detail"] == "dùng lại 3, embed 1"
    assert admin.get(f"{URL}/logs", params={"action": "replace_requested"}).json()["data"]["total"] == 1
    assert admin.get(f"{URL}/logs", params={"action": "replace_failed"}).json()["data"]["total"] == 0
    assert admin.get(f"{URL}/logs", params={"action": "replace_khac"}).status_code == 422


def test_replace_khong_thay_doi_gi_409(admin, db, doc_id, embed_calls):
    before = counts(db)
    r = do_replace(admin, doc_id, OLD + "\r\n")
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATE"
    assert "không có thay đổi" in r.json()["error"]["message"]
    assert counts(db) == before and embed_calls == []
    assert reload(db, doc_id).ingest_status == "done"


def test_replace_citizen_403(client, db, doc_id):
    as_role("citizen")
    assert do_replace(client, doc_id, NEW).status_code == 403
    assert reload(db, doc_id).content == OLD


def test_replace_van_ban_khong_co_404_noi_dung_rong_va_nul_422(admin, db, doc_id):
    assert do_replace(admin, uuid.uuid4(), NEW).status_code == 404
    assert do_replace(admin, doc_id, "  \n ").status_code == 422
    assert do_replace(admin, doc_id, NEW + "\u0000").status_code == 422
    assert reload(db, doc_id).content == OLD and reload(db, doc_id).ingest_status == "done"


@pytest.mark.parametrize("fields", [{"status": "draft", "ingest_status": "pending"}, {"status": "superseded"}])
def test_replace_van_ban_khong_active_409(admin, db, doc_id, fields):
    set_state(db, doc_id, **fields)
    r = do_replace(admin, doc_id, NEW)
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATE"
    assert actions(db) == []


def test_replace_van_ban_dang_processing_409_va_hai_lan_lien_nhau(admin, db, doc_id, monkeypatch):
    ran = []
    monkeypatch.setattr("app.api.v1.documents.replace_document_background", lambda *a, **kw: ran.append(a))
    assert do_replace(admin, doc_id, NEW).status_code == 202  # chiếm chỗ, việc nền bị thay bằng bản giả nên ở lại processing
    assert reload(db, doc_id).ingest_status == "processing"
    r = do_replace(admin, doc_id, NEW + "\nĐiều 5. Thêm\nNội dung năm.")
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATE"
    assert len(ran) == 1
    assert actions(db) == ["replace_requested"]  # lần bị từ chối không ghi nhật ký


def test_replace_mat_cuoc_chiem_cho_409_khong_chay_nen(admin, db, doc_id, monkeypatch):
    from app.repositories import document as repo

    ran = []
    monkeypatch.setattr("app.api.v1.documents.replace_document_background", lambda *a, **kw: ran.append(a))
    monkeypatch.setattr(repo, "claim_for_replace", lambda db_, i: False)
    assert do_replace(admin, doc_id, NEW).status_code == 409
    assert ran == [] and actions(db) == []


def test_replace_truyen_nguoi_goi_cho_viec_nen(admin, db, doc_id, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "app.api.v1.documents.replace_document_background",
        lambda document_id, content, **kw: seen.update(document_id=document_id, content=content, **kw),
    )
    do_replace(admin, doc_id, NEW)
    assert (str(seen["document_id"]), seen["content"]) == (doc_id, NEW)
    assert (seen["actor_user_id"], seen["actor_role"]) == ("admin-1", "admin")


def test_replace_loi_embedding_giu_ban_cu_roi_thu_lai_duoc(admin, db, doc_id, monkeypatch, embed_calls):
    monkeypatch.setenv("GEMINI_API_KEY", "khoa-bi-mat-123")
    get_settings.cache_clear()

    class Leaky(FakeEmbedder):
        def embed_documents(self, texts):
            raise EmbeddingError("hết quota khoa-bi-mat-123 key=abc " + "x" * 800)

    monkeypatch.setattr(replace, "get_embedder", lambda: Leaky(768))
    assert do_replace(admin, doc_id, NEW).status_code == 202

    doc = reload(db, doc_id)
    assert (doc.status, doc.ingest_status, doc.content, doc.chunk_count, doc.is_processed) == ("active", "failed", OLD, 4, True)
    assert doc.ingest_error and len(doc.ingest_error) <= 500 and "hết quota" in doc.ingest_error
    assert "khoa-bi-mat-123" not in doc.ingest_error and "key=abc" not in doc.ingest_error
    assert counts(db)[:3] == (1, 4, 0)  # không có dòng document_versions mới, chunk cũ còn nguyên
    assert actions(db) == ["replace_requested", "replace_failed"]
    failed = admin.get(f"{URL}/logs", params={"action": "replace_failed"}).json()["data"]["items"][0]
    assert "khoa-bi-mat-123" not in failed["detail"] and failed["duration_ms"] >= 0
    assert admin.get(f"{URL}/{doc_id}").json()["data"]["ingest_error"] == doc.ingest_error

    monkeypatch.setattr(replace, "get_embedder", lambda: Counting(embed_calls))  # embedder tốt, thử lại từ failed
    assert do_replace(admin, doc_id, NEW).status_code == 202
    doc = reload(db, doc_id)
    assert (doc.status, doc.ingest_status, doc.ingest_error, doc.content) == ("active", "done", None, NEW)
    assert counts(db)[2] == 1
    assert actions(db) == ["replace_requested", "replace_failed", "replace_requested", "replace_done"]


def test_replace_van_tim_kiem_duoc_ban_cu_trong_luc_xu_ly(admin, db, doc_id, monkeypatch):
    """Giữa chừng (đang embed): văn bản vẫn active, is_processed, chunk cũ còn đủ để tìm kiếm."""
    seen = {}

    class Peek(FakeEmbedder):
        def embed_documents(self, texts):
            db.expire_all()
            doc = db.get(LegalDocument, uuid.UUID(doc_id))
            seen["state"] = (doc.status, doc.ingest_status, doc.is_processed, doc.content == OLD)
            seen["chunks"] = db.scalar(select(func.count()).select_from(DocumentChunk))
            return super().embed_documents(texts)

    monkeypatch.setattr(replace, "get_embedder", lambda: Peek(768))
    assert do_replace(admin, doc_id, NEW).status_code == 202
    assert seen["state"] == ("active", "processing", True, True)
    assert seen["chunks"] == 4
    assert reload(db, doc_id).content == NEW


def test_duyet_ban_nhap_va_xoa_nhap_khong_bi_anh_huong(admin, db, doc_id):
    """Đường tạo nháp / duyệt cũ không đổi: văn bản active không duyệt được, không xóa được."""
    assert admin.post(f"{URL}/{doc_id}/approve").status_code == 409
    assert admin.delete(f"{URL}/{doc_id}").status_code == 409
