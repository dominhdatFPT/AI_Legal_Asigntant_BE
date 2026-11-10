"""Build the fillable copy of the lost land-certificate reissue request."""
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
DOCX_PATH = OUTPUT / "de-nghi-cap-lai-giay-chung-nhan-bi-mat.docx"
JSON_PATH = OUTPUT / "de-nghi-cap-lai-giay-chung-nhan-bi-mat.json"


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
        {"id": "applicant", "title": "Thông tin người làm đơn", "order": 1, "blocks": [
            block("applicant_info", "Người đề nghị cấp lại", [
                field("name", "Họ và tên người làm đơn", "applicant_name", required=True,
                      autofill="profile.full_name", help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn."),
                field("birth_year", "Năm sinh", "birth_year", required=False, autofill="profile.birth_year",
                      help_text="Tự lấy từ ngày sinh trong hồ sơ CCCD; không thể sửa trong đơn."),
                field("identity", "Số CCCD / định danh cá nhân", "applicant_identity", "cccd", True,
                      autofill="profile.cccd_number", help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn."),
                field("identity_issue_date", "Ngày cấp CCCD", "applicant_issue_date", "issue_date", True,
                      autofill="profile.cccd_issue_date", help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn."),
                field("identity_issue_place", "Nơi cấp CCCD", "applicant_issue_place", required=True,
                      autofill="profile.cccd_issue_place", help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn."),
                field("address", "Địa chỉ thường trú", "applicant_address", "textarea", True,
                      autofill="profile.permanent_address", help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn."),
                field("phone", "Số điện thoại liên hệ", "applicant_phone", "phone", False,
                      autofill="profile.phone", help_text="Tự điền từ hồ sơ cá nhân; có thể sửa cho đơn này."),
            ])
        ]},
        {"id": "recipient", "title": "Cơ quan tiếp nhận và ngày lập đơn", "order": 2, "blocks": [
            block("recipient_info", "Nơi nhận đơn", [
                field("recipient", "Chi nhánh Văn phòng Đăng ký đất đai tiếp nhận", "recipient_unit", required=True),
                field("place", "Địa điểm lập đơn", "signing_place", required=True),
                field("day", "Ngày", "signing_day", "number", True),
                field("month", "Tháng", "signing_month", "number", True),
                field("year", "Năm", "signing_year", "number", True),
            ])
        ]},
        {"id": "land", "title": "Thông tin thửa đất và Giấy chứng nhận", "order": 3, "blocks": [
            block("land_info", "Thửa đất đề nghị cấp lại", [
                field("parcel_number", "Số thửa đất", "parcel_number", required=True),
                field("map_sheet_number", "Số tờ bản đồ", "map_sheet_number", required=True),
                field("land_address", "Địa chỉ thửa đất", "land_address", "textarea", True),
                field("area", "Diện tích (m²)", "land_area", "number", True),
                field("land_purpose", "Mục đích sử dụng đất", "land_purpose", required=True),
                field("certificate_serial", "Số phát hành Giấy chứng nhận", "certificate_serial", required=True),
                field("certificate_book_number", "Số vào sổ cấp Giấy chứng nhận", "certificate_book_number", required=True),
            ])
        ]},
        {"id": "loss", "title": "Thông tin về việc mất Giấy chứng nhận", "order": 4, "blocks": [
            block("loss_info", "Lý do và việc niêm yết", [
                field("loss_details", "Thời gian, địa điểm, hoàn cảnh mất", "loss_details", "textarea", True,
                      help_text="Ghi rõ thời gian, địa điểm và hoàn cảnh làm mất Giấy chứng nhận."),
                field("public_notice", "Thông tin niêm yết công khai tại UBND cấp xã", "public_notice", "textarea", True,
                      help_text="Ghi thời điểm, địa điểm niêm yết hoặc tình trạng thực hiện niêm yết."),
            ])
        ]},
        {"id": "closing", "title": "Cam đoan và người ký", "order": 5, "blocks": [
            block("closing_info", "Cam đoan", [
                field("signer", "Họ tên người ký đơn", "signer_name", required=True,
                      autofill="profile.full_name", help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn."),
            ])
        ]},
    ]
    return {
        "schema_version": 1,
        "name": "Đơn đề nghị cấp lại Giấy chứng nhận quyền sử dụng đất bị mất",
        "code": "",
        "procedure_code": "DD-005",
        "domain": "dat_dai",
        "procedure_id": None,
        "reference_catalog_names": ["Đơn đề nghị cấp lại Giấy chứng nhận quyền sử dụng đất bị mất (Mẫu 10/ĐK)"],
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
    para(doc, "Theo Nghị định số 101/2024/NĐ-CP", align=WD_ALIGN_PARAGRAPH.CENTER, after=3, size=9, italic=True)
    p = para(doc, "{{signing_place}}, ngày {{signing_day}} tháng {{signing_month}} năm {{signing_year}}",
             align=WD_ALIGN_PARAGRAPH.RIGHT, after=4, italic=True)
    p = para(doc, "ĐƠN ĐỀ NGHỊ CẤP LẠI GIẤY CHỨNG NHẬN", align=WD_ALIGN_PARAGRAPH.CENTER, after=0, size=12, bold=True)
    para(doc, "QUYỀN SỬ DỤNG ĐẤT", align=WD_ALIGN_PARAGRAPH.CENTER, after=5, size=12, bold=True)
    para(doc, "Kính gửi: Chi nhánh Văn phòng Đăng ký đất đai {{recipient_unit}}", after=3)

    p = para(doc, "1. Người làm đơn: {{applicant_name}} · Sinh năm: {{birth_year}}", after=2)
    p = para(doc, "Số CCCD/Định danh: {{applicant_identity}} · Ngày cấp: {{applicant_issue_date}} · Nơi cấp: {{applicant_issue_place}}", after=2)
    para(doc, "Địa chỉ thường trú: {{applicant_address}}", after=2)
    para(doc, "Số điện thoại liên hệ: {{applicant_phone}}", after=3)

    para(doc, "2. Thông tin thửa đất đề nghị cấp lại:", after=2, bold=True)
    para(doc, "- Thửa đất số: {{parcel_number}} · Tờ bản đồ số: {{map_sheet_number}}", after=2)
    para(doc, "- Địa chỉ thửa đất: {{land_address}}", after=2)
    para(doc, "- Diện tích: {{land_area}} m² · Mục đích sử dụng: {{land_purpose}}", after=2)
    para(doc, "- Giấy chứng nhận đã cấp số phát hành: {{certificate_serial}} · Số vào sổ: {{certificate_book_number}}", after=3)

    para(doc, "3. Lý do đề nghị cấp lại: Bị mất Giấy chứng nhận quyền sử dụng đất.", after=2, bold=True)
    para(doc, "Thời gian, địa điểm, hoàn cảnh mất: {{loss_details}}", after=2)
    para(doc, "Đã thực hiện niêm yết công khai tại UBND cấp xã: {{public_notice}}", after=3)
    para(doc, "4. Cam đoan: Tôi xin cam đoan toàn bộ thông tin trên là đúng sự thật và chịu trách nhiệm trước pháp luật.", after=7)

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
