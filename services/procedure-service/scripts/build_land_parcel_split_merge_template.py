"""Build a fillable land parcel split/merge form with variable parcel rows."""
from __future__ import annotations

import json
from pathlib import Path

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "app" / "builtin_forms"
DOCX_PATH = OUTPUT / "don-de-nghi-tach-thua-hop-thua-dat.docx"
JSON_PATH = OUTPUT / "don-de-nghi-tach-thua-hop-thua-dat.json"


PARCEL_FIELDS = [
    {"key": "role", "label": "Vai trò thửa đất", "word_key": "parcel_role", "type": "select", "required": True,
     "options": ["Thửa đất gốc", "Thửa đất mới sau tách", "Thửa đất mới sau hợp"]},
    {"key": "parcel_number", "label": "Số thửa", "word_key": "parcel_number", "type": "text", "required": True},
    {"key": "map_sheet_number", "label": "Số tờ bản đồ", "word_key": "map_sheet_number", "type": "text", "required": True},
    {"key": "area", "label": "Diện tích (m²)", "word_key": "parcel_area", "type": "number", "required": True},
    {"key": "land_type", "label": "Loại đất", "word_key": "land_type", "type": "text", "required": True},
    {"key": "address", "label": "Địa chỉ thửa đất", "word_key": "parcel_address", "type": "textarea", "required": True},
    {"key": "certificate_number", "label": "Số vào sổ cấp GCN", "word_key": "certificate_number", "type": "text", "required": False},
    {"key": "certificate_issue_date", "label": "Ngày cấp GCN", "word_key": "certificate_issue_date", "type": "date", "required": False},
]
CO_USER_FIELDS = [
    {"key": "name", "label": "Họ tên người cùng sử dụng", "word_key": "co_user_name", "type": "text", "required": False},
    {"key": "identity", "label": "Số CCCD / định danh", "word_key": "co_user_identity", "type": "text", "required": False},
    {"key": "address", "label": "Địa chỉ", "word_key": "co_user_address", "type": "textarea", "required": False},
]


def field(key, label, word_key, kind="text", required=False, *, autofill=None, help_text="", condition=None, options=None):
    item = {"key": key, "label": label, "word_key": word_key, "type": kind, "required": required}
    if autofill:
        item["autofill"] = autofill
    if help_text:
        item["help_text"] = help_text
    if condition:
        item["condition"] = condition
    if options:
        item["options"] = options
    return item


def block(instance, title, fields, block_key="free_field", **extra):
    return {"id": instance, "instance_id": instance, "block_key": block_key,
            "title": title, "order": 1, "fields": fields, **extra}


