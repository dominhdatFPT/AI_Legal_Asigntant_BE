"""Tests for PDF font health checks and rejecting defective font uploads in contract-service."""
from pathlib import Path
import unittest
import pymupdf

from app.core.errors import ContractError
from app.services.reference_pdf import check_pdf_health, prepare_reference_pdf


def _make_corrupted_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    corrupted_text = (
        "M·u s· 01\n"
        "C·NG HÒA XÃ H·I CH· NGH·A VI·T NAM\n"
        "··c l·p - T· do - H·nh phúc\n"
        "··N ·· NGH· C·P GI·Y PHÉP XÂY D·NG M·I\n"
        "(S· d·ng cho công trình: Nhà · riêng l· / S·a ch·a, c·i t·o)\n"
        "Kính g·i: ...\n"
        "1. Thông tin v· ch· ··u t· (Ch· h·): ...\n"
        "- H· và tên ch· h·: ...\n"
        "- Ngày tháng n·m sinh: ...\n"
        "- S· CCCD/··nh danh cá nhân: ...\n"
    )
    page.insert_text((50, 72), corrupted_text, fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def _get_clean_pdf() -> bytes:
    workspace = Path(__file__).resolve().parents[4]
    pdf_path = (
        workspace
        / "FE/AI_Legal/public/demo_assets"
        / "37_Mau_don_Giay_phep_xay_dung_moi_thamkhao.pdf"
    )
    return pdf_path.read_bytes()


class ReferencePdfFontSafetyTests(unittest.TestCase):
    def test_check_pdf_health_clean_pdf(self):
        pdf_bytes = _get_clean_pdf()
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            text = " ".join(
                " ".join(page.get_text().split()) for page in doc
            )
            self.assertIn("CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", text)
            self.assertIn("ĐƠN ĐỀ NGHỊ CẤP GIẤY PHÉP XÂY DỰNG MỚI", text)
            status, reason = check_pdf_health(doc)
            self.assertEqual(status, "CLEAN")
            self.assertIsNone(reason)

    def test_check_pdf_health_defective_font(self):
        pdf_bytes = _make_corrupted_pdf()
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            status, reason = check_pdf_health(doc)
            self.assertEqual(status, "DEFECTIVE_FONT")
            self.assertIsNotNone(reason)

    def test_check_pdf_health_scanned_pdf(self):
        doc = pymupdf.open()
        doc.new_page()
        pdf_bytes = doc.tobytes()
        doc.close()
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as d:
            status, reason = check_pdf_health(d)
            self.assertEqual(status, "SCANNED")
            self.assertIsNone(reason)

    def test_check_pdf_health_short_defective_text(self):
        doc = pymupdf.open()
        try:
            page = doc.new_page()
            page.insert_text((50, 72), "M·u s· 01")
            page_text = page.get_text()
            self.assertLess(len(page_text.strip()), 50)

            status, reason = check_pdf_health(doc)
            self.assertEqual(status, "DEFECTIVE_FONT")
            self.assertIn("Trang 1", reason)
        finally:
            doc.close()

    def test_check_pdf_health_mixed_clean_and_defective_pages(self):
        clean_bytes = _get_clean_pdf()
        defective_bytes = _make_corrupted_pdf()
        with (
            pymupdf.open(stream=clean_bytes, filetype="pdf") as clean,
            pymupdf.open(stream=defective_bytes, filetype="pdf") as defective,
            pymupdf.open() as mixed,
        ):
            mixed.insert_pdf(clean, from_page=0, to_page=0)
            mixed.insert_pdf(defective, from_page=0, to_page=0)
            status, reason = check_pdf_health(mixed)
            self.assertEqual(status, "DEFECTIVE_FONT")
            self.assertIn("Trang 2", reason)

    def test_prepare_reference_pdf_rejects_defective_font(self):
        pdf_bytes = _make_corrupted_pdf()
        classification = {"key": "DD-018", "code": "DD-018", "name": "Thủ tục cấp Giấy phép xây dựng"}
        with self.assertRaises(ContractError) as ctx:
            prepare_reference_pdf(pdf_bytes, "mau.pdf", "Mẫu kiểm thử", "land", classification)
        self.assertEqual(ctx.exception.code, "PDF_DEFECTIVE_FONT")

    def test_prepare_reference_pdf_accepts_clean_pdf(self):
        pdf_bytes = _get_clean_pdf()
        classification = {"key": "DD-018", "code": "DD-018", "name": "Thủ tục cấp Giấy phép xây dựng"}
        item, images = prepare_reference_pdf(pdf_bytes, "mau.pdf", "Mẫu kiểm thử", "land", classification)
        self.assertGreater(item["page_count"], 0)
        self.assertEqual(len(images), item["page_count"])


if __name__ == "__main__":
    unittest.main()
