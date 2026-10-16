from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models import DocumentChunk, LegalDocument
from app.services.batch_ingest import ManifestError, load_manifest, run_batch
from app.services.embedding import EmbeddingError, FakeEmbedder

HEADER = "file,title,doc_type,doc_number,issuing_body,issued_date,effective_date,status,source_url\n"


@pytest.fixture
def folder(tmp_path, sample_text):
    (tmp_path / "a.txt").write_text(sample_text, encoding="utf-8")
    (tmp_path / "b.txt").write_text("Điều 1. Một\nNội dung một.\nĐiều 2. Hai\nNội dung hai.", encoding="utf-8")
    return tmp_path


def _manifest(folder: Path, body: str) -> Path:
    p = folder / "ds.csv"
    p.write_text(HEADER + body, encoding="utf-8")
    return p


ROWS = (
    "a.txt,Luật Mẫu A,luat,01/2099/QH-A,Quốc hội,2099-01-01,2099-07-01,active,\n"
    "b.txt,Nghị định Mẫu B,nghi_dinh,02/2099/ND-B,,,,,\n"
)


def test_doc_danh_sach(folder):
    rows = load_manifest(_manifest(folder, ROWS))
    assert [r.doc_number for r in rows] == ["01/2099/QH-A", "02/2099/ND-B"]
    assert rows[0].file == folder / "a.txt"
    assert str(rows[0].effective_date) == "2099-07-01"
    assert rows[1].issued_date is None and rows[1].status == "active"


def test_bo_qua_dong_bat_dau_bang_thang(folder):
    rows = load_manifest(_manifest(folder, ROWS + "#c.txt,Tắt,luat,03/2099/X,,,,,\n"))
    assert len(rows) == 2


@pytest.mark.parametrize(
    "body,msg",
    [
        ("a.txt,Luật,hien_phap,01/X,,,,,\n", "doc_type"),
        ("a.txt,Luật,luat,01/X,,2099-13-01,,,\n", "YYYY-MM-DD"),
        ("a.txt,Luật,luat,01/X,,,,,\nb.txt,Luật 2,luat,01/X,,,,,\n", "trùng"),
        ("a.txt,,luat,01/X,,,,,\n", "thiếu title"),
    ],
)
def test_danh_sach_sai_bao_loi(folder, body, msg):
    with pytest.raises(ManifestError, match=msg):
        load_manifest(_manifest(folder, body))


def test_dry_run_khong_ghi_gi(folder):
    logs = []
    rows = load_manifest(_manifest(folder, ROWS))
    result = run_batch(None, None, rows, dry_run=True, log=logs.append)
    assert result.done == [] and result.failed == []
    assert "6 đoạn, 6 Điều" in logs[0]


def test_nap_ca_danh_sach_roi_chay_lai_thi_bo_qua(db, folder):
    rows = load_manifest(_manifest(folder, ROWS))
    first = run_batch(db, FakeEmbedder(768), rows, log=lambda _: None)
    assert first.done == ["01/2099/QH-A", "02/2099/ND-B"]
    assert db.scalar(select(func.count()).select_from(LegalDocument)) == 2
    assert db.scalar(select(func.count()).select_from(DocumentChunk)) == 8

    second = run_batch(db, FakeEmbedder(768), rows, log=lambda _: None)
    assert second.done == [] and second.skipped == ["01/2099/QH-A", "02/2099/ND-B"]
    assert db.scalar(select(func.count()).select_from(DocumentChunk)) == 8


def test_thieu_file_thi_bo_qua_van_ban_do(db, folder):
    rows = load_manifest(_manifest(folder, "khong_co.txt,X,luat,09/X,,,,,\n" + ROWS))
    result = run_batch(db, FakeEmbedder(768), rows, log=lambda _: None)
    assert [d for d, _ in result.failed] == ["09/X"]
    assert result.done == ["01/2099/QH-A", "02/2099/ND-B"]


def test_het_quota_thi_dung_va_giu_van_ban_da_nap(db, folder):
    class QuotaAfterFirst(FakeEmbedder):
        calls = 0

        def embed_documents(self, texts):
            QuotaAfterFirst.calls += 1
            if QuotaAfterFirst.calls > 1:
                raise EmbeddingError("Gemini embedding lỗi 429: RESOURCE_EXHAUSTED")
            return super().embed_documents(texts)

    rows = load_manifest(_manifest(folder, ROWS))
    result = run_batch(db, QuotaAfterFirst(768), rows, log=lambda _: None)
    assert result.done == ["01/2099/QH-A"]
    assert result.stopped_for_quota
    assert db.scalar(select(func.count()).select_from(LegalDocument)) == 1

    # Lần chạy sau: văn bản đã nạp được bỏ qua, nạp tiếp phần còn thiếu
    again = run_batch(db, FakeEmbedder(768), rows, log=lambda _: None)
    assert again.skipped == ["01/2099/QH-A"] and again.done == ["02/2099/ND-B"]


def test_loi_khac_thi_chuyen_sang_van_ban_tiep(db, folder):
    class BrokenOnce(FakeEmbedder):
        calls = 0

        def embed_documents(self, texts):
            BrokenOnce.calls += 1
            if BrokenOnce.calls == 1:
                raise EmbeddingError("Gemini embedding lỗi 400: bad request")
            return super().embed_documents(texts)

    rows = load_manifest(_manifest(folder, ROWS))
    result = run_batch(db, BrokenOnce(768), rows, log=lambda _: None)
    assert [d for d, _ in result.failed] == ["01/2099/QH-A"]
    assert result.done == ["02/2099/ND-B"] and not result.stopped_for_quota


def test_file_mau_trong_repo_doc_duoc(tmp_path):
    mau = Path(__file__).resolve().parents[1] / "scripts" / "danh_sach_luat.mau.csv"
    rows = load_manifest(mau)
    assert len(rows) == 6
    assert rows[0].doc_number == "31/2024/QH15"
