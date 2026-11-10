"""Thay nội dung văn bản active: hàm so khớp chunk (thuần) và việc nền ba giai đoạn (không qua HTTP)."""

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DocumentChunk, DocumentVersion, IngestLog, LegalDocument
from app.services import document_approval as approval
from app.services import document_replace as replace
from app.services.chunking import Chunk, chunk_legal_text, embedding_text
from app.services.embedding import EmbeddingError, FakeEmbedder
from app.services.ingest import ingest_document

TITLE = "Luật Mẫu"
OLD = (
    "Điều 1. Phạm vi\nNội dung điều một.\n"
    "Điều 2. Đối tượng\nNội dung điều hai.\n"
    "Điều 3. Giải thích\nNội dung điều ba.\n"
    "Điều 4. Hiệu lực\nNội dung điều bốn."
)
NEW = OLD.replace("Nội dung điều hai.", "Nội dung điều hai đã được sửa đổi.")
MODEL = FakeEmbedder(768).model_name


class Counting(FakeEmbedder):
    """FakeEmbedder ghi lại các chuỗi được gửi đi embed."""

    def __init__(self, calls):
        super().__init__(768)
        self.calls = calls

    def embed_documents(self, texts):
        self.calls.append(list(texts))
        return super().embed_documents(texts)


def texts(content, title=TITLE):
    return [embedding_text(title, c) for c in chunk_legal_text(content)]


# ── Hàm thuần ──────────────────────────────────────────────────────────────────

def no_embed(_texts):
    raise AssertionError("không được gọi embedder")


def test_giong_het_dung_lai_het_khong_embed():
    old = [(t, "m") for t in texts(OLD)]
    plan = replace.reuse_plan(old, texts(OLD), "m")
    assert plan == [0, 1, 2, 3]
    vectors, reused, embedded = replace.fill_vectors(plan, [[1], [2], [3], [4]], texts(OLD), no_embed)
    assert (vectors, reused, embedded) == ([[1], [2], [3], [4]], 4, 0)


def test_doi_mot_dieu_chi_dieu_do_phai_embed():
    old = [(t, "m") for t in texts(OLD)]
    new = texts(NEW)
    plan = replace.reuse_plan(old, new, "m")
    assert plan == [0, None, 2, 3]
    calls = []

    def embed(batch):
        calls.append(batch)
        return [[99]]

    vectors, reused, embedded = replace.fill_vectors(plan, [[1], [2], [3], [4]], new, embed)
    assert vectors == [[1], [99], [3], [4]]  # đúng thứ tự chunk mới
    assert (reused, embedded) == (3, 1)
    assert calls == [[new[1]]]  # một lần gọi, chỉ chunk đổi


def test_chunk_cu_khac_model_khong_dung_lai():
    old = [(t, "model-cu") for t in texts(OLD)]
    assert replace.reuse_plan(old, texts(OLD), "model-moi") == [None] * 4
    mixed = [(t, "model-moi" if i % 2 == 0 else "model-cu") for i, t in enumerate(texts(OLD))]
    assert replace.reuse_plan(mixed, texts(OLD), "model-moi") == [0, None, 2, None]


def test_da_tap_hai_chunk_moi_giong_nhau_chi_mot_chunk_cu_khop():
    assert replace.reuse_plan([("A", "m")], ["A", "A"], "m") == [0, None]
    assert replace.reuse_plan([("A", "m"), ("A", "m")], ["A", "B", "A", "A"], "m") == [0, None, 1, None]


def test_ghep_vector_nhieu_chunk_can_embed_dung_thu_tu():
    plan = [None, 1, None, 0, None]
    new = ["a", "b", "c", "d", "e"]
    seen = []

    def embed(batch):
        seen.append(batch)
        return [[10 + i] for i in range(len(batch))]

    vectors, reused, embedded = replace.fill_vectors(plan, [["old0"], ["old1"]], new, embed)
    assert seen == [["a", "c", "e"]]
    assert vectors == [[10], ["old1"], [11], ["old0"], [12]]
    assert (reused, embedded) == (2, 3)


def test_embedder_tra_sai_so_vector_la_loi():
    with pytest.raises(EmbeddingError):
        replace.fill_vectors([None, None], [], ["a", "b"], lambda batch: [[1]])


def chunks_of(content, model=MODEL):
    return [(c.content, c.section_info, model) for c in chunk_legal_text(content)]


