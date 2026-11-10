"""Việc duyệt nền chia ba giai đoạn: đọc, embed (không giữ giao dịch), ghi (một giao dịch ngắn)."""

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DocumentChunk, LegalDocument
from app.services import document_approval as approval
from app.services.chunking import chunk_legal_text
from app.services.embedding import EmbeddingError, FakeEmbedder

CONTENT = "Điều 1. A\nNội dung điều một.\nĐiều 2. B\nNội dung điều hai."


@pytest.fixture
def events():
    return []


@pytest.fixture
def opened(db, events, monkeypatch):
    """Mọi session việc nền mở ra, và một session ghi lại câu lệnh đã chạy vào `events`."""
    sessions: list[Session] = []

    class Recording(Session):
        def execute(self, statement, *args, **kwargs):
            sql = str(statement)
            if sql.lstrip().startswith("SET LOCAL"):
                events.append(("set_local", sql, self.in_transaction()))
                return None  # SQLite không có câu này, chỉ ghi lại
            events.append(("sql", sql.lstrip()[:20], self.in_transaction()))
            return super().execute(statement, *args, **kwargs)

    def open_session():
        session = Recording(db.get_bind(), expire_on_commit=False)
        sessions.append(session)
        return session

    monkeypatch.setattr(approval, "_open_session", open_session)
    return sessions


def add_doc(db, content=CONTENT) -> uuid.UUID:
    doc = LegalDocument(title="Luật A", doc_type="luat", content=content, status="draft", ingest_status="processing")
    db.add(doc)
    db.commit()
    return doc.id


def state(db, document_id):
    db.expire_all()
    d = db.get(LegalDocument, document_id)
    return d.status, d.ingest_status, d.ingest_error, d.chunk_count, d.is_processed


def chunk_total(db) -> int:
    return db.scalar(select(func.count()).select_from(DocumentChunk))


def test_khi_embed_session_khong_dang_trong_giao_dich(db, opened, events, monkeypatch):
    document_id = add_doc(db)
    seen = []

    class Spy(FakeEmbedder):
        def embed_documents(self, texts):
            seen.append([s.in_transaction() for s in opened])
            events.append(("embed",))
            return super().embed_documents(texts)

    monkeypatch.setattr(approval, "get_embedder", lambda: Spy(768))
    approval.approve_document_background(document_id)

    assert seen == [[False]]  # đúng một session của việc nền, không trong giao dịch
    # đối chứng: đọc một dòng bằng session thường thì CÓ giao dịch mở (in_transaction đo đúng điều cần đo)
    probe = Session(db.get_bind())
    probe.get(LegalDocument, document_id)
    assert probe.in_transaction()
    probe.close()
    assert state(db, document_id)[:2] == ("active", "done")


def test_ghi_chunk_dung_sau_khi_embed(db, opened, monkeypatch):
    document_id = add_doc(db)
    monkeypatch.setattr(approval, "get_embedder", lambda: FakeEmbedder(768))
    approval.approve_document_background(document_id)

    expected = len(chunk_legal_text(CONTENT))
    assert state(db, document_id) == ("active", "done", None, expected, True)
    assert chunk_total(db) == expected
    assert {m for (m,) in db.execute(select(DocumentChunk.embedding_model))} == {FakeEmbedder(768).model_name}
    assert [c.chunk_index for c in db.scalars(select(DocumentChunk).order_by(DocumentChunk.chunk_index))] == list(range(expected))


def test_ghi_de_chunk_cu_khi_duyet_lai(db, opened, monkeypatch):
    document_id = add_doc(db)
    db.add(DocumentChunk(document_id=document_id, chunk_index=0, content="cu", embedding=[0.0] * 768, embedding_model="cu"))
    db.add(DocumentChunk(document_id=document_id, chunk_index=7, content="cu", embedding=[0.0] * 768, embedding_model="cu"))
    db.commit()
    monkeypatch.setattr(approval, "get_embedder", lambda: FakeEmbedder(768))
    approval.approve_document_background(document_id)
    assert chunk_total(db) == len(chunk_legal_text(CONTENT))
    assert db.scalar(select(func.count()).select_from(DocumentChunk).where(DocumentChunk.content == "cu")) == 0


