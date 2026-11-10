"""Build the editable copy of the land-dispute mediation request form."""
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
DOCX_PATH = OUTPUT / "don-de-nghi-hoa-giai-tranh-chap-dat-dai.docx"
JSON_PATH = OUTPUT / "don-de-nghi-hoa-giai-tranh-chap-dat-dai.json"


def set_font(run, *, size=10.5, bold=False, italic=False, superscript=False):
    run.font.name = "Times New Roman"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
    run.font.size = Pt(size)
    run.bold = bold
    run.italic = italic
    run.font.superscript = superscript


def add(p, text, **style):
    set_font(p.add_run(text), **style)


def field(key, label, word_key, kind="text", required=False, *, autofill=None, help_text=""):
    result = {"key": key, "label": label, "word_key": word_key, "type": kind, "required": required}
    if autofill:
        result["autofill"] = autofill
    if help_text:
        result["help_text"] = help_text
    return result


def block(instance, title, fields):
    return {"id": instance, "instance_id": instance, "block_key": "free_field", "title": title,
            "order": 1, "fields": fields}


def content():
    sections = [
        {"id": "applicant", "title": "Thông tin người đề nghị", "order": 1, "blocks": [
            block("applicant_info", "Người làm đơn", [
                field("name", "Họ và tên", "applicant_name", required=True, autofill="profile.full_name",
                      help_text="Tự lấy từ hồ sơ CCCD và khóa không cho sửa."),
                field("birth_year", "Năm sinh", "birth_year", required=True, autofill="profile.birth_year",
                      help_text="Tự lấy từ ngày sinh trong hồ sơ CCCD và khóa không cho sửa."),
                field("identity", "Số CCCD / định danh cá nhân", "applicant_identity", "cccd", True,
                      autofill="profile.cccd_number", help_text="Tự lấy từ hồ sơ CCCD và khóa không cho sửa."),
                field("issue_date", "Ngày cấp CCCD", "applicant_issue_date", "issue_date", True,
                      autofill="profile.cccd_issue_date", help_text="Tự lấy từ hồ sơ CCCD và khóa không cho sửa."),
                field("issue_place", "Nơi cấp CCCD", "applicant_issue_place", required=True,
                      autofill="profile.cccd_issue_place", help_text="Tự lấy từ hồ sơ CCCD và khóa không cho sửa."),
                field("current_address", "Nơi ở hiện nay", "current_address", "textarea", True,
                      autofill="profile.current_address", help_text="Tự điền từ hồ sơ cá nhân; có thể sửa cho đơn này."),
                field("permanent_address", "Nơi đăng ký thường trú", "permanent_address", "textarea", True,
                      autofill="profile.permanent_address", help_text="Tự lấy địa chỉ thường trú từ hồ sơ CCCD và khóa không cho sửa."),
                field("phone", "Số điện thoại liên hệ", "phone", "phone", False, autofill="profile.phone"),
                field("email", "Email liên hệ", "email", "email", False, autofill="profile.email"),
            ])
        ]},
        {"id": "recipient", "title": "Cơ quan tiếp nhận và ngày lập đơn", "order": 2, "blocks": [
            block("recipient_info", "Thông tin tiếp nhận", [
                field("recipient", "Xã / phường / thị trấn tiếp nhận", "recipient_unit", required=True),
                field("place", "Địa điểm lập đơn", "signing_place", required=True),
                field("day", "Ngày", "signing_day", "number", True),
                field("month", "Tháng", "signing_month", "number", True),
                field("year", "Năm", "signing_year", "number", True),
            ])
        ]},
        {"id": "dispute", "title": "Thông tin tranh chấp", "order": 3, "blocks": [
            block("dispute_info", "Bên tranh chấp và nội dung", [
                field("other_party", "Người / gia đình có tranh chấp", "other_party_name", required=True),
                field("other_party_address", "Nơi ở của bên có tranh chấp", "other_party_address", "textarea", True),
                field("land_location", "Vị trí thửa đất đang tranh chấp", "land_location", "textarea", True),
                field("disputed_area", "Diện tích đất đang tranh chấp (nếu biết)", "disputed_area"),
                field("statement", "Trình bày sự việc và quá trình tranh chấp", "dispute_statement", "textarea", True,
                      help_text="Ghi rõ mốc thời gian, nguồn gốc đất, diễn biến và đề nghị hòa giải."),
            ])
        ]},
        {"id": "attachments", "title": "Tài liệu kèm theo và người ký", "order": 4, "blocks": [
            block("closing_info", "Tài liệu", [
                field("attachments", "Tài liệu gửi kèm", "attachments", "textarea"),
                field("signer", "Họ tên người viết đơn", "signer_name", required=True, autofill="profile.full_name",
                      help_text="Tự lấy từ hồ sơ CCCD và khóa không cho sửa."),
            ])
        ]},
    ]
    return {"schema_version": 1, "name": "Đơn đề nghị hòa giải tranh chấp đất đai", "code": "",
            "procedure_code": "DD-014", "domain": "dat_dai", "procedure_id": None,
            "sections": sections, "repeat_groups": []}


