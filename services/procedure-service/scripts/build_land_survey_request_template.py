"""Build the editable copy of the land-survey request in the local catalog."""
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
DOCX_PATH = OUTPUT / "don-yeu-cau-do-dac-dat.docx"
JSON_PATH = OUTPUT / "don-yeu-cau-do-dac-dat.json"


def field(key, label, word_key, kind="text", required=False, *, autofill=None, help_text=""):
    item = {"key": key, "label": label, "word_key": word_key, "type": kind, "required": required}
    if autofill:
        item["autofill"] = autofill
    if help_text:
        item["help_text"] = help_text
    return item


def content():
    sections = [
        {"id": "applicant", "title": "Thông tin người yêu cầu", "order": 1, "blocks": [
            {"id": "applicant_info", "instance_id": "applicant_info", "block_key": "free_field",
             "title": "Người làm đơn", "order": 1, "fields": [
                field("name", "Họ và tên", "applicant_name", required=True, autofill="profile.full_name",
                      help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn."),
                field("birth_year", "Năm sinh", "birth_year", required=True, autofill="profile.birth_year",
                      help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn."),
                field("identity", "Số CCCD / định danh cá nhân", "applicant_identity", "cccd", True,
                      autofill="profile.cccd_number", help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn."),
                field("issue_date", "Ngày cấp CCCD", "applicant_issue_date", "issue_date", True,
                      autofill="profile.cccd_issue_date", help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn."),
                field("issue_place", "Nơi cấp CCCD", "applicant_issue_place", required=True,
                      autofill="profile.cccd_issue_place", help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn."),
                field("permanent_address", "Địa chỉ thường trú", "permanent_address", "textarea", True,
                      autofill="profile.permanent_address", help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn."),
                field("current_address", "Nơi ở hiện nay", "current_address", "textarea", True,
                      autofill="profile.current_address", help_text="Tự điền từ hồ sơ cá nhân; có thể sửa cho đơn này."),
                field("phone", "Số điện thoại liên hệ", "phone", "phone", False, autofill="profile.phone"),
            ]}
        ]},
        {"id": "land", "title": "Thông tin đất và quan hệ với thửa đất", "order": 2, "blocks": [
            {"id": "land_info", "instance_id": "land_info", "block_key": "free_field",
             "title": "Thửa đất cần đo đạc", "order": 1, "fields": [
                field("parcel_number", "Số thửa đất cần đo đạc", "parcel_number", required=True),
                field("certificate_number", "Số Giấy chứng nhận (nếu có)", "certificate_number"),
                field("land_relation", "Quan hệ của người yêu cầu với thửa đất", "land_relation", "textarea", True,
                      help_text="Ví dụ: là người sử dụng đất theo Giấy chứng nhận số… do… cấp ngày…"),
                field("request_content", "Nội dung sự việc cần trình bày", "request_content", "textarea", True),
                field("purpose", "Mục đích cần đo đạc / bảo vệ quyền lợi", "request_purpose", "textarea", True),
                field("related_people", "Người có quyền lợi liên quan (nếu có)", "related_people", "textarea"),
            ]}
        ]},
        {"id": "closing", "title": "Nơi nhận, tài liệu và người ký", "order": 3, "blocks": [
            {"id": "closing_info", "instance_id": "closing_info", "block_key": "free_field",
             "title": "Thông tin lập đơn", "order": 1, "fields": [
                field("recipient", "UBND và địa chính xã / phường tiếp nhận", "recipient_units", "textarea", True),
                field("attachments", "Tài liệu gửi kèm", "attachments", "textarea"),
                field("place", "Địa điểm lập đơn", "signing_place", required=True),
                field("day", "Ngày", "signing_day", "number", True),
                field("month", "Tháng", "signing_month", "number", True),
                field("year", "Năm", "signing_year", "number", True),
                field("signer", "Họ tên người làm đơn", "signer_name", required=True,
                      autofill="profile.full_name", help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn."),
            ]}
        ]},
    ]
    return {"schema_version": 1, "name": "Đơn yêu cầu đo đạc đất", "code": "", "procedure_code": "DD-002",
            "domain": "dat_dai", "procedure_id": None,
            "reference_catalog_names": ["Mẫu đơn xin đo đạc lại đất phục vụ cấp đổi Giấy chứng nhận"],
            "sections": sections, "repeat_groups": []}


def set_font(run, *, size=10, bold=False, italic=False):
    run.font.name = "Times New Roman"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
    run.font.size = Pt(size)
    run.bold = bold
    run.italic = italic


def para(doc, text="", *, align=None, after=2, before=0, size=10, bold=False, italic=False):
    p = doc.add_paragraph()
    if align is not None:
        p.alignment = align
    p.paragraph_format.space_before = Pt(before)
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
    para(doc, "ĐƠN YÊU CẦU ĐO ĐẠC ĐẤT", align=WD_ALIGN_PARAGRAPH.CENTER, after=2, size=12, bold=True)
    para(doc, "(Về việc: Đo đạc thửa đất số {{parcel_number}})",
         align=WD_ALIGN_PARAGRAPH.CENTER, after=5, size=10, italic=True)
    para(doc, "Kính gửi: {{recipient_units}}", after=4)
    para(doc, "Căn cứ Luật Đất đai năm 2024 và nhu cầu thực tế của công dân.", after=4)
    para(doc, "THÔNG TIN NGƯỜI YÊU CẦU", after=2, bold=True)
    para(doc, "Tôi tên là: {{applicant_name}} · Sinh năm: {{birth_year}}", after=2)
    para(doc, "CCCD/định danh số: {{applicant_identity}} · Ngày cấp: {{applicant_issue_date}} · Nơi cấp: {{applicant_issue_place}}", after=2)
    para(doc, "Địa chỉ thường trú: {{permanent_address}}", after=2)
    para(doc, "Hiện đang cư trú tại: {{current_address}}", after=2)
    para(doc, "Số điện thoại liên hệ: {{phone}}", after=2)
    para(doc, "Là: {{land_relation}}", after=4)
    para(doc, "NỘI DUNG YÊU CẦU", after=2, bold=True)
    para(doc, "Tôi xin trình bày sự việc như sau: {{request_content}}", after=3)
    para(doc, "Để nhằm {{request_purpose}}, cũng như bảo đảm quyền và lợi ích của bản thân tôi cùng {{related_people}}, nay tôi làm đơn này đề nghị Ủy ban nhân dân xem xét và đo đạc lại thửa đất đã nêu. Những điều kê khai trong đơn đều đúng sự thật và tôi xin chịu trách nhiệm về nội dung đã trình bày.", after=4)
    para(doc, "Tôi xin trân trọng cảm ơn!", after=2)
    para(doc, "Tài liệu gửi kèm: {{attachments}}", after=6)
    para(doc, "NGƯỜI LÀM ĐƠN", align=WD_ALIGN_PARAGRAPH.RIGHT, after=0, bold=True)
    para(doc, "(Ký và ghi rõ họ tên)", align=WD_ALIGN_PARAGRAPH.RIGHT, after=12, italic=True)
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