def test_loi_embedding_failed_khong_chunk_van_draft(db, opened, monkeypatch):
    document_id = add_doc(db)

    class Broken(FakeEmbedder):
        def embed_documents(self, texts):
            raise EmbeddingError("hết quota")

    monkeypatch.setattr(approval, "get_embedder", lambda: Broken(768))
    approval.approve_document_background(document_id)
    status, ingest_status, error, chunk_count, processed = state(db, document_id)
    assert (status, ingest_status, chunk_count, processed) == ("draft", "failed", 0, False)
    assert "hết quota" in error
    assert chunk_total(db) == 0


def test_loi_o_giai_doan_ghi_rollback_het_va_failed(db, opened, monkeypatch):
    document_id = add_doc(db)
    monkeypatch.setattr(approval, "get_embedder", lambda: FakeEmbedder(768))

    def boom(*a, **kw):
        raise RuntimeError("ghi chunk hỏng")

    monkeypatch.setattr(approval.document_repo, "replace_chunks", boom)
    approval.approve_document_background(document_id)
    status, ingest_status, error, chunk_count, processed = state(db, document_id)
    assert (status, ingest_status, chunk_count, processed) == ("draft", "failed", 0, False)
    assert "RuntimeError" in error and "ghi chunk hỏng" not in error  # lỗi lạ không lộ chi tiết
    assert chunk_total(db) == 0


def test_van_ban_rong_failed_khong_goi_embedder(db, opened, monkeypatch):
    document_id = add_doc(db, content="   ")

    def boom():
        raise AssertionError("văn bản rỗng không được gọi embedder")

    monkeypatch.setattr(approval, "get_embedder", boom)
    approval.approve_document_background(document_id)
    assert state(db, document_id)[:2] == ("draft", "failed")
    assert "EmptyDocumentError" in state(db, document_id)[2]


def test_set_local_khong_phat_ra_tren_sqlite(db, opened, events, monkeypatch):
    document_id = add_doc(db)
    monkeypatch.setattr(approval, "get_embedder", lambda: FakeEmbedder(768))
    approval.approve_document_background(document_id)
    assert not [e for e in events if e[0] == "set_local"]


def test_set_local_phat_ra_tren_postgresql_la_cau_dau_giao_dich_ghi(db, opened, events, monkeypatch):
    document_id = add_doc(db)

    class Spy(FakeEmbedder):
        def embed_documents(self, texts):
            events.append(("embed",))
            return super().embed_documents(texts)

    monkeypatch.setattr(approval, "get_embedder", lambda: Spy(768))
    monkeypatch.setattr(db.get_bind().dialect, "name", "postgresql")  # giả lập dialect, câu SET chỉ được ghi lại, không chạy
    approval.approve_document_background(document_id)

    set_locals = [e for e in events if e[0] == "set_local"]
    assert len(set_locals) == 1
    assert set_locals[0][1] == "SET LOCAL statement_timeout = 0"
    assert set_locals[0][2] is False  # chưa có giao dịch nào: nó là câu đầu tiên của giao dịch ghi (SET LOCAL tự mở giao dịch)
    # sau khi embed xong, câu đầu tiên phát ra là SET LOCAL
    after_embed = events[events.index(("embed",)) + 1:]
    assert after_embed[0][0] == "set_local"
    assert state(db, document_id)[:2] == ("active", "done")


def test_khong_dat_set_local_khi_loi_truoc_giai_doan_ghi(db, opened, events, monkeypatch):
    document_id = add_doc(db)

    class Broken(FakeEmbedder):
        def embed_documents(self, texts):
            raise EmbeddingError("hết quota")

    monkeypatch.setattr(approval, "get_embedder", lambda: Broken(768))
    monkeypatch.setattr(db.get_bind().dialect, "name", "postgresql")
    approval.approve_document_background(document_id)
    assert not [e for e in events if e[0] == "set_local"]


def test_khong_ghi_nhat_ky_khi_khong_truyen_nguoi_goi_van_chay(db, opened, monkeypatch):
    """Chữ ký cũ approve_document_background(id) vẫn dùng được: người gọi là tham số tùy chọn."""
    from app.models import IngestLog

    document_id = add_doc(db)
    monkeypatch.setattr(approval, "get_embedder", lambda: FakeEmbedder(768))
    approval.approve_document_background(document_id)
    (row,) = db.scalars(select(IngestLog)).all()
    assert (row.action, row.actor_user_id, row.actor_role) == ("approve_done", None, None)
