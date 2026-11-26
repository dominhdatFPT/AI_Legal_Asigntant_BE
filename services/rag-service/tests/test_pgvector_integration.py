"""Test với PostgreSQL + pgvector thật. Mặc định BỎ QUA.

Chạy khi có database thử (KHÔNG dùng database Supabase chính, test sẽ xóa sạch 2 bảng):
    $env:RAG_TEST_DATABASE_URL="postgresql://user:pass@localhost:5432/rag_test"   # PowerShell
    pytest services/rag-service/tests/test_pgvector_integration.py
"""

import os
from pathlib import Path

import pytest

DB_URL = os.environ.get("RAG_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="Chưa đặt RAG_TEST_DATABASE_URL")

SCHEMA = Path(__file__).resolve().parents[1] / "database" / "001_rag_schema.sql"
CHECK = Path(__file__).resolve().parents[1] / "database" / "check_rag_db.sql"


@pytest.fixture
def pg():
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    url = DB_URL.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("drop table if exists document_chunks, legal_documents cascade"))
        conn.exec_driver_sql(SCHEMA.read_text(encoding="utf-8"))
        conn.exec_driver_sql(SCHEMA.read_text(encoding="utf-8"))  # chạy lần 2 không lỗi
    with Session(engine, expire_on_commit=False) as s:
        yield s
    engine.dispose()


def test_check_sql_toan_ok(pg):
    from sqlalchemy import text

    rows = pg.execute(text(CHECK.read_text(encoding="utf-8"))).all()
    for stt, hang_muc, ket_qua in rows:
        if stt <= 9:
            assert ket_qua == "OK", hang_muc


def test_nap_roi_tim_kiem(pg, sample_text):
    from app.services.embedding import FakeEmbedder
    from app.services.ingest import ingest_document
    from app.services.search import search_chunks

    emb = FakeEmbedder(768)
    # The hashed embedder matches words rather than intent. This clause appears in Article 4,
    # while Article 2 also uses the generic phrase about transfer of land-use rights.
    query = "hợp đồng chuyển nhượng có công chứng hoặc chứng thực và tờ khai lệ phí trước bạ"
    ingest_document(pg, emb, title="Luật Mẫu", doc_type="luat", content=sample_text, doc_number="01/2099/QH-MAU")
    ingest_document(pg, emb, title="Nghị định Mẫu", doc_type="nghi_dinh", status="superseded",
                    content=f"Điều 1. Hồ sơ chuyển nhượng quyền sử dụng đất\n{query}. Văn bản đã hết hiệu lực.")

    results = search_chunks(pg, emb, query, top_k=3)
    assert results[0]["section_info"]["dieu"] == "4"
    assert results[0]["document"]["doc_number"] == "01/2099/QH-MAU"
    assert 0 <= results[-1]["score"] <= results[0]["score"] <= 1
    # văn bản superseded không xuất hiện
    assert all(r["document"]["title"] == "Luật Mẫu" for r in results)

    assert search_chunks(pg, emb, "chuyển nhượng", top_k=3, doc_types=["thong_tu"]) == []

    # chunk tạo bằng model khác thì không được so sánh
    other = FakeEmbedder(768)
    other.model_name = "model-khac"
    assert search_chunks(pg, other, "chuyển nhượng", top_k=3) == []
