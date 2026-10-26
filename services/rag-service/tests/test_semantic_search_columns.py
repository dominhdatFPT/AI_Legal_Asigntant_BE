"""semantic_search không được kéo toàn văn luật và vector về theo từng dòng (làm truy vấn chậm 15s)."""

from sqlalchemy.dialects import postgresql

from app.repositories import document as document_repo


def test_semantic_search_khong_lay_toan_van_va_vector():
    captured = {}

    class FakeDB:
        def execute(self, stmt):
            captured["sql"] = str(stmt.compile(dialect=postgresql.dialect()))

            class R:
                def all(self):
                    return []

            return R()

    document_repo.semantic_search(FakeDB(), [0.0] * 768, 20, embedding_model="gemini-embedding-001")
    sql = captured["sql"]
    assert "legal_documents.content" not in sql
    assert "document_chunks.embedding," not in sql
    assert "<=>" in sql  # vẫn sắp xếp theo khoảng cách cosine trong DB