def para(doc, text="", *, align=None, after=2, before=0, size=10.5, bold=False, italic=False, left=0, first=0):
    p = doc.add_paragraph()
    if align is not None:
        p.alignment = align
    p.paragraph_format.space_before = Pt(before)
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.line_spacing = 1.0
    if left:
        p.paragraph_format.left_indent = Cm(left)
    if first:
        p.paragraph_format.first_line_indent = Cm(first)
    if text:
        add(p, text, size=size, bold=bold, italic=italic)
    return p


def add_field(p, label, key):
    add(p, label)
    add(p, "{{" + key + "}}")


def create_docx():
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.top_margin, sec.bottom_margin = Cm(1.5), Cm(1.5)
    sec.left_margin = sec.right_margin = Cm(1.7)
    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(2)
    normal.paragraph_format.line_spacing = 1.0

    for text in ("CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", "Độc lập - Tự do - Hạnh phúc"):
        p = para(doc, align=WD_ALIGN_PARAGRAPH.CENTER, after=0, size=11, bold=True)
        add(p, text, size=11, bold=True)
    p = para(doc, align=WD_ALIGN_PARAGRAPH.CENTER, after=5, size=8)
    add(p, "____________________________", size=8)
    p = para(doc, align=WD_ALIGN_PARAGRAPH.RIGHT, after=4, italic=True)
    add(p, "{{signing_place}}, ngày {{signing_day}} tháng {{signing_month}} năm {{signing_year}}", italic=True)
    p = para(doc, align=WD_ALIGN_PARAGRAPH.CENTER, after=6, size=12, bold=True)
    add(p, "ĐƠN ĐỀ NGHỊ HÒA GIẢI TRANH CHẤP ĐẤT ĐAI", size=12, bold=True)

    p = para(doc, after=3)
    add(p, "Kính gửi: Ủy ban nhân dân xã (phường, thị trấn) ")
    add(p, "{{recipient_unit}}")
    p = para(doc, after=2)
    add_field(p, "Họ và tên: ", "applicant_name")
    add(p, " · Sinh năm: {{birth_year}}")
    p = para(doc, after=2)
    add_field(p, "CCCD/định danh số: ", "applicant_identity")
    add(p, " · Ngày cấp: {{applicant_issue_date}} · Nơi cấp: {{applicant_issue_place}}")
    p = para(doc, after=2)
    add_field(p, "Nơi ở hiện nay: ", "current_address")
    p = para(doc, after=2)
    add_field(p, "Nơi đăng ký thường trú: ", "permanent_address")
    p = para(doc, after=2)
    add(p, "Điện thoại: {{phone}} · Email: {{email}}")

    p = para(doc, before=3, after=2)
    add(p, "Tôi viết đơn này đề nghị UBND xã (phường, thị trấn) giải quyết tranh chấp đất đai giữa gia đình tôi với gia đình ông (bà): ")
    add(p, "{{other_party_name}}", bold=True)
    p = para(doc, after=2)
    add_field(p, "Trú tại: ", "other_party_address")
    p = para(doc, after=2)
    add_field(p, "Diện tích / vị trí đất đang xảy ra tranh chấp: ", "disputed_area")
    add(p, " · ")
    add(p, "{{land_location}}")
    p = para(doc, before=2, after=2, bold=True)
    add(p, "Tôi trình bày sự việc như sau:", bold=True)
    p = para(doc, after=4)
    add(p, "{{dispute_statement}}")
    paragraph = para(doc, after=3)
    add(paragraph, "Đến nay, mặc dù hai bên đã thương lượng và tự hòa giải nhưng không thể giải quyết tranh chấp trên. Do vậy, tôi làm đơn này đề nghị UBND xã (phường, thị trấn) tổ chức hòa giải tranh chấp đất giữa gia đình tôi với gia đình ông: ")
    add(paragraph, "{{other_party_name}}", bold=True)
    add(paragraph, ", trú tại ")
    add(paragraph, "{{other_party_address}}")
    add(paragraph, " nhằm xác định người có quyền sử dụng đối với diện tích đang xảy ra tranh chấp theo đúng quy định.")
    for text in (
        "Tôi cam đoan với UBND những thông tin trên hoàn toàn đúng sự thật.",
        "Kính mong UBND xem xét đơn đề nghị và sớm tổ chức hòa giải tranh chấp đất đai theo quy định.",
        "Tôi chân thành cảm ơn!",
    ):
        para(doc, text, after=2)
    p = para(doc, before=2, after=1)
    add_field(p, "Tài liệu có gửi kèm theo: ", "attachments")
    p = para(doc, align=WD_ALIGN_PARAGRAPH.RIGHT, before=2, after=0, bold=True)
    add(p, "NGƯỜI VIẾT ĐƠN", bold=True)
    para(doc, "(Ký, ghi rõ họ tên)", align=WD_ALIGN_PARAGRAPH.RIGHT, after=1, italic=True, size=9)
    para(doc, "", after=12)
    p = para(doc, align=WD_ALIGN_PARAGRAPH.RIGHT, bold=True)
    add(p, "{{signer_name}}", bold=True)

    footer = sec.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_font(footer.add_run("Trang "), size=8)
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
