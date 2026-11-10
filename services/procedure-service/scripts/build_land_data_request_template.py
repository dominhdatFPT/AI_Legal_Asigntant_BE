"""Build a fillable request form for land-data access and extracts."""
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
DOCX_PATH = OUTPUT / "phieu-yeu-cau-cung-cap-thong-tin-dat-dai.docx"
JSON_PATH = OUTPUT / "phieu-yeu-cau-cung-cap-thong-tin-dat-dai.json"


def field(key, label, word_key, kind="text", required=False, *, autofill=None, help_text="", condition=None):
    item = {"key": key, "label": label, "word_key": word_key, "type": kind, "required": required}
    if autofill:
        item["autofill"] = autofill
    if help_text:
        item["help_text"] = help_text
    if condition:
        item["condition"] = condition
    return item


def block(instance, title, fields):
    return {"id": instance, "instance_id": instance, "block_key": "free_field", "title": title,
            "order": 1, "fields": fields}


def content():
    sections = [
        {"id": "requester", "title": "Thông tin tổ chức, cá nhân yêu cầu", "order": 1, "blocks": [
            block("requester_info", "Người yêu cầu", [
                field("requester_type", "Loại người yêu cầu", "requester_type", "select", True,
                      help_text="Chọn cá nhân hoặc tổ chức để hiện đúng nhóm thông tin.",
                      condition=None),
                field("person_name", "Họ và tên cá nhân", "person_name", required=True,
                      autofill="profile.full_name", help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn.",
                      condition={"field": "requester_info.requester_type", "equals": "Cá nhân"}),
                field("person_identity", "Số định danh cá nhân", "person_identity", "cccd", True,
                      autofill="profile.cccd_number", help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn.",
                      condition={"field": "requester_info.requester_type", "equals": "Cá nhân"}),
                field("person_address", "Địa chỉ cá nhân", "person_address", "textarea", True,
                      autofill="profile.permanent_address", help_text="Tự lấy địa chỉ thường trú; không thể sửa trong đơn.",
                      condition={"field": "requester_info.requester_type", "equals": "Cá nhân"}),
                field("organization_name", "Tên tổ chức", "organization_name", required=True,
                      condition={"field": "requester_info.requester_type", "equals": "Tổ chức"}),
                field("organization_tax_code", "Mã số thuế tổ chức", "organization_tax_code", required=True,
                      condition={"field": "requester_info.requester_type", "equals": "Tổ chức"}),
                field("organization_address", "Địa chỉ trụ sở tổ chức", "organization_address", "textarea", True,
                      condition={"field": "requester_info.requester_type", "equals": "Tổ chức"}),
                field("phone", "Số điện thoại liên hệ", "contact_phone", "phone", False,
                      autofill="profile.phone", help_text="Tự điền từ hồ sơ cá nhân; có thể sửa cho đơn này."),
            ])
        ]},
        {"id": "data", "title": "Thông tin cần cung cấp", "order": 2, "blocks": [
            block("data_info", "Danh mục dữ liệu và mục đích sử dụng", [
                field("parcel_info", "Thông tin về thửa đất (diện tích, tọa độ, ranh giới, quy hoạch)", "data_parcel_info", "checkbox"),
                field("cadastral_map", "Trích lục bản đồ địa chính thửa đất", "data_cadastral_map", "checkbox"),
                field("land_history", "Lịch sử biến động quyền sử dụng đất, tình trạng thế chấp", "data_land_history", "checkbox"),
                field("purpose", "Mục đích sử dụng dữ liệu", "data_purpose", "textarea", True),
                field("result_method", "Hình thức nhận kết quả", "result_method", "select", True,
                      help_text="Chọn một hình thức nhận kết quả."),
            ])
        ]},
        {"id": "closing", "title": "Ngày lập phiếu và người ký", "order": 3, "blocks": [
            block("closing_info", "Người làm đơn", [
                field("place", "Địa điểm lập phiếu", "signing_place", required=True),
                field("day", "Ngày", "signing_day", "number", True),
                field("month", "Tháng", "signing_month", "number", True),
                field("year", "Năm", "signing_year", "number", True),
                field("person_signer", "Họ tên người ký (cá nhân)", "person_signer", required=True,
                      autofill="profile.full_name", help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn.",
                      condition={"field": "requester_info.requester_type", "equals": "Cá nhân"}),
                field("organization_signer", "Người đại diện ký tên (tổ chức)", "organization_signer", required=True,
                      condition={"field": "requester_info.requester_type", "equals": "Tổ chức"}),
            ])
        ]},
    ]
    sections[0]["blocks"][0]["fields"][0]["options"] = ["Cá nhân", "Tổ chức"]
    sections[1]["blocks"][0]["fields"][-1]["options"] = ["Nhận trực tiếp", "Qua cổng dịch vụ công trực tuyến"]
    return {
        "schema_version": 1,
        "name": "Phiếu yêu cầu cung cấp thông tin, dữ liệu đất đai và trích lục bản đồ địa chính",
        "code": "",
        "procedure_code": "DD-012",
        "domain": "dat_dai",
        "procedure_id": None,
        "reference_catalog_names": ["Phiếu yêu cầu cung cấp thông tin, dữ liệu đất đai và trích lục bản đồ địa chính"],
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
    sec.top_margin, sec.bottom_margin = Cm(1.7), Cm(1.7)
    sec.left_margin = sec.right_margin = Cm(1.8)
    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
    normal.font.size = Pt(10)
    normal.paragraph_format.space_after = Pt(2)
    normal.paragraph_format.line_spacing = 1.0

    para(doc, "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", align=WD_ALIGN_PARAGRAPH.CENTER, after=0, size=11, bold=True)
    para(doc, "Độc lập - Tự do - Hạnh phúc", align=WD_ALIGN_PARAGRAPH.CENTER, after=0, size=11, bold=True)
    para(doc, "----------o0o----------", align=WD_ALIGN_PARAGRAPH.CENTER, after=4, size=9)
    para(doc, "{{signing_place}}, ngày {{signing_day}} tháng {{signing_month}} năm {{signing_year}}",
         align=WD_ALIGN_PARAGRAPH.RIGHT, after=4, italic=True)
    para(doc, "PHIẾU YÊU CẦU CUNG CẤP THÔNG TIN, DỮ LIỆU ĐẤT ĐAI",
         align=WD_ALIGN_PARAGRAPH.CENTER, after=5, size=12, bold=True)
    para(doc, "Kính gửi: Trung tâm Công nghệ thông tin tài nguyên và môi trường / Văn phòng Đăng ký đất đai", after=5)

    para(doc, "1. Tổ chức, cá nhân yêu cầu:", after=2, bold=True)
    para(doc, "Loại người yêu cầu: {{requester_type}}", after=2)
    para(doc, "Họ và tên cá nhân (nếu người yêu cầu là cá nhân): {{person_name}}", after=2)
    para(doc, "Số định danh cá nhân (nếu có): {{person_identity}}", after=2)
    para(doc, "Tên tổ chức (nếu người yêu cầu là tổ chức): {{organization_name}}", after=2)
    para(doc, "Mã số thuế tổ chức (nếu có): {{organization_tax_code}}", after=2)
    para(doc, "Địa chỉ cá nhân (nếu có): {{person_address}}", after=2)
    para(doc, "Địa chỉ trụ sở tổ chức (nếu có): {{organization_address}}", after=2)
    para(doc, "Số điện thoại liên hệ: {{contact_phone}}", after=4)

    para(doc, "2. Danh mục dữ liệu đất đai yêu cầu cung cấp:", after=2, bold=True)
    para(doc, "[{{data_parcel_info}}] Thông tin về thửa đất (diện tích, tọa độ, ranh giới, quy hoạch sử dụng đất)", after=2)
    para(doc, "[{{data_cadastral_map}}] Trích lục bản đồ địa chính thửa đất", after=2)
    para(doc, "[{{data_land_history}}] Lịch sử biến động quyền sử dụng đất, tình trạng thế chấp", after=4)
    para(doc, "3. Mục đích sử dụng dữ liệu: {{data_purpose}}", after=4)
    para(doc, "4. Hình thức nhận kết quả: {{result_method}}", after=7)
    para(doc, "NGƯỜI LÀM ĐƠN / NGƯỜI ĐẠI DIỆN", align=WD_ALIGN_PARAGRAPH.RIGHT, after=0, bold=True)
    para(doc, "(Ký, ghi rõ họ tên)", align=WD_ALIGN_PARAGRAPH.RIGHT, after=12, italic=True)
    para(doc, "{{person_signer}}{{organization_signer}}", align=WD_ALIGN_PARAGRAPH.RIGHT, bold=True)

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
