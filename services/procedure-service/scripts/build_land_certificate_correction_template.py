"""Build an editable, fillable copy of the land-certificate correction request."""
from __future__ import annotations

import json
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "app" / "builtin_forms"
DOCX_PATH = OUTPUT / "don-de-nghi-dinh-chinh-giay-chung-nhan.docx"
JSON_PATH = OUTPUT / "don-de-nghi-dinh-chinh-giay-chung-nhan.json"


def field(key, label, word_key, kind="text", required=False, *, autofill=None, help_text=""):
    item = {"key": key, "label": label, "word_key": word_key, "type": kind, "required": required}
    if autofill:
        item["autofill"] = autofill
    if help_text:
        item["help_text"] = help_text
    return item


def block(instance, title, fields):
    return {"id": instance, "instance_id": instance, "block_key": "free_field", "title": title,
            "order": 1, "fields": fields}


def content():
    sections = [
        {"id": "applicant", "title": "Thông tin người sử dụng đất", "order": 1, "blocks": [
            block("applicant_info", "Người đề nghị đính chính", [
                field("name", "Họ và tên người sử dụng đất", "applicant_name", required=True,
                      autofill="profile.full_name", help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn."),
                field("identity", "Số CCCD / định danh cá nhân", "applicant_identity", "cccd", True,
                      autofill="profile.cccd_number", help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn."),
                field("issue_date", "Ngày cấp CCCD", "applicant_issue_date", "issue_date", True,
                      autofill="profile.cccd_issue_date", help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn."),
                field("issue_place", "Nơi cấp CCCD", "applicant_issue_place", required=True,
                      autofill="profile.cccd_issue_place", help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn."),
                field("address", "Địa chỉ liên hệ", "applicant_address", "textarea", True,
                      autofill="profile.permanent_address", help_text="Tự lấy địa chỉ thường trú; không thể sửa trong đơn."),
            ])
        ]},
        {"id": "certificate", "title": "Thông tin Giấy chứng nhận", "order": 2, "blocks": [
            block("certificate_info", "Giấy chứng nhận đã cấp", [
                field("serial", "Số phát hành Giấy chứng nhận", "certificate_serial", required=True),
                field("book_number", "Số vào sổ cấp Giấy chứng nhận", "certificate_book_number", required=True),
                field("issuing_authority", "Cơ quan cấp", "issuing_authority", required=True),
                field("issue_date", "Ngày cấp Giấy chứng nhận", "certificate_issue_date", "date", True),
            ])
        ]},
        {"id": "correction", "title": "Nội dung đề nghị đính chính", "order": 3, "blocks": [
            block("correction_info", "Thông tin sai và thông tin đúng", [
                field("incorrect_information", "Thông tin đang ghi sai trên Giấy chứng nhận", "incorrect_information", "textarea", True,
                      help_text="Ghi nguyên văn thông tin sai trên Giấy chứng nhận."),
                field("correct_information", "Thông tin đúng đề nghị đính chính", "correct_information", "textarea", True,
                      help_text="Ghi thông tin đúng theo giấy tờ chứng minh."),
                field("evidence_documents", "Giấy tờ chứng minh nội dung sai sót", "evidence_documents", "textarea", True),
            ])
        ]},
        {"id": "closing", "title": "Nơi lập, ngày lập và người ký", "order": 4, "blocks": [
            block("closing_info", "Người làm đơn", [
                field("place", "Địa điểm lập đơn", "signing_place", required=True),
                field("day", "Ngày", "signing_day", "number", True),
                field("month", "Tháng", "signing_month", "number", True),
                field("year", "Năm", "signing_year", "number", True),
                field("signer", "Họ tên người ký đơn", "signer_name", required=True,
                      autofill="profile.full_name", help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn."),
            ])
        ]},
    ]
    return {
        "schema_version": 1,
        "name": "Đơn đề nghị đính chính thông tin Giấy chứng nhận đã cấp",
        "code": "",
        "procedure_code": "DD-007",
        "domain": "dat_dai",
        "procedure_id": None,
        "reference_catalog_names": ["Đơn đề nghị đính chính thông tin Giấy chứng nhận đã cấp"],
        "sections": sections,
        "repeat_groups": [],
    }


def set_font(run, *, size=10, bold=False, italic=False):
    run.font.name = "Times New Roman"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
    run.font.size = Pt(size)
    run.bold = bold
    run.italic = italic


def add(p, text, *, size=10, bold=False, italic=False):
    run = p.add_run(text)
    set_font(run, size=size, bold=bold, italic=italic)


def para(doc, text="", *, align=None, after=2, before=0, size=10, bold=False, italic=False):
    p = doc.add_paragraph()
    if align is not None:
        p.alignment = align
    p.paragraph_format.space_before = Pt(before)
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.line_spacing = 1.0
    if text:
        add(p, text, size=size, bold=bold, italic=italic)
    return p


def create_docx():
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.top_margin, sec.bottom_margin = Cm(1.6), Cm(1.6)
    sec.left_margin = sec.right_margin = Cm(1.8)
    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
    normal.font.size = Pt(10)
    normal.paragraph_format.space_after = Pt(2)
    normal.paragraph_format.line_spacing = 1.0

    para(doc, "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", align=WD_ALIGN_PARAGRAPH.CENTER, after=0, size=11, bold=True)
    para(doc, "Độc lập - Tự do - Hạnh phúc", align=WD_ALIGN_PARAGRAPH.CENTER, after=0, size=11, bold=True)
    para(doc, "____________________________", align=WD_ALIGN_PARAGRAPH.CENTER, after=4, size=8)
    p = para(doc, "{{signing_place}}, ngày {{signing_day}} tháng {{signing_month}} năm {{signing_year}}",
             align=WD_ALIGN_PARAGRAPH.RIGHT, after=4, italic=True)
    para(doc, "ĐƠN ĐỀ NGHỊ ĐÍNH CHÍNH GIẤY CHỨNG NHẬN ĐÃ CẤP",
         align=WD_ALIGN_PARAGRAPH.CENTER, after=2, size=12, bold=True)
    para(doc, "(Áp dụng khi phát hiện có sai sót thông tin trên Giấy chứng nhận)",
         align=WD_ALIGN_PARAGRAPH.CENTER, after=5, size=9, italic=True)
    para(doc, "Kính gửi: Văn phòng Đăng ký đất đai / Chi nhánh Văn phòng Đăng ký đất đai", after=3)

    para(doc, "1. Người sử dụng đất: {{applicant_name}}", after=2)
    para(doc, "Số CCCD/Định danh: {{applicant_identity}} · Ngày cấp: {{applicant_issue_date}} · Nơi cấp: {{applicant_issue_place}}", after=2)
    para(doc, "Địa chỉ liên hệ: {{applicant_address}}", after=4)

    para(doc, "2. Giấy chứng nhận đã cấp:", after=2, bold=True)
    para(doc, "- Số phát hành: {{certificate_serial}} · Số vào sổ cấp GCN: {{certificate_book_number}}", after=2)
    para(doc, "- Do cơ quan cấp: {{issuing_authority}} · Ngày cấp: {{certificate_issue_date}}", after=4)

    para(doc, "3. Nội dung phát hiện có sai sót cần đính chính:", after=2, bold=True)
    para(doc, "- Thông tin ghi trên Giấy chứng nhận: {{incorrect_information}}", after=2)
    para(doc, "- Thông tin đúng thực tế: {{correct_information}}", after=4)
    para(doc, "4. Giấy tờ chứng minh nội dung sai sót kèm theo: {{evidence_documents}}", after=7)
    para(doc, "Tôi xin cam đoan nội dung kê khai trên là đúng sự thật và chịu trách nhiệm trước pháp luật.", after=6)
    para(doc, "NGƯỜI LÀM ĐƠN", align=WD_ALIGN_PARAGRAPH.RIGHT, after=0, bold=True)
    para(doc, "(Ký, ghi rõ họ tên)", align=WD_ALIGN_PARAGRAPH.RIGHT, after=13, italic=True)
    para(doc, "{{signer_name}}", align=WD_ALIGN_PARAGRAPH.RIGHT, bold=True)

    footer = sec.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    add(footer, "Trang ", size=8)
    page = OxmlElement("w:fldSimple")
    page.set(qn("w:instr"), "PAGE")
    footer._p.append(page)
    doc.save(DOCX_PATH)


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(content(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    create_docx()
    print(DOCX_PATH)
    print(JSON_PATH)


if __name__ == "__main__":
    main()
