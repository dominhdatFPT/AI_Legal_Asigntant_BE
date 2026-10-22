"""search_chunks với câu hỏi nhiều ý: tìm theo câu gốc + từng ý rồi xếp xen kẽ."""
import threading
from types import SimpleNamespace

import pytest

from app.services import search
from app.services.embedding import EmbeddingError

QUESTION = "Tôi muốn tách thửa đất rồi tặng cho con một phần, cần điều kiện gì và thời hạn sử dụng đất thế nào?"


class TextEmbedder:
    """Vector = [số thứ tự của câu], để fake_semantic_search biết đang tìm cho câu nào."""

    model_name = "fake"

    def __init__(self):
        self.texts = []
        self._lock = threading.Lock()  # search.py gọi embedding song song khi câu có nhiều ý

    def embed_query(self, text):
        with self._lock:
            self.texts.append(text)
            return [float(len(self.texts) - 1)]


def _row(cid, dieu, distance, doc_id="d1"):
    chunk = SimpleNamespace(id=cid, content=f"đoạn {cid}", section_info={"dieu": dieu})
    doc = SimpleNamespace(id=doc_id, title="Luật Đất đai 2024", doc_number="31/2024/QH15", doc_type="luat")
    return chunk, doc, distance


@pytest.fixture(autouse=True)
def no_expansion(monkeypatch):
    """Tắt đổi từ đời thường để câu gửi đi embedding đúng nguyên văn."""
    from app.core.config import get_settings

    monkeypatch.setenv("QUERY_EXPANSION_ENABLED", "false")
    get_settings.cache_clear()


def _fake_db(monkeypatch, results_by_text, embedder):
    calls = []

    def fake_semantic_search(db, vec, top_k, **kw):
        text = embedder.texts[int(vec[0])]
        calls.append(text)
        return results_by_text.get(text, [])

    monkeypatch.setattr(search.document_repo, "semantic_search", fake_semantic_search)
    return calls


def test_moi_y_deu_co_doan_cua_minh(monkeypatch):
    emb = TextEmbedder()
    results = {
        # Câu gốc nghiêng hẳn về tách thửa: 5 đoạn rất gần đều là tách thửa (Đ.220)
        QUESTION: [_row(f"g{i}", "220", 0.10 + i / 100) for i in range(5)],
        "Tôi muốn tách thửa đất": [_row("g0", "220", 0.10), _row("t1", "220", 0.12)],
        "tặng cho con một phần, cần điều kiện gì (thửa đất)": [_row("c45", "45", 0.30)],
        "thời hạn sử dụng đất thế nào": [_row("h172", "172", 0.35)],
    }
    calls = _fake_db(monkeypatch, results, emb)

    out = search.search_chunks(None, emb, QUESTION, top_k=4)

    assert calls[0] == QUESTION and len(calls) == 4  # câu gốc + 3 ý
    dieu = {c["section_info"]["dieu"] for c in out}
    assert dieu == {"220", "45", "172"}  # trước đây chỉ có Đ.220
    assert len(out) == 4
    assert len({c["id"] for c in out}) == 4  # không trùng đoạn
    assert [c["score"] for c in out] == sorted((c["score"] for c in out), reverse=True)


def test_tat_tach_cau_thi_chi_tim_1_lan(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("QUERY_SPLIT_ENABLED", "false")
    get_settings.cache_clear()
    emb = TextEmbedder()
    calls = _fake_db(monkeypatch, {QUESTION: [_row(f"g{i}", "220", 0.1 + i / 100) for i in range(5)]}, emb)

    out = search.search_chunks(None, emb, QUESTION, top_k=4)

    assert calls == [QUESTION]
    assert {c["section_info"]["dieu"] for c in out} == {"220"}


def test_cau_mot_y_chi_tim_1_lan(monkeypatch):
    emb = TextEmbedder()
    calls = _fake_db(monkeypatch, {}, emb)
    search.search_chunks(None, emb, "sang tên sổ đỏ cần những giấy tờ gì", top_k=4)
    assert calls == ["sang tên sổ đỏ cần những giấy tờ gì"]


def test_loi_embedding_o_mot_y_bao_loi_nhu_cu(monkeypatch):
    """Một ý lỗi Gemini thì cả lần tìm báo EmbeddingError -> API trả 503 như trước, không trả nửa vời."""

    class Flaky(TextEmbedder):
        def embed_query(self, text):
            if text.startswith("thời hạn"):
                raise EmbeddingError("Gemini 503")
            return super().embed_query(text)

    _fake_db(monkeypatch, {}, Flaky())
    with pytest.raises(EmbeddingError):
        search.search_chunks(None, Flaky(), QUESTION, top_k=4)