def test_so_sanh_dem_dung_va_liet_ke_dieu_doi():
    result = replace.compare_chunks(TITLE, chunks_of(OLD), chunk_legal_text(NEW), MODEL, batch_size=20)
    assert result["old_chunk_count"] == result["new_chunk_count"] == 4
    assert (result["reused_chunk_count"], result["embed_chunk_count"], result["removed_chunk_count"]) == (3, 1, 1)
    assert result["changed_articles"] == ["2"]
    assert (result["changed_article_count"], result["changed_articles_truncated"]) == (1, False)
    est = result["embed_estimate"]
    assert est["batch_size"] == 20 and est["batch_count"] == 1
    assert est["total_chars"] == len(texts(NEW)[1])  # chỉ tính phần phải embed


def test_so_sanh_giong_het_khong_co_gi_phai_embed():
    result = replace.compare_chunks(TITLE, chunks_of(OLD), chunk_legal_text(OLD), MODEL)
    assert (result["reused_chunk_count"], result["embed_chunk_count"], result["removed_chunk_count"]) == (4, 0, 0)
    assert result["changed_articles"] == []
    assert result["embed_estimate"] == {"total_chars": 0, "batch_size": result["embed_estimate"]["batch_size"], "batch_count": 0}


def test_dieu_bi_xoa_va_dieu_them_vao_deu_tinh_la_doi_sap_theo_so():
    new = OLD.replace("Điều 3. Giải thích\nNội dung điều ba.\n", "") + "\nĐiều 10. Mới thêm\nNội dung mười."
    result = replace.compare_chunks(TITLE, chunks_of(OLD), chunk_legal_text(new), MODEL)
    assert result["changed_articles"] == ["3", "10"]  # sắp theo số, không theo chữ ("10" < "3" nếu so chuỗi)
    assert result["removed_chunk_count"] == 1 and result["embed_chunk_count"] == 1


def test_liet_ke_toi_da_50_dieu_co_co_truncated():
    old = "\n".join(f"Điều {i}. A{i}\nNội dung {i}." for i in range(1, 71))
    new = "\n".join(f"Điều {i}. A{i}\nNội dung {i} đổi." for i in range(1, 71))
    result = replace.compare_chunks(TITLE, chunks_of(old), chunk_legal_text(new), MODEL, batch_size=20)
    assert len(result["changed_articles"]) == 50 and result["changed_article_count"] == 70
    assert result["changed_articles_truncated"] is True
    assert result["changed_articles"][:3] == ["1", "2", "3"]
    assert result["embed_estimate"]["batch_count"] == 4  # 70 chunk / 20 mỗi lô


def test_canh_bao_rieng_cho_viec_thay():
    def comparison(old_n, new_n, reused):
        return {"old_chunk_count": old_n, "new_chunk_count": new_n, "reused_chunk_count": reused}

    assert replace.replace_warnings(OLD, NEW, 4, 4, comparison(4, 4, 3)) == []
    fewer = replace.replace_warnings(OLD, NEW, 4, 3, comparison(4, 3, 3))
    assert len(fewer) == 1 and "ít hơn" in fewer[0]
    longer = replace.replace_warnings("x" * 100, "x" * 140, 1, 1, comparison(1, 1, 1))
    assert len(longer) == 1 and "40%" in longer[0]
    assert replace.replace_warnings("x" * 100, "x" * 130, 1, 1, comparison(1, 1, 1)) == []  # đúng 30% thì chưa cảnh báo
    none_reused = replace.replace_warnings(OLD, "Điều 1. Khác\nHoàn toàn khác.", 4, 1, comparison(4, 1, 0))
    assert any("Không có đoạn nào dùng lại" in w for w in none_reused)
    same = replace.replace_warnings(OLD, OLD + "\n\n", 4, 4, comparison(4, 4, 4))
    assert same == ["Nội dung mới giống hệt nội dung hiện tại, không có gì để thay."]


def test_build_replace_preview_gom_phan_tich_so_sanh_va_canh_bao():
    result = replace.build_replace_preview(TITLE, OLD, chunks_of(OLD), NEW, MODEL)
    assert result["chunk_count"] == 4 and result["article_count"] == 4  # kết quả analyze_document còn nguyên
    assert "warnings" in result and "embedding_estimate" in result and "sample_chunks" in result
    assert result["comparison"]["embed_chunk_count"] == 1
    assert result["replace_warnings"] == []


# ── Việc nền ───────────────────────────────────────────────────────────────────

@pytest.fixture
def events():
    return []


@pytest.fixture
def opened(db, events, monkeypatch):
    """Mọi session việc nền mở ra; một session ghi lại câu lệnh vào `events`. ingest_log cũng mở session qua chỗ này."""
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


