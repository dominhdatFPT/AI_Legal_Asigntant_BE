"""Kiểm hàm thuần app/services/document_preview.py bằng vài đoạn văn bản ngắn tự viết."""

import pytest

from app.services.chunking import chunk_legal_text, embedding_text
from app.services.document_preview import EmptyContentError, analyze_document


def _dieu(*numbers: str) -> str:
    return "\n".join(f"Điều {n}. Tên điều {n}\nNội dung điều {n}." for n in numbers)


def test_van_ban_mau_khop_chunk_legal_text(sample_text):
    r = analyze_document(sample_text, "Luật Mẫu")
    chunks = chunk_legal_text(sample_text)
    assert r["chunk_count"] == len(chunks)
    assert r["article_count"] == len({c.section_info["dieu"] for c in chunks})
    assert r["first_article"] == "1" and r["last_article"] == "6"
    assert sum(r["chunks_by_level"].values()) == len(chunks)
    assert r["embedding_estimate"]["total_chars"] == sum(len(embedding_text("Luật Mẫu", c)) for c in chunks)
    assert r["warnings"] == []


def test_so_lo_embedding_theo_batch_size(sample_text):
    n = len(chunk_legal_text(sample_text))
    r = analyze_document(sample_text, "Luật Mẫu", batch_size=2)
    assert r["embedding_estimate"]["batch_size"] == 2
    assert r["embedding_estimate"]["batch_count"] == -(-n // 2)


def test_lay_batch_size_tu_settings(sample_text, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("EMBEDDING_BATCH_SIZE", "3")
    get_settings.cache_clear()
    assert analyze_document(sample_text, "T")["embedding_estimate"]["batch_size"] == 3


def test_ba_chunk_dau_cat_300_ky_tu():
    text = "\n".join(f"Điều {i}. Tên\n" + "x" * 400 for i in range(1, 6))
    r = analyze_document(text, "T")
    assert [s["chunk_index"] for s in r["sample_chunks"]] == [0, 1, 2]
    assert all(len(s["content"]) == 300 for s in r["sample_chunks"])
    assert r["sample_chunks"][0]["section_info"]["dieu"] == "1"


def test_nhay_coc_neu_ro_so_thieu():
    r = analyze_document(_dieu("1", "2", "5", "6", "9"), "T")
    (w,) = [w for w in r["warnings"] if "nhảy cóc" in w]
    assert "Điều 3 đến Điều 4" in w and "Điều 7 đến Điều 8" in w
    assert "giữa Điều 2 và Điều 5" in w


def test_nhay_coc_thieu_dung_mot_dieu():
    (w,) = analyze_document(_dieu("5", "7"), "T")["warnings"]
    assert "thiếu Điều 6 (giữa Điều 5 và Điều 7)" in w


def test_hau_to_chu_khong_bao_nham():
    r = analyze_document(_dieu("9", "10", "10a", "10b", "11"), "T")
    assert r["warnings"] == []
    assert r["article_count"] == 5


def test_hau_to_chu_van_bat_duoc_nhay_coc():
    (w,) = analyze_document(_dieu("10", "10a", "12"), "T")["warnings"]
    assert "thiếu Điều 11" in w


def test_dieu_lap_khong_lien_ke():
    r = analyze_document(_dieu("1", "2", "3", "2", "4"), "T")
    (w,) = [w for w in r["warnings"] if "lặp" in w]
    assert "Điều 2" in w
    assert r["article_count"] == 4


def test_dieu_trung_lien_ke_khong_bao_lap():
    r = analyze_document(_dieu("1", "2", "2", "3"), "T")
    assert not [w for w in r["warnings"] if "lặp" in w]


def test_khong_co_dieu_cat_theo_doan_van():
    r = analyze_document("Đoạn một.\n\nĐoạn hai.", "T")
    assert r["article_count"] == 0
    assert r["first_article"] is None and r["last_article"] is None
    assert r["chunks_by_level"]["doan_van"] == r["chunk_count"] >= 1
    assert any("cắt theo đoạn văn" in w for w in r["warnings"])


def test_ky_tu_thay_the_u_fffd():
    r = analyze_document(_dieu("1") + "\nNội dung l�i m� hóa.", "T")
    (w,) = r["warnings"]
    assert "U+FFFD" in w and "2 ký tự" in w


def test_dieu_dai_bi_tach_va_duoc_liet_ke():
    khoans = "\n".join(f"{i}. " + "nội dung khoản " * 40 for i in range(1, 8))
    text = "Điều 1. Ngắn\nNội dung.\nĐiều 2. Rất dài\n" + khoans
    r = analyze_document(text, "T")
    assert r["split_articles"] == [{"dieu": "2", "chunk_count": r["chunk_count"] - 1}]
    assert r["chunks_by_level"]["khoan"] == r["chunk_count"] - 1
    assert r["article_count"] == 2


def test_chunk_dai_hon_2500_bi_canh_bao(monkeypatch):
    from app.services import document_preview
    from app.services.chunking import Chunk

    big = Chunk("Điều 1. A\n" + "y" * 2600, {"chuong": None, "dieu": "1", "khoan": None, "diem": None})
    monkeypatch.setattr(document_preview, "chunk_legal_text", lambda text: [big])
    r = analyze_document("Điều 1. A", "T")
    assert any("dài hơn 2500" in w for w in r["warnings"])


@pytest.mark.parametrize("text", ["", "   \n\n  \t "])
def test_noi_dung_rong_nem_loi(text):
    with pytest.raises(EmptyContentError):
        analyze_document(text, "T")
