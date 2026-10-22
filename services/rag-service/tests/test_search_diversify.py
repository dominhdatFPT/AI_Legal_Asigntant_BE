from types import SimpleNamespace

from app.services import search


class SpyEmbedder:
    model_name = "fake"

    def __init__(self):
        self.calls = 0

    def embed_query(self, text):
        self.calls += 1
        return [0.0]


def _row(i, doc_id, dieu):
    chunk = SimpleNamespace(id=f"c{i}", content=f"đoạn {i}", section_info={"dieu": dieu})
    doc = SimpleNamespace(id=doc_id, title="Luật Mẫu", doc_number="01/2099/QH-MAU", doc_type="luat")
    return chunk, doc, 0.1 + i / 100


def test_moi_dieu_toi_da_2_doan(monkeypatch):
    rows = [_row(i, "d1", "133") for i in range(5)] + [_row(5, "d1", "134"), _row(6, "d2", "133"), _row(7, "d1", None)]
    asked = {}

    def fake_semantic_search(db, vec, top_k, **kw):
        asked["top_k"] = top_k
        return rows

    monkeypatch.setattr(search.document_repo, "semantic_search", fake_semantic_search)
    out = search.search_chunks(None, SpyEmbedder(), "câu hỏi", top_k=4)
    assert asked["top_k"] == 4 * search.OVERFETCH
    # 2 đoạn Đ.133 của d1, rồi Đ.134 của d1, rồi Đ.133 của văn bản khác (d2)
    assert [c["id"] for c in out] == ["c0", "c1", "c5", "c6"]


def test_toan_mot_dieu_van_du_top_k(monkeypatch):
    # Lỗi gặp thật 20/10: 20 đoạn đều thuộc Đ.133 -> chỉ trả 2 đoạn. Phải bù đủ top_k.
    rows = [_row(i, "d1", "133") for i in range(20)]
    monkeypatch.setattr(search.document_repo, "semantic_search", lambda *a, **k: rows)
    out = search.search_chunks(None, SpyEmbedder(), "sang tên", top_k=4)
    assert [c["id"] for c in out] == ["c0", "c1", "c2", "c3"]


def test_doan_khong_co_dieu_khong_bi_gioi_han(monkeypatch):
    rows = [_row(i, "d1", None) for i in range(4)]
    monkeypatch.setattr(search.document_repo, "semantic_search", lambda *a, **k: rows)
    out = search.search_chunks(None, SpyEmbedder(), "câu hỏi", top_k=4)
    assert len(out) == 4


def test_cau_hoi_trung_chi_goi_embedding_1_lan(monkeypatch):
    monkeypatch.setattr(search.document_repo, "semantic_search", lambda *a, **k: [])
    emb = SpyEmbedder()
    search.search_chunks(None, emb, "thủ tục tách thửa", top_k=4)
    search.search_chunks(None, emb, "thủ tục tách thửa", top_k=4)
    assert emb.calls == 1
    search.search_chunks(None, emb, "câu khác hẳn", top_k=4)
    assert emb.calls == 2


def test_loi_embedding_khong_bi_cache(monkeypatch):
    monkeypatch.setattr(search.document_repo, "semantic_search", lambda *a, **k: [])

    class Flaky(SpyEmbedder):
        def embed_query(self, text):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("Gemini 503")
            return [0.0]

    emb = Flaky()
    try:
        search.search_chunks(None, emb, "hỏi lại", top_k=4)
    except RuntimeError:
        pass
    search.search_chunks(None, emb, "hỏi lại", top_k=4)
    assert emb.calls == 2