@pytest.fixture
def calls(monkeypatch):
    seen: list[list[str]] = []
    monkeypatch.setattr(replace, "get_embedder", lambda: Counting(seen))
    return seen


def add_active_doc(db, content=OLD) -> uuid.UUID:
    doc = ingest_document(db, FakeEmbedder(768), title=TITLE, doc_type="luat", content=content, doc_number="1/2020/QH")
    assert (doc.status, doc.ingest_status) == ("active", "done")
    return doc.id


def claim(db, document_id):
    """Giống bước chiếm chỗ của endpoint: ingest_status = processing."""
    doc = db.get(LegalDocument, document_id)
    doc.ingest_status = "processing"
    db.commit()


def state(db, document_id):
    db.expire_all()
    d = db.get(LegalDocument, document_id)
    return d.status, d.ingest_status, d.ingest_error, d.chunk_count, d.content


def vectors_by_content(db, document_id):
    db.expire_all()
    return {c.content: list(c.embedding) for c in db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == document_id))}


def versions(db):
    db.expire_all()
    return list(db.scalars(select(DocumentVersion)).all())


def log_rows(db):
    db.expire_all()
    return list(db.scalars(select(IngestLog).order_by(IngestLog.created_at, IngestLog.id)).all())


def test_thay_mot_dieu_chi_embed_dieu_do_va_luu_ban_cu(db, opened, calls):
    document_id = add_active_doc(db)
    before = vectors_by_content(db, document_id)
    claim(db, document_id)
    replace.replace_document_background(document_id, NEW, "admin-1", "admin")

    status, ingest_status, error, chunk_count, content = state(db, document_id)
    assert (status, ingest_status, error, chunk_count, content) == ("active", "done", None, 4, NEW)
    assert len(calls) == 1 and len(calls[0]) == 1 and "đã được sửa đổi" in calls[0][0]
    after = vectors_by_content(db, document_id)
    assert len(after) == 4
    for content_, vector in after.items():
        if "đã được sửa đổi" not in content_:
            assert vector == before[content_]  # chunk không đổi giữ nguyên vector cũ
    (version,) = versions(db)
    assert (str(version.document_id), version.content, version.chunk_count, version.content_length) == (str(document_id), OLD, 4, len(OLD))
    assert version.replaced_by_user_id == "admin-1"
    done = log_rows(db)[-1]
    assert done.action == "replace_done" and (done.actor_user_id, done.actor_role) == ("admin-1", "admin")
    assert (done.chunk_count, done.detail) == (4, "dùng lại 3, embed 1")
    assert done.duration_ms is not None and done.duration_ms >= 0
    assert OLD not in repr([(r.title, r.detail) for r in log_rows(db)])  # toàn văn cũ chỉ nằm ở document_versions


def test_khong_co_chunk_nao_can_embed_thi_khong_goi_embedder(db, opened, calls):
    document_id = add_active_doc(db)
    claim(db, document_id)
    crlf = OLD.replace("\n", "\r\n") + "\r\n\r\n"  # chữ khác nhưng cắt ra đúng các chunk cũ
    replace.replace_document_background(document_id, crlf)
    assert calls == []
    status, ingest_status, _, chunk_count, content = state(db, document_id)
    assert (status, ingest_status, chunk_count, content) == ("active", "done", 4, crlf)
    assert len(versions(db)) == 1
    assert log_rows(db)[-1].detail == "dùng lại 4, embed 0"


def test_bo_bot_mot_dieu_giam_chunk_khong_embed(db, opened, calls):
    document_id = add_active_doc(db)
    claim(db, document_id)
    shorter = OLD.replace("Điều 4. Hiệu lực\nNội dung điều bốn.", "").strip()
    replace.replace_document_background(document_id, shorter)
    assert calls == []
    assert state(db, document_id)[3] == 3
    assert db.scalar(select(func.count()).select_from(DocumentChunk)) == 3


def test_chunk_cu_khac_model_thi_embed_lai_het(db, opened, calls):
    document_id = add_active_doc(db)
    for chunk in db.scalars(select(DocumentChunk)):
        chunk.embedding_model = "model-cu"
    db.commit()
    claim(db, document_id)
    replace.replace_document_background(document_id, NEW)
    assert len(calls) == 1 and len(calls[0]) == 4
    assert {m for (m,) in db.execute(select(DocumentChunk.embedding_model))} == {MODEL}


