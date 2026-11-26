"""semantic_search không được kéo toàn văn luật và vector về theo từng dòng (làm truy vấn chậm 15s)."""

from types import SimpleNamespace

import pytest
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


@pytest.mark.parametrize(
    "dialect,top_k,expected_setting",
    [
        ("postgresql", 20, None),
        ("postgresql", 21, "SET LOCAL hnsw.ef_search = 64"),
        ("postgresql", 32, "SET LOCAL hnsw.ef_search = 64"),
        ("postgresql", 33, "SET LOCAL hnsw.ef_search = 66"),
        ("postgresql", 40, "SET LOCAL hnsw.ef_search = 80"),
        ("postgresql", 41, "SET LOCAL hnsw.ef_search = 82"),
        ("postgresql", 60, "SET LOCAL hnsw.ef_search = 120"),
        ("postgresql", 100, "SET LOCAL hnsw.ef_search = 200"),
        ("sqlite", 21, None),
        ("sqlite", 100, None),
    ],
)
def test_hnsw_overfetch_uses_transaction_local_setting(dialect, top_k, expected_setting):
    statements = []

    class FakeDB:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name=dialect))

        def execute(self, stmt):
            statements.append(stmt)
            return SimpleNamespace(all=lambda: [])

    assert document_repo.semantic_search(
        FakeDB(), [0.0] * 768, top_k, embedding_model="gemini-embedding-001"
    ) == []

    if expected_setting is None:
        assert len(statements) == 1
    else:
        assert len(statements) == 2
        assert str(statements[0]) == expected_setting

    # SELECT phải chạy sau SET LOCAL và vẫn giữ đúng LIMIT được yêu cầu.
    compiled = statements[-1].compile(dialect=postgresql.dialect())
    assert "LIMIT" in str(compiled) and "<=>" in str(compiled)
    assert top_k in compiled.params.values()
