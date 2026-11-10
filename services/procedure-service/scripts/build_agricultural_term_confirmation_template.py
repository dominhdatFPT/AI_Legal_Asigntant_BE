"""Build the land-use term confirmation form for agricultural parcels."""
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
DOCX_PATH = OUTPUT / "don-xac-nhan-thoi-han-dat-nong-nghiep.docx"
JSON_PATH = OUTPUT / "don-xac-nhan-thoi-han-dat-nong-nghiep.json"


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
            block("applicant_info", "Người sử dụng đất", [
                field("name", "Họ tên người sử dụng đất", "applicant_name", required=True,
                      autofill="profile.full_name", help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn."),
                field("identity", "Số CCCD / định danh cá nhân", "applicant_identity", "cccd", True,
                      autofill="profile.cccd_number", help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn."),
                field("address", "Địa chỉ thường trú / trụ sở", "applicant_address", "textarea", True,
                      autofill="profile.permanent_address", help_text="Tự lấy địa chỉ thường trú; không thể sửa trong đơn."),
                field("phone", "Số điện thoại liên hệ", "phone", "phone", False, autofill="profile.phone"),
                field("email", "Email liên hệ", "email", "email", False, autofill="profile.email"),
            ])
        ]},
        {"id": "land", "title": "Thông tin thửa đất", "order": 2, "blocks": [
            block("land_info", "Thửa đất / khu đất đang sử dụng", [
                field("parcel_number", "Số thửa đất", "parcel_number", required=True),
                field("map_sheet_number", "Số tờ bản đồ", "map_sheet_number", required=True),
                field("area", "Diện tích đất (m²)", "land_area", "number", True),
                field("purpose", "Mục đích sử dụng đất", "land_purpose", required=True),
                field("current_term", "Thời hạn sử dụng đất hiện tại", "current_term", required=True),
                field("attached_property", "Tài sản gắn liền với đất hiện có", "attached_property", "textarea"),
                field("location", "Địa điểm thửa đất / khu đất", "land_location", "textarea", True),
                field("certificate_serial", "Số phát hành Giấy chứng nhận", "certificate_serial", required=True),
                field("certificate_book_number", "Số vào sổ cấp Giấy chứng nhận", "certificate_book_number", required=True),
                field("certificate_issue_date", "Ngày cấp Giấy chứng nhận", "certificate_issue_date", "date", True),
            ])
        ]},
        {"id": "confirmation", "title": "Nội dung xin xác nhận", "order": 3, "blocks": [
            block("confirmation_info", "Thời hạn tiếp tục sử dụng", [
                field("requested_until", "Đề nghị xác nhận thời hạn sử dụng đất đến ngày", "requested_until", "date", True),
                field("attachments", "Giấy tờ kèm theo", "attachments", "textarea", True,
                      help_text="Theo mẫu nguồn, kèm Giấy chứng nhận quyền sử dụng đất đã cấp."),
            ])
        ]},
        {"id": "closing", "title": "Ngày lập đơn và người ký", "order": 4, "blocks": [
            block("closing_info", "Thông tin lập đơn", [
                field("recipient", "Văn phòng / Chi nhánh Văn phòng Đăng ký đất đai", "recipient_unit", required=True),
                field("place", "Địa điểm lập đơn", "signing_place", required=True),
                field("day", "Ngày", "signing_day", "number", True),
                field("month", "Tháng", "signing_month", "number", True),
                field("year", "Năm", "signing_year", "number", True),
                field("signer", "Họ tên người làm đơn", "signer_name", required=True,
                      autofill="profile.full_name", help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn."),
            ])
        ]},
    ]
    return {"schema_version": 1, "name": "Đơn xin xác nhận lại thời hạn sử dụng đất nông nghiệp",
            "code": "", "procedure_code": "DD-009", "domain": "dat_dai", "procedure_id": None,
            "reference_catalog_names": ["Đơn xin xác nhận tiếp tục sử dụng đất nông nghiệp của hộ gia đình, cá nhân"],
            "sections": sections, "repeat_groups": []}


def set_font(run, *, size=10, bold=False, italic=False):
    run.font.name = "Times New Roman"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
    run.font.size = Pt(size)
    run.bold = bold
    run.italic = italic


def para(doc, text="", *, align=None, after=2, size=10, bold=False, italic=False):
    p = doc.add_paragraph()
    if align is not None:
        p.alignment = align
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.line_spacing = 1.0
    if text:
        run = p.add_run(text)
        set_font(run, size=size, bold=bold, italic=italic)
    return p


def create_docx():
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.top_margin, sec.bottom_margin = Cm(1.5), Cm(1.5)
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
    para(doc, "{{signing_place}}, ngày {{signing_day}} tháng {{signing_month}} năm {{signing_year}}",
         align=WD_ALIGN_PARAGRAPH.RIGHT, after=4, italic=True)
    para(doc, "ĐƠN XIN XÁC NHẬN LẠI THỜI HẠN SỬ DỤNG ĐẤT NÔNG NGHIỆP",
         align=WD_ALIGN_PARAGRAPH.CENTER, after=5, size=12, bold=True)
    para(doc, "Kính gửi: Văn phòng Đăng ký đất đai / Chi nhánh Văn phòng Đăng ký đất đai {{recipient_unit}}", after=4)
    para(doc, "1. Người sử dụng đất: {{applicant_name}} · CCCD/định danh: {{applicant_identity}}", after=2)
    para(doc, "2. Địa chỉ liên hệ: {{applicant_address}} · Điện thoại: {{phone}} · Email: {{email}}", after=3)
    para(doc, "3. Thông tin về thửa đất / khu đất đang sử dụng:", after=2, bold=True)
    para(doc, "3.1. Thửa đất số: {{parcel_number}} · 3.2. Tờ bản đồ số: {{map_sheet_number}}", after=2)
    para(doc, "3.3. Diện tích đất: {{land_area}} m² · 3.4. Mục đích sử dụng đất: {{land_purpose}}", after=2)
    para(doc, "3.5. Thời hạn sử dụng đất: {{current_term}} · 3.6. Tài sản gắn liền: {{attached_property}}", after=2)
    para(doc, "3.7. Địa điểm thửa đất / khu đất: {{land_location}}", after=2)
    para(doc, "3.8. Giấy chứng nhận đã cấp: Số phát hành {{certificate_serial}}; số vào sổ {{certificate_book_number}}; ngày cấp {{certificate_issue_date}}", after=4)
    para(doc, "4. Nội dung đề nghị xác nhận lại thời hạn sử dụng đất đến ngày: {{requested_until}}", after=3)
    para(doc, "5. Giấy tờ nộp kèm theo: {{attachments}}", after=3)
    para(doc, "Tôi cam đoan nội dung kê khai trên đơn là đúng sự thật; nếu sai, tôi hoàn toàn chịu trách nhiệm trước pháp luật.", after=7)
    para(doc, "NGƯỜI LÀM ĐƠN", align=WD_ALIGN_PARAGRAPH.RIGHT, after=0, bold=True)
    para(doc, "(Ký, ghi rõ họ tên, đóng dấu nếu có)", align=WD_ALIGN_PARAGRAPH.RIGHT, after=12, italic=True)
    para(doc, "{{signer_name}}", align=WD_ALIGN_PARAGRAPH.RIGHT, bold=True)

    footer = sec.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer.add_run("Trang ")
    set_font(run, size=8)
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
