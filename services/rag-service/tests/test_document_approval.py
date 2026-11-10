"""Hàm nền app/services/document_approval.py và phục hồi khi khởi động (không qua HTTP)."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DocumentChunk, LegalDocument
from app.services import document_approval as approval
from app.services.embedding import EmbeddingError, FakeEmbedder


@pytest.fixture(autouse=True)
def factory(db, monkeypatch):
    monkeypatch.setattr(approval, "_open_session", lambda: Session(db.get_bind(), expire_on_commit=False))
    monkeypatch.setattr(approval, "get_embedder", lambda: FakeEmbedder(768))


def add_doc(db, ingest_status="processing", status="draft", content="Điều 1. A\nNội dung.", **kw) -> uuid.UUID:
    doc = LegalDocument(title="Luật A", doc_type="luat", content=content, status=status, ingest_status=ingest_status, **kw)
    db.add(doc)
    db.commit()
    return doc.id


def state(db, document_id):
    db.expire_all()
    d = db.get(LegalDocument, document_id)
    return d.status, d.ingest_status, d.ingest_error


def test_thanh_cong_active_done(db):
    document_id = add_doc(db)
    approval.approve_document_background(document_id)
    assert state(db, document_id) == ("active", "done", None)
    assert db.scalar(select(func.count()).select_from(DocumentChunk)) == 1


def test_loi_bat_ky_khong_thoat_ra_khoi_ham(db, monkeypatch):
    document_id = add_doc(db)

    def boom():
        raise RuntimeError("postgresql://user:matkhau@host:5432/db hỏng")

    monkeypatch.setattr(approval, "get_embedder", boom)
    approval.approve_document_background(document_id)  # không ném
    status, ingest_status, error = state(db, document_id)
    assert (status, ingest_status) == ("draft", "failed")
    assert "matkhau" not in error and "postgresql" not in error and len(error) <= 500


def test_van_ban_rong_failed(db):
    document_id = add_doc(db, content="   ")
    approval.approve_document_background(document_id)
    status, ingest_status, error = state(db, document_id)
    assert (status, ingest_status) == ("draft", "failed")
    assert "EmptyDocumentError" in error


def test_khong_tim_thay_van_ban_khong_ne_loi():
    approval.approve_document_background(uuid.uuid4())


def test_khong_mo_duoc_session_khong_ne_loi(monkeypatch):
    def broken():
        raise RuntimeError("DB chết")

    monkeypatch.setattr(approval, "_open_session", broken)
    approval.approve_document_background(uuid.uuid4())


def test_loi_o_buoc_chuyen_active_van_giu_nhap(db, monkeypatch):
    """Commit ghi chunk + chuyển active hỏng: văn bản vẫn draft (không lọt vào tìm kiếm), đánh dấu failed, không để lại chunk dở."""
    document_id = add_doc(db)
    real_commit = Session.commit
    calls = {"n": 0}

    def flaky_commit(self):
        calls["n"] += 1
        if calls["n"] == 1:  # lần 1 là commit của giai đoạn ghi (chunk + chuyển active chung một giao dịch)
            raise RuntimeError("mất kết nối")
        return real_commit(self)

    monkeypatch.setattr(Session, "commit", flaky_commit)
    approval.approve_document_background(document_id)
    monkeypatch.setattr(Session, "commit", real_commit)
    status, ingest_status, error = state(db, document_id)
    assert (status, ingest_status) == ("draft", "failed") and error
    assert db.scalar(select(func.count()).select_from(DocumentChunk)) == 0


def test_thong_bao_loi_cat_500_va_che_khoa(db, monkeypatch):
    document_id = add_doc(db)
    monkeypatch.setenv("GEMINI_API_KEY", "khoa-bi-mat-123")
    from app.core.config import get_settings

    get_settings.cache_clear()

    class Leaky(FakeEmbedder):
        def embed_documents(self, texts):
            raise EmbeddingError("khoa-bi-mat-123 AIzaSyA1234567890123456789012345 key=abc " + "y" * 900)

    monkeypatch.setattr(approval, "get_embedder", lambda: Leaky(768))
    approval.approve_document_background(document_id)
    _, _, error = state(db, document_id)
    assert len(error) == 500
    assert "khoa-bi-mat-123" not in error and "AIzaSy" not in error and "key=abc" not in error


# ── Phục hồi khi khởi động ─────────────────────────────────────────────────────

def test_phuc_hoi_processing_thanh_failed_khong_dung_trang_thai_khac(db):
    processing = add_doc(db, ingest_status="processing")
    pending = add_doc(db, ingest_status="pending")
    failed = add_doc(db, ingest_status="failed", ingest_error="loi cu")
    done = add_doc(db, ingest_status="done", status="active")
    assert approval.recover_interrupted_ingests(db) == 1
    assert state(db, processing) == ("draft", "failed", "Bị ngắt do service khởi động lại, hãy bấm duyệt lại")
    assert state(db, pending) == ("draft", "pending", None)
    assert state(db, failed) == ("draft", "failed", "loi cu")
    assert state(db, done) == ("active", "done", None)


def test_phuc_hoi_khi_khoi_dong_nuot_loi(monkeypatch):
    def broken():
        raise RuntimeError("không có DB")

    monkeypatch.setattr(approval, "_open_session", broken)
    approval.recover_on_startup()  # không ném


def test_phuc_hoi_khi_khoi_dong_chay_tren_session_rieng(db):
    document_id = add_doc(db, ingest_status="processing")
    approval.recover_on_startup()
    assert state(db, document_id)[1] == "failed"


@pytest.mark.parametrize("url,expected_calls", [("", 0), ("postgresql://x", 1)])
def test_lifespan_chi_phuc_hoi_khi_co_cau_hinh_database(monkeypatch, url, expected_calls):
    from app.core.config import get_settings
    from app.main import app

    monkeypatch.setenv("RAG_DATABASE_URL", url)
    monkeypatch.setenv("WARMUP_ON_STARTUP", "false")
    get_settings.cache_clear()
    calls = []
    monkeypatch.setattr("app.main.recover_on_startup", lambda: calls.append(1))
    with TestClient(app):
        pass
    assert len(calls) == expected_calls
