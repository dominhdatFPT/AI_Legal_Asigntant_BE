"""Build the fillable land-use extension request and its filing notes."""
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
DOCX_PATH = OUTPUT / "don-xin-gia-han-su-dung-dat.docx"
JSON_PATH = OUTPUT / "don-xin-gia-han-su-dung-dat.json"


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
        {"id": "applicant", "title": "Người xin gia hạn", "order": 1, "blocks": [
            block("applicant_info", "Thông tin người xin gia hạn", [
                field("name", "Họ tên người xin gia hạn / người đại diện", "applicant_name", required=True,
                      autofill="profile.full_name", help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn."),
                field("identity", "Số CCCD / định danh cá nhân", "applicant_identity", "cccd", True,
                      autofill="profile.cccd_number", help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn."),
                field("identity_issue_date", "Ngày cấp CCCD", "identity_issue_date", "issue_date", True,
                      autofill="profile.cccd_issue_date", help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn."),
                field("identity_issue_place", "Cơ quan cấp CCCD", "identity_issue_place", required=True,
                      autofill="profile.cccd_issue_place", help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn."),
                field("address", "Địa chỉ / trụ sở chính", "applicant_address", "textarea", True,
                      autofill="profile.permanent_address", help_text="Tự lấy địa chỉ thường trú; không thể sửa trong đơn."),
                field("contact", "Điện thoại, fax, email liên hệ", "applicant_contact", "textarea"),
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
        {"id": "extension", "title": "Nội dung xin gia hạn", "order": 3, "blocks": [
            block("extension_info", "Thời gian và lý do gia hạn", [
                field("current_expired_date", "Thời hạn sử dụng đất hiện tại hết ngày", "current_expired_date", "date", True),
                field("extension_duration_years", "Thời gian đề nghị gia hạn (số năm)", "extension_duration_years", "number", True),
                field("reason", "Lý do xin gia hạn", "extension_reason", "textarea", True),
                field("attachments", "Giấy tờ nộp kèm theo đơn", "attachments", "textarea", True),
                field("other_commitments", "Cam kết khác (nếu có)", "other_commitments", "textarea"),
            ])
        ]},
        {"id": "closing", "title": "Cơ quan nhận và người ký", "order": 4, "blocks": [
            block("closing_info", "Nơi lập, ngày lập đơn", [
                field("recipient", "Ủy ban nhân dân cấp có thẩm quyền", "recipient_unit", required=True),
                field("place", "Địa điểm lập đơn", "signing_place", required=True),
                field("day", "Ngày", "signing_day", "number", True),
                field("month", "Tháng", "signing_month", "number", True),
                field("year", "Năm", "signing_year", "number", True),
                field("signer", "Họ tên người ký đơn", "signer_name", required=True,
                      autofill="profile.full_name", help_text="Tự lấy từ hồ sơ CCCD; không thể sửa trong đơn."),
            ])
        ]},
    ]
    return {"schema_version": 1, "name": "Đơn đề nghị gia hạn thời hạn sử dụng đất", "code": "",
            "procedure_code": "DD-009", "domain": "dat_dai", "procedure_id": None,
            "reference_catalog_names": ["Đơn đề nghị gia hạn thời hạn sử dụng đất"],
            "sections": sections, "repeat_groups": []}


def set_font(run, *, size=10, bold=False, italic=False):
    run.font.name = "Times New Roman"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
    run.font.size = Pt(size)
    run.bold = bold
    run.italic = italic


def para(doc, text="", *, align=None, after=2, before=0, size=10, bold=False, italic=False, page_break=False):
    p = doc.add_paragraph()
    if align is not None:
        p.alignment = align
    p.paragraph_format.space_before = Pt(before)
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.line_spacing = 1.0
    if page_break:
        p.paragraph_format.page_break_before = True
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
    normal.font.size = Pt(9.7)
    normal.paragraph_format.space_after = Pt(2)
    normal.paragraph_format.line_spacing = 1.0

    para(doc, "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", align=WD_ALIGN_PARAGRAPH.CENTER, after=0, size=11, bold=True)
    para(doc, "Độc lập - Tự do - Hạnh phúc", align=WD_ALIGN_PARAGRAPH.CENTER, after=0, size=11, bold=True)
    para(doc, "____________________________", align=WD_ALIGN_PARAGRAPH.CENTER, after=4, size=8)
    para(doc, "{{signing_place}}, ngày {{signing_day}} tháng {{signing_month}} năm {{signing_year}}",
         align=WD_ALIGN_PARAGRAPH.RIGHT, after=4, italic=True)
    para(doc, "ĐƠN XIN GIA HẠN SỬ DỤNG ĐẤT", align=WD_ALIGN_PARAGRAPH.CENTER, after=5, size=12, bold=True)
    para(doc, "Kính gửi: Ủy ban nhân dân {{recipient_unit}}", after=3)
    para(doc, "1. Người xin gia hạn sử dụng đất: {{applicant_name}}", after=2)
    para(doc, "Số CCCD/định danh: {{applicant_identity}}", after=2)
    para(doc, "Ngày cấp: {{identity_issue_date}} · Cơ quan cấp: {{identity_issue_place}}", after=2)
    para(doc, "2. Địa chỉ / trụ sở chính: {{applicant_address}}", after=2)
    para(doc, "3. Địa chỉ liên hệ (điện thoại, fax, email): {{applicant_contact}}", after=3)
    para(doc, "4. Thông tin về thửa đất / khu đất đang sử dụng:", after=2, bold=True)
    para(doc, "4.1. Thửa đất số: {{parcel_number}}", after=2)
    para(doc, "4.2. Tờ bản đồ số: {{map_sheet_number}}", after=2)
    para(doc, "4.3. Diện tích đất (m²): {{land_area}}", after=2)
    para(doc, "4.4. Mục đích sử dụng đất: {{land_purpose}}", after=2)
    para(doc, "4.5. Thời hạn sử dụng đất: {{current_term}}", after=2)
    para(doc, "4.6. Tài sản gắn liền với đất hiện có: {{attached_property}}", after=2)
    para(doc, "4.7. Địa điểm thửa đất / khu đất: {{land_location}}", after=2)
    para(doc, "4.8. Giấy chứng nhận đã cấp:", after=2)
    para(doc, "Số phát hành: {{certificate_serial}}; Số vào sổ cấp Giấy chứng nhận: {{certificate_book_number}}", after=2)
    para(doc, "Ngày cấp: {{certificate_issue_date}}", after=3)
    para(doc, "5. Nội dung đề nghị gia hạn:", after=2, bold=True)
    para(doc, "5.1. Thời hạn sử dụng đất hiện tại hết ngày: {{current_expired_date}}", after=2)
    para(doc, "5.2. Thời gian đề nghị gia hạn: {{extension_duration_years}} năm", after=2)
    para(doc, "5.3. Lý do xin gia hạn: {{extension_reason}}", after=3)
    para(doc, "6. Giấy tờ nộp kèm theo đơn này gồm có: {{attachments}}", after=3)
    para(doc, "7. Tôi cam kết sử dụng đất đúng mục đích, chấp hành đúng quy định của pháp luật đất đai, nộp tiền sử dụng đất (nếu có) đầy đủ, đúng hạn.", after=2)
    para(doc, "Cam kết khác (nếu có): {{other_commitments}}", after=6)
    para(doc, "NGƯỜI LÀM ĐƠN", align=WD_ALIGN_PARAGRAPH.RIGHT, after=0, bold=True)
    para(doc, "(Ký, ghi rõ họ tên, đóng dấu nếu có)", align=WD_ALIGN_PARAGRAPH.RIGHT, after=1, italic=True)
    for _ in range(4):
        para(doc, "", align=WD_ALIGN_PARAGRAPH.RIGHT, after=0)
    para(doc, "{{signer_name}}", align=WD_ALIGN_PARAGRAPH.RIGHT, bold=True)

    para(doc, "HƯỚNG DẪN KÊ KHAI", page_break=True, size=11, bold=True, after=5)
    notes = [
        "(1) Ghi rõ tên Ủy ban nhân dân cấp có thẩm quyền giao đất, cho thuê đất, cho phép chuyển mục đích sử dụng đất.",
        "(2) Đối với cá nhân, người đại diện: ghi họ tên và thông tin số, ngày cấp, cơ quan cấp Căn cước công dân, số định danh hoặc Hộ chiếu. Đối với tổ chức: ghi thông tin theo quyết định thành lập, văn bản công nhận, đăng ký kinh doanh hoặc giấy chứng nhận đầu tư.",
        "(3) Trường hợp đã được cấp giấy chứng nhận đầu tư, quyết định/chấp thuận chủ trương đầu tư hoặc quyết định dự án thì ghi mục đích sử dụng đất theo giấy tờ đã cấp.",
        "(4) Giấy tờ quy định tại khoản 1 Điều 66 Nghị định này.",
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