def test_loi_embedding_giu_nguyen_ban_cu_va_thu_lai_duoc(db, opened, monkeypatch, calls):
    from app.core.config import get_settings

    monkeypatch.setenv("GEMINI_API_KEY", "khoa-bi-mat-123")
    get_settings.cache_clear()
    document_id = add_active_doc(db)
    before = vectors_by_content(db, document_id)
    claim(db, document_id)

    class Leaky(FakeEmbedder):
        def embed_documents(self, texts):
            raise EmbeddingError("hết quota khoa-bi-mat-123 key=abc " + "y" * 900)

    monkeypatch.setattr(replace, "get_embedder", lambda: Leaky(768))
    replace.replace_document_background(document_id, NEW, "admin-1", "admin")  # không ném

    status, ingest_status, error, chunk_count, content = state(db, document_id)
    assert (status, ingest_status, chunk_count, content) == ("active", "failed", 4, OLD)
    assert error and len(error) <= 500 and "hết quota" in error
    assert "khoa-bi-mat-123" not in error and "key=abc" not in error
    assert vectors_by_content(db, document_id) == before  # chunk cũ còn nguyên
    assert versions(db) == []
    failed = log_rows(db)[-1]
    assert failed.action == "replace_failed" and failed.duration_ms >= 0
    assert "khoa-bi-mat-123" not in failed.detail

    monkeypatch.setattr(replace, "get_embedder", lambda: Counting(calls))  # thử lại từ failed (endpoint cho phép)
    claim(db, document_id)
    replace.replace_document_background(document_id, NEW, "admin-1", "admin")
    assert state(db, document_id)[:3] == ("active", "done", None)
    assert state(db, document_id)[4] == NEW
    assert len(versions(db)) == 1


def test_noi_dung_moi_rong_la_loi_va_khong_goi_embedder(db, opened, calls):
    document_id = add_active_doc(db)
    claim(db, document_id)
    replace.replace_document_background(document_id, "   \n  ")
    assert calls == []
    status, ingest_status, error, _, content = state(db, document_id)
    assert (status, ingest_status, content) == ("active", "failed", OLD)
    assert "EmptyDocumentError" in error
    assert versions(db) == []


def test_loi_o_giai_doan_ghi_rollback_het_ban_cu_con_nguyen(db, opened, calls, monkeypatch):
    document_id = add_active_doc(db)
    before = vectors_by_content(db, document_id)
    claim(db, document_id)

    def boom(*a, **kw):
        raise RuntimeError("ghi chunk hỏng")

    monkeypatch.setattr(replace.document_repo, "replace_chunks", boom)
    replace.replace_document_background(document_id, NEW)
    status, ingest_status, error, chunk_count, content = state(db, document_id)
    assert (status, ingest_status, chunk_count, content) == ("active", "failed", 4, OLD)
    assert "RuntimeError" in error and "ghi chunk hỏng" not in error
    assert versions(db) == []  # dòng document_versions đã thêm trong giao dịch cũng bị rollback
    assert vectors_by_content(db, document_id) == before


def test_khong_tim_thay_van_ban_va_khong_mo_duoc_session_khong_ne_loi(monkeypatch):
    replace.replace_document_background(uuid.uuid4(), NEW)

    def broken():
        raise RuntimeError("DB chết")

    monkeypatch.setattr(approval, "_open_session", broken)
    replace.replace_document_background(uuid.uuid4(), NEW)


def test_khi_embed_session_khong_dang_trong_giao_dich(db, opened, events, monkeypatch):
    document_id = add_active_doc(db)
    claim(db, document_id)
    seen = []

    class Spy(FakeEmbedder):
        def embed_documents(self, texts):
            seen.append([s.in_transaction() for s in opened])
            return super().embed_documents(texts)

    monkeypatch.setattr(replace, "get_embedder", lambda: Spy(768))
    replace.replace_document_background(document_id, NEW)
    assert seen == [[False]]  # đúng một session của việc nền, không trong giao dịch
    probe = Session(db.get_bind())  # đối chứng: đọc bằng session thường thì CÓ giao dịch mở
    probe.get(LegalDocument, document_id)
    assert probe.in_transaction()
    probe.close()
    assert state(db, document_id)[1] == "done"


def test_set_local_khong_phat_ra_tren_sqlite(db, opened, events, calls):
    document_id = add_active_doc(db)
    claim(db, document_id)
    replace.replace_document_background(document_id, NEW)
    assert not [e for e in events if e[0] == "set_local"]


