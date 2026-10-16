import pytest
from sqlalchemy import func, select

from app.models import DocumentChunk, LegalDocument
from app.services.embedding import EmbeddingError, FakeEmbedder
from app.services.ingest import DocumentExistsError, EmptyDocumentError, ingest_document, reembed_document


def _ingest(db, text, **kw):
    params = {"title": "Luật Mẫu", "doc_type": "luat", "content": text, "doc_number": "01/2099/QH-MAU"}
    params.update(kw)
    return ingest_document(db, FakeEmbedder(768), **params)


def test_nap_van_ban_luu_doc_va_chunk(db, sample_text):
    doc = _ingest(db, sample_text)
    assert doc.chunk_count == 6 and doc.is_processed
    chunks = db.scalars(select(DocumentChunk).order_by(DocumentChunk.chunk_index)).all()
    assert [c.chunk_index for c in chunks] == list(range(6))
    assert chunks[3].section_info["dieu"] == "4"
    assert chunks[3].embedding_model == "fake-hash-768"
    assert len(chunks[3].embedding) == 768
    assert db.get(LegalDocument, doc.id).content == sample_text


def test_trung_so_hieu_bao_loi(db, sample_text):
    _ingest(db, sample_text)
    with pytest.raises(DocumentExistsError):
        _ingest(db, sample_text)


def test_van_ban_rong_bao_loi(db):
    with pytest.raises(EmptyDocumentError):
        _ingest(db, "   ")


def test_loi_embedding_khong_ghi_gi(db, sample_text):
    class Broken(FakeEmbedder):
        def embed_documents(self, texts):
            raise EmbeddingError("hết quota")

    with pytest.raises(EmbeddingError):
        ingest_document(db, Broken(768), title="X", doc_type="luat", content=sample_text)
    assert db.scalar(select(func.count()).select_from(LegalDocument)) == 0
    assert db.scalar(select(func.count()).select_from(DocumentChunk)) == 0


def test_embedding_lai_thay_chunk_cu(db, sample_text):
    doc = _ingest(db, sample_text)

    class Other(FakeEmbedder):
        def __init__(self):
            super().__init__(768)
            self.model_name = "model-moi"

    reembed_document(db, Other(), doc, max_chars=300)
    models = set(db.scalars(select(DocumentChunk.embedding_model)).all())
    assert models == {"model-moi"}
    assert doc.chunk_count == db.scalar(select(func.count()).select_from(DocumentChunk)) > 6


def test_xoa_van_ban_xoa_luon_chunk(db, sample_text):
    doc = _ingest(db, sample_text)
    db.delete(doc)
    db.commit()
    assert db.scalar(select(func.count()).select_from(DocumentChunk)) == 0
