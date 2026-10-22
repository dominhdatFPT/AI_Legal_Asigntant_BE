import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.services import query_expansion, search
from app.services.query_expansion import expand, legal_terms

SERVICE_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("query, term", [
    ("sang tên sổ đỏ cần giấy tờ gì", "Giấy chứng nhận quyền sử dụng đất"),
    ("sang tên sổ đỏ cần giấy tờ gì", "đăng ký biến động"),
    ("mua đất thì cần làm những thủ tục gì", "chuyển nhượng quyền sử dụng đất"),
    ("MUA DAT can gi", "chuyển nhượng quyền sử dụng đất"),  # gõ hoa, không dấu
    ("muốn lên thổ cư", "chuyển mục đích sử dụng đất"),
    ("tách sổ cho con", "tách thửa đất"),
    ("đóng cửa công ty", "giải thể doanh nghiệp"),
    ("mở quán cà phê cần giấy gì", "đăng ký hộ kinh doanh"),
])
def test_them_thuat_ngu_luat(query, term):
    assert any(term in t for t in legal_terms(query))


def test_giu_nguyen_cau_goc_va_them_vao_cuoi():
    out = expand("mua đất cần gì")
    assert out.startswith("mua đất cần gì (") and out.endswith(")")
    assert "chuyển nhượng quyền sử dụng đất" in out


@pytest.mark.parametrize("query", [
    "Điều 188 Luật Đất đai nói gì",
    "chuyển nhượng quyền sử dụng đất cần điều kiện gì",  # đã là từ ngữ luật
    "amstel",  # "mst" chỉ khớp nguyên từ
    "cho thuê đất",  # không khớp nhầm "cho đất"
])
def test_khong_doi_cau_khong_can_doi(query):
    assert expand(query) == query


def test_khong_lap_thuat_ngu():
    terms = legal_terms("sổ đỏ sổ hồng bìa đỏ")
    assert len(terms) == len(set(terms)) == 1


def test_search_dung_cau_da_mo_rong(monkeypatch):
    seen = []

    class FakeEmbedder:
        model_name = "fake"

        def embed_query(self, text):
            seen.append(text)
            return [0.0]

    monkeypatch.setattr(search.document_repo, "semantic_search", lambda *a, **k: [])
    search.search_chunks(None, FakeEmbedder(), "sang tên sổ đỏ", top_k=4)
    assert "đăng ký biến động" in seen[0]


def test_tat_mo_rong_bang_cau_hinh(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("QUERY_EXPANSION_ENABLED", "false")
    get_settings.cache_clear()
    seen = []

    class FakeEmbedder:
        model_name = "fake"

        def embed_query(self, text):
            seen.append(text)
            return [0.0]

    monkeypatch.setattr(search.document_repo, "semantic_search", lambda *a, **k: [])
    search.search_chunks(None, FakeEmbedder(), "sang tên sổ đỏ", top_k=4)
    assert seen == ["sang tên sổ đỏ"]


def test_warmup_loi_khong_lam_chet_service(monkeypatch):
    import app.main as main

    def broken_engine():
        raise RuntimeError("Chưa cấu hình RAG_DATABASE_URL")

    monkeypatch.setattr(main, "get_engine", broken_engine)
    with TestClient(main.app) as client:  # chạy lifespan -> warmup
        assert client.get("/api/v1/rag/health").status_code == 200


def test_bo_cau_hoi_danh_gia_hop_le():
    data = json.loads((SERVICE_ROOT / "scripts" / "eval_questions.json").read_text(encoding="utf-8"))
    assert len(data) >= 20
    for item in data:
        assert item["q"] and item["doc"] and item["dieu"]
        assert all(isinstance(d, str) for d in item["dieu"])


def test_moi_tu_dong_nghia_deu_co_thuat_ngu():
    for phrases, legal in query_expansion.SYNONYMS:
        assert phrases and legal
