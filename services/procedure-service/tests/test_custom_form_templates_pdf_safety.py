"""Unit tests for PDF safety, font integrity validation, and fallback in custom_form_templates."""

import io
from pathlib import Path
import pytest
from fastapi import HTTPException, UploadFile
from reportlab.lib.pagesizes import A4
from reportlab.platypus import Paragraph, SimpleDocTemplate
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from docx import Document
from pypdf import PdfReader

from app.api.custom_form_templates import (
    check_pdf_health,
    is_defective_pdf,
    _validate_upload,
    word_to_pdf,
)

# Đăng ký font LiberationSerif để PDF test nhúng font Unicode thực sự
_FONT_DIR = Path(__file__).resolve().parents[1] / "app" / "fonts"
_TTF_PATH = _FONT_DIR / "LiberationSerif-Regular.ttf"
if _TTF_PATH.exists() and "TestUnicode" not in pdfmetrics.getRegisteredFontNames():
    pdfmetrics.registerFont(TTFont("TestUnicode", str(_TTF_PATH)))


def _create_test_pdf(text: str, use_unicode_font: bool = True) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    font_name = "TestUnicode" if (use_unicode_font and "TestUnicode" in pdfmetrics.getRegisteredFontNames()) else "Helvetica"
    style = ParagraphStyle("TestStyle", fontName=font_name, fontSize=11, leading=14)
    p = Paragraph(text, style)
    doc.build([p])
    return buffer.getvalue()


def _create_test_word(text: str) -> bytes:
    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_check_pdf_health_detects_corrupted_font_text():
    # Mô phỏng nội dung văn bản bị thay thế font thành dấu chấm giữa (\xb7)
    corrupted_text = (
        "M·u s· 01 C·NG HÒA XÃ H·I CH· NGH·A VI·T NAM ··c l·p - T· do - H·nh phúc "
        "··N ·· NGH· C·P GI·Y PHÉP XÂY D·NG M·I "
        "(S· d·ng cho công trình: Nhà · riêng l· / S·a ch·a, c·i t·o) "
        "Kính g·i: ... 1. Thông tin v· ch·· ·u t· (Ch· h·): - H· và tên ch· h·: ... "
        "- Ngày tháng n·m sinh: ... - S· CCCD/··nh danh cá nhân: ... Ngày c·p: ... "
        "- S· ·i·n tho·i liên h·: ... - Ng··i ··i di·n (n·u có): ... Ch·c v·: ... "
        "- S· CCCD ng··i ··i di·n: ... - N·i c· tr·: ... "
        "·· ngh· c· quan có th·m quy·n xem xét và c·p gi·y phép."
    )
    pdf_bytes = _create_test_pdf(corrupted_text, use_unicode_font=False)
    status, detail = check_pdf_health(pdf_bytes)
    assert status == "DEFECTIVE_FONT"
    assert detail is not None
    assert is_defective_pdf(pdf_bytes) is True


def test_check_pdf_health_accepts_clean_document():
    clean_text = (
        "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM. "
        "Độc lập - Tự do - Hạnh phúc. "
        "ĐƠN ĐỀ NGHỊ CẤP GIẤY PHÉP XÂY DỰNG MỚI. "
        "Kính gửi: Ủy ban nhân dân thành phố. "
        "Thông tin về người làm đơn và hồ sơ xin cấp phép xây dựng nhà ở riêng lẻ tại địa phương."
    )
    pdf_bytes = _create_test_pdf(clean_text, use_unicode_font=True)
    status, detail = check_pdf_health(pdf_bytes)
    assert status == "CLEAN"
    assert detail is None
    assert is_defective_pdf(pdf_bytes) is False