def test_set_local_phat_ra_tren_postgresql_la_cau_dau_giao_dich_ghi(db, opened, events, monkeypatch):
    document_id = add_active_doc(db)
    claim(db, document_id)

    class Spy(FakeEmbedder):
        def embed_documents(self, texts):
            events.append(("embed",))
            return super().embed_documents(texts)

    monkeypatch.setattr(replace, "get_embedder", lambda: Spy(768))
    monkeypatch.setattr(db.get_bind().dialect, "name", "postgresql")  # giả lập dialect, câu SET chỉ được ghi lại, không chạy
    replace.replace_document_background(document_id, NEW)

    set_locals = [e for e in events if e[0] == "set_local"]
    assert len(set_locals) == 1 and set_locals[0][1] == "SET LOCAL statement_timeout = 0"
    assert set_locals[0][2] is False  # chưa có giao dịch nào: nó là câu đầu tiên của giao dịch ghi
    assert events[events.index(("embed",)) + 1][0] == "set_local"  # ngay sau khi embed xong
    assert state(db, document_id)[1] == "done"


def test_khong_dat_set_local_khi_loi_truoc_giai_doan_ghi(db, opened, events, monkeypatch):
    document_id = add_active_doc(db)
    claim(db, document_id)

    class Broken(FakeEmbedder):
        def embed_documents(self, texts):
            raise EmbeddingError("hết quota")

    monkeypatch.setattr(replace, "get_embedder", lambda: Broken(768))
    monkeypatch.setattr(db.get_bind().dialect, "name", "postgresql")
    replace.replace_document_background(document_id, NEW)
    assert not [e for e in events if e[0] == "set_local"]


def test_van_ban_bi_doi_giua_giai_doan_1_va_3_that_bai_an_toan(db, opened, monkeypatch):
    document_id = add_active_doc(db)
    claim(db, document_id)
    changed = OLD + "\nĐiều 5. Người khác vừa thêm\nNội dung năm."

    class Meddling(FakeEmbedder):
        def embed_documents(self, texts):
            other = Session(db.get_bind(), expire_on_commit=False)  # người khác sửa nội dung trong lúc đang embed
            other.get(LegalDocument, document_id).content = changed
            other.commit()
            other.close()
            return super().embed_documents(texts)

    monkeypatch.setattr(replace, "get_embedder", lambda: Meddling(768))
    replace.replace_document_background(document_id, NEW, "admin-1", "admin")

    status, ingest_status, error, chunk_count, content = state(db, document_id)
    assert (status, ingest_status, chunk_count, content) == ("active", "failed", 4, changed)  # không ghi đè bản của người khác
    assert "vừa bị đổi" in error
    assert versions(db) == []
    assert db.scalar(select(func.count()).select_from(DocumentChunk)) == 4  # chunk cũ còn nguyên
    assert log_rows(db)[-1].action == "replace_failed"


def test_van_ban_bi_doi_status_giua_chung_cung_that_bai_an_toan(db, opened, monkeypatch):
    document_id = add_active_doc(db)
    claim(db, document_id)

    class Meddling(FakeEmbedder):
        def embed_documents(self, texts):
            other = Session(db.get_bind(), expire_on_commit=False)
            other.get(LegalDocument, document_id).status = "superseded"
            other.commit()
            other.close()
            return super().embed_documents(texts)

    monkeypatch.setattr(replace, "get_embedder", lambda: Meddling(768))
    replace.replace_document_background(document_id, NEW)
    status, ingest_status, _, _, content = state(db, document_id)
    assert (status, ingest_status, content) == ("superseded", "failed", OLD)
    assert versions(db) == []


def test_chu_ky_chi_can_id_va_noi_dung_nguoi_goi_tuy_chon(db, opened, calls):
    document_id = add_active_doc(db)
    claim(db, document_id)
    replace.replace_document_background(document_id, NEW)
    (version,) = versions(db)
    assert version.replaced_by_user_id is None
    done = log_rows(db)[-1]
    assert (done.action, done.actor_user_id, done.actor_role) == ("replace_done", None, None)


def test_recover_van_ban_active_dang_processing_thanh_failed_giu_nguyen_status(db):
    document_id = add_active_doc(db)
    claim(db, document_id)
    assert approval.recover_interrupted_ingests(db) == 1
    status, ingest_status, error, chunk_count, content = state(db, document_id)
    assert (status, ingest_status, chunk_count, content) == ("active", "failed", 4, OLD)
    assert error == approval.INTERRUPTED_MESSAGE
    assert db.get(LegalDocument, document_id).is_processed is True  # vẫn tìm kiếm được
