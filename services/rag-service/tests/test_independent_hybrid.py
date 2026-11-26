"""Eligible-corpus BM25/exact lookup use independent, safely closed sessions."""
from sqlalchemy import event

from app.models import DocumentChunk
from app.services import hybrid
from tests.test_hybrid import _add_doc


def test_lexical_and_exact_corpus_filters_and_separate_session(db):
    good = _add_doc(db, "31/2024/QH15", ["nội dung chung", "quy tắc đặt cọc năm phần trăm"])
    _add_doc(db, "32/2024/QH15", ["đặt cọc"], status="draft")
    _add_doc(db, "33/2024/QH15", ["đặt cọc"], status="superseded")
    wrong_model = _add_doc(db, "34/2024/QH15", ["đặt cọc"])
    for c in db.query(DocumentChunk).filter_by(document_id=wrong_model.id):
        c.embedding_model = "other-model"
    wrong_type = _add_doc(db, "35/2024/QH15", ["đặt cọc"])
    wrong_type.doc_type = "nghi_dinh"
    db.commit()
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        lexical, exact = hybrid.independent_rankings(
            db, "Điều 1 Luật số 31/2024/QH15 đặt cọc", "fake", ["luat"], 10,
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert lexical and exact
    assert all(r[1].id == good.id for r in lexical + exact)
    assert len(exact) == 1 and exact[0][0].section_info["dieu"] == "1"
    assert exact[0][0].content == "quy tắc đặt cọc năm phần trăm"  # usable after session close
    select_columns = [stmt.lower().split("from")[0] for stmt in statements]
    assert all("document_chunks.embedding," not in s and "legal_documents.content" not in s for s in select_columns)
    assert db.is_active  # auxiliary session was closed without closing/rolling back caller


def test_exact_article_and_document_lookup_without_vector(db):
    _add_doc(db, "31/2024/QH15", ["alpha", "beta", "gamma"])
    _, article = hybrid.independent_rankings(db, "Điều 2", "fake", None, 10)
    assert [r[0].content for r in article] == ["gamma"]
    _, document = hybrid.independent_rankings(db, "31/2024/QH15", "fake", None, 10)
    assert len(document) == 3
    assert hybrid.independent_rankings(db, "Điều 2 99/2024/QH15", "fake", None, 10)[1] == []


def test_lexical_corpus_does_not_depend_on_vector_candidates(db):
    _add_doc(db, "1/2024/QH15", ["arbitrary unrelated words", "uniquelexicaltoken"])
    lexical, exact = hybrid.independent_rankings(db, "uniquelexicaltoken", "fake", None, 10)
    assert [r[0].content for r in lexical] == ["uniquelexicaltoken"]
    assert not exact