def test_check_pdf_health_accepts_valid_document_with_bullet_points():
    # Tài liệu tiếng Việt chuẩn có nhiều dấu gạch đầu dòng / bullet point hợp lệ
    bullet_text = (
        "BỘ NỘI VỤ · CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM\n"
        "Độc lập - Tự do - Hạnh phúc\n"
        "DANH MỤC CÁC TÀI LIỆU KÈM THEO HỒ SƠ:\n"
        "· Bản sao Giấy chứng nhận quyền sử dụng đất hợp pháp\n"
        "· Bản sao Căn cước công dân của chủ hộ và các thành viên\n"
        "· Bản vẽ sơ đồ hiện trạng vị trí thửa đất đề nghị cấp phép\n"
        "· Đơn xin xác nhận tình trạng bất động sản không có tranh chấp\n"
        "· Văn bản cam kết bảo đảm an toàn cho các công trình liền kề\n"
        "· Giấy ủy quyền thực hiện thủ tục hành chính (nếu có)\n"
        "· Hóa đơn nộp tiền lệ phí địa chính theo quy định\n"
        "· Biên bản kiểm tra thực địa của cán bộ địa chính xã/phường\n"
        "· Văn bản chấp thuận của cơ quan quản lý quy hoạch đô thị\n"
        "· Bản cam kết tự tháo dỡ công trình khi nhà nước thu hồi đất\n"
        "· Bản sao giấy phép xây dựng cũ đã được cấp trước đó\n"
    )
    pdf_bytes = _create_test_pdf(bullet_text, use_unicode_font=True)
    status, detail = check_pdf_health(pdf_bytes)
    assert status == "CLEAN"
    assert is_defective_pdf(pdf_bytes) is False


def test_check_pdf_health_accepts_scanned_or_short_pdf():
    # Tài liệu ảnh scan hoặc không có text layer
    pdf_bytes = _create_test_pdf("   ", use_unicode_font=False)
    status, _ = check_pdf_health(pdf_bytes)
    assert status == "SCANNED"
    assert is_defective_pdf(pdf_bytes) is False


def test_check_pdf_health_detects_corrupted_pdf_structure():
    # Tệp không phải PDF hoặc bị hỏng cấu trúc
    status, detail = check_pdf_health(b"")
    assert status == "CORRUPTED"
    assert "PDF" in detail

    status, detail = check_pdf_health(b"not a pdf file")
    assert status == "CORRUPTED"

    # PDF có header nhưng stream hỏng hoàn toàn
    status, detail = check_pdf_health(b"%PDF-1.4\ncorrupted trailer xref %%%")
    assert status == "CORRUPTED"
    assert detail is not None


def test_check_pdf_health_cross_validates_with_word():
    # Word gốc có tiếng Việt Unicode chuẩn phong phú
    word_text = (
        "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM. Độc lập - Tự do - Hạnh phúc. "
        "ĐƠN ĐỀ NGHỊ CẤP GIẤY PHÉP XÂY DỰNG MỚI. Sử dụng cho công trình nhà ở riêng lẻ. "
        "Kính gửi: Ủy ban nhân dân quận/huyện. Tôi tên là Nguyễn Văn A, thường trú tại Hà Nội."
    )
    word_bytes = _create_test_word(word_text)

    # PDF bị lỗi rớt ký tự thành dấu ·
    corrupted_pdf_text = (
        "C·NG HÒA XÃ H·I CH· NGH·A VI·T NAM. ··c l·p - T· do - H·nh phúc. "
        "··N ·· NGH· C·P GI·Y PHÉP XÂY D·NG M·I. S· d·ng cho công trình nhà · riêng l·. "
        "Kính g·i: ... Tôi tên là Nguy·n V·n A, th···ng trú t·i Hà N·i."
    )
    pdf_bytes = _create_test_pdf(corrupted_pdf_text, use_unicode_font=False)

    status, detail = check_pdf_health(pdf_bytes, word_bytes=word_bytes)
    assert status == "DEFECTIVE_FONT"
    assert "Đối chiếu Word" in detail


def test_validate_upload_rejects_corrupted_pdf():
    corrupted_data = b"%PDF-1.4 corrupt content that cannot be parsed"
    upload = UploadFile(filename="corrupt.pdf", file=io.BytesIO(corrupted_data))

    with pytest.raises(HTTPException) as exc_info:
        _validate_upload(upload, corrupted_data, pdf=True)

    assert exc_info.value.status_code == 422
    assert "Tệp PDF bị hỏng hoặc không thể đọc cấu trúc" in exc_info.value.detail