def content():
    split_block = block("parcels", "Thửa đất gốc và thửa đất dự kiến sau tách / hợp", PARCEL_FIELDS,
                        block_key="repeat_group",
                        help_text="Thêm một dòng cho mỗi thửa đất gốc và mỗi thửa đất mới; chọn vai trò tương ứng.")
    co_users_block = block("co_users", "Người cùng sử dụng đất (nếu có)", CO_USER_FIELDS,
                           block_key="repeat_group",
                           help_text="Thêm một dòng cho mỗi người cùng sử dụng đất cần ký đơn.")
    sections = [
        {"id": "applicant", "title": "I. Kê khai của người sử dụng đất", "order": 1, "blocks": [
            block("applicant_info", "Người sử dụng đất", [
                field("name", "Họ và tên", "applicant_name", required=True, autofill="profile.full_name",
                      help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn."),
                field("identity", "Số CCCD / định danh cá nhân", "applicant_identity", "cccd", True,
                      autofill="profile.cccd_number", help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn."),
                field("address", "Địa chỉ", "applicant_address", "textarea", True,
                      autofill="profile.permanent_address", help_text="Tự lấy địa chỉ thường trú; không thể sửa trong đơn."),
                field("phone", "Điện thoại liên hệ", "phone", "phone", False, autofill="profile.phone"),
                field("email", "Hộp thư điện tử", "email", "email", False, autofill="profile.email"),
            ]),
            co_users_block,
        ]},
        {"id": "operation", "title": "2. Đề nghị tách thửa, hợp thửa đất", "order": 2, "blocks": [
            block("operation_info", "Loại đề nghị và thông tin các thửa đất", [
                field("operation_type", "Nội dung đề nghị", "operation_type", "select", True,
                      options=["Tách thửa đất", "Hợp thửa đất", "Tách đồng thời với hợp thửa đất"]),
                field("combined_details", "Mô tả chi tiết việc tách đồng thời với hợp thửa", "combined_details", "textarea", True,
                      condition={"field": "operation_info.operation_type", "equals": "Tách đồng thời với hợp thửa đất"}),
                field("reason", "Lý do tách / hợp thửa đất", "operation_reason", "textarea", True),
                field("attachments", "Giấy tờ nộp kèm theo đơn", "attachments", "textarea", True,
                      help_text="Theo mẫu nguồn, gồm Giấy chứng nhận và bản vẽ tách/hợp thửa."),
                field("new_certificate_request", "Đề nghị cấp Giấy chứng nhận (ghi có/không thay đổi người sử dụng đất)",
                      "new_certificate_request", "select", True, options=["Có thay đổi người sử dụng đất", "Không thay đổi người sử dụng đất"]),
            ]),
            split_block,
        ]},
        {"id": "closing", "title": "Ngày lập đơn và người viết đơn", "order": 3, "blocks": [
            block("closing_info", "Thông tin lập đơn", [
                field("recipient", "Văn phòng / Chi nhánh Văn phòng Đăng ký đất đai", "recipient_unit", required=True),
                field("place", "Địa điểm lập đơn", "signing_place", required=True),
                field("day", "Ngày", "signing_day", "number", True),
                field("month", "Tháng", "signing_month", "number", True),
                field("year", "Năm", "signing_year", "number", True),
                field("signer", "Họ tên người viết đơn", "signer_name", required=True,
                      autofill="profile.full_name", help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn."),
            ])
        ]},
    ]
    return {
        "schema_version": 1,
        "name": "Đơn đề nghị tách thửa đất, hợp thửa đất",
        "code": "",
        "procedure_code": "DD-003",
        "domain": "dat_dai",
        "procedure_id": None,
        "reference_catalog_names": [
            "Đơn đề nghị tách thửa hoặc hợp thửa đất (Mẫu 11/ĐK - Bản Word)",
            "Đơn đề nghị tách thửa hoặc hợp thửa đất (Mẫu 11/ĐK - Bản PDF gốc)",
        ],
        "sections": sections,
        "repeat_groups": [
            {"key": "parcels", "title": "Thửa đất", "fields": PARCEL_FIELDS},
            {"key": "co_users", "title": "Người cùng sử dụng đất", "fields": CO_USER_FIELDS},
        ],
    }


def set_font(run, *, size=9.5, bold=False, italic=False, superscript=False):
    run.font.name = "Times New Roman"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
    run.font.size = Pt(size)
    run.bold = bold
    run.italic = italic
    run.font.superscript = superscript


def add(p, text, *, size=9.5, bold=False, italic=False, superscript=False):
    run = p.add_run(text)
    set_font(run, size=size, bold=bold, italic=italic, superscript=superscript)


def para(doc, text="", *, align=None, before=0, after=2, size=9.5, bold=False, italic=False, page_break=False):
    p = doc.add_paragraph()
    if align is not None:
        p.alignment = align
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.space_before = Pt(before)
    p.paragraph_format.line_spacing = 1.0
    if page_break:
        p.paragraph_format.page_break_before = True
    if text:
        add(p, text, size=size, bold=bold, italic=italic)
    return p


def shade(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def create_repeat_table(doc, title, group_key, fields, *, co_users=False):
    para(doc, title, before=3, after=2, bold=True)
    if co_users:
        headers = ["Họ tên", "CCCD / định danh", "Địa chỉ", "Ký tên"]
        row_values = ["{{#co_users}}{{co_users.name}}", "{{co_users.identity}}", "{{co_users.address}}", "{{/co_users}}"]
        widths = [Cm(4.2), Cm(3.4), Cm(6.7), Cm(2.0)]
    else:
        headers = ["Vai trò", "Thửa / tờ bản đồ", "Diện tích / loại đất", "Địa chỉ thửa đất", "Số vào sổ / ngày cấp"]
        row_values = ["{{#parcels}}{{parcels.role}}", "Thửa {{parcels.parcel_number}} / Tờ {{parcels.map_sheet_number}}",
                      "{{parcels.area}} m² / {{parcels.land_type}}", "{{parcels.address}}",
                      "{{parcels.certificate_number}} / {{parcels.certificate_issue_date}}{{/parcels}}"]
        widths = [Cm(3.0), Cm(3.0), Cm(3.4), Cm(4.1), Cm(2.8)]
    table = doc.add_table(rows=2, cols=len(headers))
    table.style = "Table Grid"
    for index, label in enumerate(headers):
        cell = table.rows[0].cells[index]
        cell.width = widths[index]
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        shade(cell, "F0F0F0")
        p = cell.paragraphs[0]
        p.paragraph_format.space_after = Pt(0)
        add(p, label, size=7.7, bold=True)
    for index, value in enumerate(row_values):
        cell = table.rows[1].cells[index]
        cell.width = widths[index]
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
        p = cell.paragraphs[0]
        p.paragraph_format.space_after = Pt(0)
        add(p, value, size=7.5)
    return table


def create_docx():
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.top_margin, sec.bottom_margin = Cm(1.3), Cm(1.3)
    sec.left_margin = sec.right_margin = Cm(1.5)
    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
    normal.font.size = Pt(9.5)
    normal.paragraph_format.space_after = Pt(2)
    normal.paragraph_format.line_spacing = 1.0

    para(doc, "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", align=WD_ALIGN_PARAGRAPH.CENTER, after=0, size=11, bold=True)
    para(doc, "Độc lập - Tự do - Hạnh phúc", align=WD_ALIGN_PARAGRAPH.CENTER, after=0, size=11, bold=True)
    para(doc, "____________________________", align=WD_ALIGN_PARAGRAPH.CENTER, after=3, size=8)
    para(doc, "{{signing_place}}, ngày {{signing_day}} tháng {{signing_month}} năm {{signing_year}}",
         align=WD_ALIGN_PARAGRAPH.RIGHT, after=3, italic=True)
    para(doc, "ĐƠN ĐỀ NGHỊ TÁCH THỬA ĐẤT, HỢP THỬA ĐẤT", align=WD_ALIGN_PARAGRAPH.CENTER, after=4, size=11.5, bold=True)
    para(doc, "Kính gửi: Văn phòng Đăng ký đất đai / Chi nhánh Văn phòng Đăng ký đất đai {{recipient_unit}}", after=2)
    para(doc, "I. KÊ KHAI CỦA NGƯỜI SỬ DỤNG ĐẤT", after=1, bold=True)
    para(doc, "1. Người sử dụng đất: {{applicant_name}} · CCCD/định danh: {{applicant_identity}}", after=1)
    para(doc, "Địa chỉ: {{applicant_address}} · Điện thoại: {{phone}} · Email: {{email}}", after=1)
    create_repeat_table(doc, "Người cùng sử dụng đất (nếu có):", "co_users", CO_USER_FIELDS, co_users=True)
    para(doc, "2. Đề nghị: {{operation_type}}", before=3, after=2, bold=True)
    para(doc, "Các thửa đất gốc và thửa đất dự kiến sau tách / hợp (thêm một dòng cho mỗi thửa):", after=2)
    create_repeat_table(doc, "", "parcels", PARCEL_FIELDS)
    para(doc, "Mô tả chi tiết việc tách đồng thời với hợp thửa (nếu áp dụng): {{combined_details}}", before=3, after=2)
    para(doc, "3. Lý do tách / hợp thửa đất: {{operation_reason}}", after=2)
    para(doc, "4. Giấy tờ nộp kèm theo đơn: {{attachments}}", after=2)
    para(doc, "5. Đề nghị cấp Giấy chứng nhận: {{new_certificate_request}}", after=4)
    para(doc, "NGƯỜI VIẾT ĐƠN", align=WD_ALIGN_PARAGRAPH.RIGHT, after=0, bold=True)
    para(doc, "(Ký và ghi rõ họ tên, đóng dấu nếu có)", align=WD_ALIGN_PARAGRAPH.RIGHT, after=9, italic=True)
    para(doc, "{{signer_name}}", align=WD_ALIGN_PARAGRAPH.RIGHT, after=4, bold=True)
    para(doc, "II. Ý KIẾN CỦA VĂN PHÒNG ĐĂNG KÝ ĐẤT ĐAI / CHI NHÁNH", before=2, after=2, bold=True)
    para(doc, "Đủ điều kiện tách thửa / hợp thửa theo bản vẽ gửi kèm (ghi ý kiến):", after=2)
    for _ in range(2):
        para(doc, "................................................................................................................................................", after=3)
    para(doc, "Ngày ...... tháng ...... năm ........                         Ngày ...... tháng ...... năm ........", after=2)
    para(doc, "Người kiểm tra                                                    Văn phòng Đăng ký đất đai / Chi nhánh", after=0)
    para(doc, "(Ký, ghi rõ họ tên, chức vụ)                                    (Ký, ghi rõ họ tên, chức vụ, đóng dấu)", after=0, size=8.5, italic=True)

    para(doc, "HƯỚNG DẪN VIẾT ĐƠN", page_break=True, size=11, bold=True, after=5)
    notes = [
        "(1) Ghi tên người sử dụng đất theo Giấy chứng nhận. Trường hợp các thửa đất gốc thuộc nhiều người sử dụng đất khác nhau thì ghi đầy đủ người sử dụng đất của các thửa đất gốc đó.",
        "(2) Ghi số định danh cá nhân hoặc số, ngày cấp và nơi cấp hộ chiếu. Đối với tổ chức thì ghi số, ngày ký, cơ quan ký văn bản theo quyết định thành lập hoặc giấy đăng ký kinh doanh hoặc giấy phép đầu tư.",
        "(3) Ghi thông tin thửa đất theo Giấy chứng nhận. Trong bảng, thêm một dòng cho từng thửa gốc và từng thửa dự kiến mới; chọn đúng vai trò của mỗi thửa.",
        "(4) Người sử dụng đất của các thửa đất gốc cùng ký vào đơn. Trường hợp ủy quyền viết đơn thì người được ủy quyền ký, ghi rõ họ tên và ghi “được ủy quyền”; đối với tổ chức phải ghi họ tên, chức vụ và đóng dấu.",
        "(5) Văn phòng đăng ký đất đai / Chi nhánh ghi rõ ý kiến đủ điều kiện theo bản vẽ gửi kèm và số thứ tự thửa đất, tờ bản đồ dự kiến sau khi tách / hợp (nếu có thay đổi).",
    ]
    for note in notes:
        para(doc, note, after=6, size=9.5)
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