def test_validate_upload_rejects_defective_pdf():
    corrupted_text = (
        "M·u s· 01 C·NG HÒA XÃ H·I CH· NGH·A VI·T NAM ··c l·p - T· do - H·nh phúc "
        "··N ·· NGH· C·P GI·Y PHÉP XÂY D·NG M·I "
        "(S· d·ng cho công trình: Nhà · riêng l· / S·a ch·a, c·i t·o) "
        "Kính g·i: ... 1. Thông tin v· ch·· ·u t· (Ch· h·): - H· và tên ch· h·: ... "
        "- Ngày tháng n·m sinh: ... - S· CCCD/··nh danh cá nhân: ... Ngày c·p: ... "
        "- S· ·i·n tho·i liên h·: ... - Ng··i ··i di·n (n·u có): ... Ch·c v·: ... "
        "- S· CCCD ng··i ··i di·n: ... - N·i c· tr·: ... "
        "·· ngh· c· quan có th·m quy·n xem xét và c·p gi·y phép."
    )
    pdf_bytes = _create_test_pdf(corrupted_text, use_unicode_font=False)
    upload = UploadFile(filename="mau-tham-khao.pdf", file=io.BytesIO(pdf_bytes))

    with pytest.raises(HTTPException) as exc_info:
        _validate_upload(upload, pdf_bytes, pdf=True)

    assert exc_info.value.status_code == 422
    assert "lỗi mã hóa font chữ tiếng Việt" in exc_info.value.detail


def test_validate_upload_accepts_clean_pdf():
    clean_text = (
        "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM. "
        "Độc lập - Tự do - Hạnh phúc. "
        "ĐƠN ĐỀ NGHỊ CẤP GIẤY PHÉP XÂY DỰNG MỚI."
    )
    pdf_bytes = _create_test_pdf(clean_text, use_unicode_font=True)
    upload = UploadFile(filename="mau-tham-khao.pdf", file=io.BytesIO(pdf_bytes))

    # Không ném ngoại lệ
    _validate_upload(upload, pdf_bytes, pdf=True)


def test_validate_upload_accepts_scanned_pdf():
    scanned_bytes = _create_test_pdf("   ", use_unicode_font=False)
    upload = UploadFile(filename="scanned.pdf", file=io.BytesIO(scanned_bytes))

    # Scanned PDF hợp lệ (không ném ngoại lệ)
    _validate_upload(upload, scanned_bytes, pdf=True)


def test_word_to_pdf_preserves_multiple_table_paragraphs_and_content():
    doc = Document()
    doc.add_heading("Biểu mẫu thử nghiệm", level=1)
    table = doc.add_table(rows=1, cols=2)
    hdr_cells = table.rows[0].cells
    hdr_cells[0].paragraphs[0].text = "Cột 1 Đoạn 1"
    hdr_cells[0].add_paragraph("Cột 1 Đoạn 2 (Đoạn bổ sung tiếng Việt)")
    hdr_cells[1].paragraphs[0].text = "Cột 2 Nội dung"

    buffer = io.BytesIO()
    doc.save(buffer)
    word_bytes = buffer.getvalue()

    pdf_result = word_to_pdf(word_bytes)
    assert pdf_result.startswith(b"%PDF-")
    assert len(pdf_result) > 500

    # Trích xuất văn bản từ kết quả PDF và xác minh nội dung trong bảng
    reader = PdfReader(io.BytesIO(pdf_result))
    extracted = ""
    for page in reader.pages:
        extracted += page.extract_text() or ""

    assert "Cột 1 Đoạn 1" in extracted
    assert "Cột 1 Đoạn 2 (Đoạn bổ sung tiếng Việt)" in extracted
    assert "Cột 2 Nội dung" in extracted
