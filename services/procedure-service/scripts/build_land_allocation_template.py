"""Build the editable copy of the land allocation / rental request form."""
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
DOCX_PATH = OUTPUT / "de-nghi-giao-thue-dat.docx"
JSON_PATH = OUTPUT / "de-nghi-giao-thue-dat.json"


def set_font(run, *, size=10.5, bold=False, italic=False, superscript=False):
    run.font.name = "Times New Roman"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
    run.font.size = Pt(size)
    run.bold = bold
    run.italic = italic
    run.font.superscript = superscript
    return run


def add_text(p, value, **style):
    set_font(p.add_run(value), **style)


def add_field(p, label, word_key, *, marker=None):
    add_text(p, label)
    if marker:
        add_text(p, marker, size=8, superscript=True)
    add_text(p, " {{" + word_key + "}}")


def make_field(key, label, word_key, kind="text", required=False, *, help_text="", autofill=None, options=None):
    field = {"key": key, "label": label, "word_key": word_key, "type": kind, "required": required}
    if help_text:
        field["help_text"] = help_text
    if autofill:
        field["autofill"] = autofill
    if options:
        field["options"] = options
    return field


def make_block(instance_id, title, fields):
    return {"id": instance_id, "instance_id": instance_id, "block_key": "free_field", "title": title,
            "order": 1, "fields": fields}


def make_content():
    uses = [
        "Giao đất",
        "Thuê đất",
        "Cho phép chuyển mục đích sử dụng đất",
        "Giao đất và giao rừng",
        "Thuê đất và thuê rừng",
    ]
    sections = [
        {"id": "request", "title": "Loại đề nghị và nơi tiếp nhận", "order": 1, "blocks": [
            make_block("request_info", "Thông tin đề nghị", [
                make_field("request_type", "Loại đề nghị", "request_type", "select", True, options=uses),
                make_field("recipient", "Đơn vị tiếp nhận", "recipient_unit", required=True),
            ]),
        ]},
        {"id": "applicant", "title": "Thông tin người đề nghị", "order": 2, "blocks": [
            make_block("applicant_info", "Người đề nghị", [
                make_field("name", "Họ và tên", "applicant_name", required=True,
                           help_text="Tự lấy từ hồ sơ CCCD; người làm đơn không thể sửa.", autofill="profile.full_name"),
                make_field("identity", "Số CCCD / định danh cá nhân", "applicant_identity", "cccd", True,
                           help_text="Tự lấy từ hồ sơ CCCD; người làm đơn không thể sửa.", autofill="profile.cccd_number"),
                make_field("issue_date", "Ngày cấp CCCD", "applicant_issue_date", "issue_date", True,
                           help_text="Tự lấy từ hồ sơ CCCD; người làm đơn không thể sửa.", autofill="profile.cccd_issue_date"),
                make_field("issue_place", "Cơ quan cấp CCCD", "applicant_issue_place", required=True,
                           help_text="Tự lấy từ hồ sơ CCCD; người làm đơn không thể sửa.", autofill="profile.cccd_issue_place"),
                make_field("address", "Địa chỉ / trụ sở chính", "applicant_address", "textarea", True,
                           help_text="Địa chỉ thường trú từ hồ sơ CCCD được khóa.", autofill="profile.permanent_address"),
                make_field("phone", "Điện thoại liên hệ", "applicant_phone", "phone", False,
                           help_text="Tự điền từ hồ sơ cá nhân; có thể sửa cho đơn này.", autofill="profile.phone"),
                make_field("email", "Email liên hệ", "applicant_email", "email", False,
                           help_text="Tự điền từ hồ sơ cá nhân; có thể sửa cho đơn này.", autofill="profile.email"),
            ]),
        ]},
        {"id": "land", "title": "Thông tin thửa đất và diện tích", "order": 3, "blocks": [
            make_block("land_info", "Khu đất đề nghị", [
                make_field("location", "Địa điểm thửa đất / khu đất", "land_location", "textarea", True),
                make_field("total_area", "Tổng diện tích đất (m²)", "total_area", "number", True),
                make_field("rice_area", "Diện tích đất chuyên trồng lúa (m²), nếu có", "rice_area", "number"),
                make_field("topsoil_area", "Diện tích phải bóc tách tầng đất mặt (m²), nếu có", "topsoil_area", "number"),
                make_field("forest_area", "Diện tích rừng (m²), nếu có", "forest_area", "number"),
            ]),
        ]},
        {"id": "use", "title": "Mục đích và thời hạn sử dụng", "order": 4, "blocks": [
            make_block("use_info", "Thông tin sử dụng đất", [
                make_field("purpose", "Mục đích sử dụng đất", "land_purpose", "textarea", True),
                make_field("term", "Thời hạn sử dụng đất", "land_term", required=True),
                make_field("exemption", "Nhu cầu miễn tiền sử dụng đất / tiền thuê đất (nếu có)", "exemption_case", "textarea"),
            ]),
        ]},
        {"id": "attachments", "title": "Cam kết và tài liệu kèm theo", "order": 5, "blocks": [
            make_block("closing_info", "Thông tin cuối đơn", [
                make_field("other_commitments", "Cam kết khác (nếu có)", "other_commitments", "textarea"),
                make_field("documents", "Tài liệu gửi kèm (nếu có)", "attachments", "textarea"),
                make_field("place", "Địa điểm lập đơn", "signing_place", required=True),
                make_field("day", "Ngày", "signing_day", "number", True),
                make_field("month", "Tháng", "signing_month", "number", True),
                make_field("year", "Năm", "signing_year", "number", True),
                make_field("signer", "Họ tên người ký", "signer_name", required=True,
                           help_text="Tự lấy từ hồ sơ CCCD; người làm đơn không thể sửa.", autofill="profile.full_name"),
            ]),
        ]},
    ]
    return {"schema_version": 1, "name": "Đơn đề nghị giao đất, thuê đất, chuyển mục đích sử dụng đất",
            "code": "", "procedure_code": "DD-013", "domain": "dat_dai", "procedure_id": None,
            "reference_catalog_names": [
                "Đơn đề nghị giao đất, thuê đất, cho phép chuyển mục đích sử dụng đất",
                "Đơn đề nghị giao đất, thuê đất, chuyển mục đích sử dụng đất (bản DOCX)",
                "Đơn đề nghị giao đất, thuê đất, chuyển mục đích sử dụng đất (bản có ghi chú cập nhật)",
                "Đơn xin thuê đất và phương án sản xuất kinh doanh",
            ],
            "sections": sections, "repeat_groups": []}


def paragraph(doc, text="", *, alignment=None, before=0, after=2, bold=False, italic=False, size=10.5,
              left_indent=0, first_indent=0, page_break_before=False):
    p = doc.add_paragraph()
    if alignment is not None:
        p.alignment = alignment
    p.paragraph_format.space_before = Pt(before)
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.line_spacing = 1.0
    if left_indent:
        p.paragraph_format.left_indent = Cm(left_indent)
    if first_indent:
        p.paragraph_format.first_line_indent = Cm(first_indent)
    if page_break_before:
        p.paragraph_format.page_break_before = True
    if text:
        add_text(p, text, size=size, bold=bold, italic=italic)
    return p


def create_docx():
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.top_margin, sec.bottom_margin = Cm(1.55), Cm(1.45)
    sec.left_margin = sec.right_margin = Cm(1.8)
    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(2)
    normal.paragraph_format.line_spacing = 1.0

    for title in ("CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", "Độc lập - Tự do - Hạnh phúc"):
        p = paragraph(doc, alignment=WD_ALIGN_PARAGRAPH.CENTER, after=0, bold=True, size=11)
        add_text(p, title, bold=True, size=11)
    p = paragraph(doc, alignment=WD_ALIGN_PARAGRAPH.CENTER, after=5, size=8)
    add_text(p, "____________________________", size=8)

    p = paragraph(doc, alignment=WD_ALIGN_PARAGRAPH.RIGHT, after=4, italic=True)
    add_text(p, "{{signing_place}}, ngày {{signing_day}} tháng {{signing_month}} năm {{signing_year}}", italic=True)
    p = paragraph(doc, alignment=WD_ALIGN_PARAGRAPH.CENTER, after=5, bold=True, size=12)
    add_text(p, "ĐƠN ĐỀ NGHỊ", bold=True, size=12)
    add_text(p, "1", size=8, superscript=True)
    add_text(p, " {{request_type}}", bold=True, size=12)

    p = paragraph(doc, after=2)
    add_text(p, "Kính gửi: Chủ tịch Ủy ban nhân dân")
    add_text(p, "2", size=8, superscript=True)
    add_text(p, ": {{recipient_unit}}")

    p = paragraph(doc, after=2)
    add_field(p, "1. Người đề nghị", "applicant_name", marker="3")
    add_text(p, " · CCCD/định danh: {{applicant_identity}} · Ngày cấp: {{applicant_issue_date}} · Cơ quan cấp: {{applicant_issue_place}}")
    p = paragraph(doc, after=2)
    add_field(p, "2. Địa chỉ/trụ sở chính:", "applicant_address")
    p = paragraph(doc, after=2)
    add_text(p, "3. Địa chỉ liên hệ (điện thoại, fax, email...): Điện thoại {{applicant_phone}} · Email {{applicant_email}}")
    p = paragraph(doc, after=2)
    add_field(p, "4. Địa điểm thửa đất/khu đất (tại xã..., tỉnh...):", "land_location")
    p = paragraph(doc, after=2)
    add_field(p, "5. Diện tích đất (m²):", "total_area")
    add_text(p, " trong đó:")
    p = paragraph(doc, after=2, left_indent=0.4)
    add_field(p, "a) Diện tích đất chuyên trồng lúa phải nộp tiền (m²), nếu có:", "rice_area")
    p = paragraph(doc, after=2, left_indent=0.4)
    add_field(p, "b) Diện tích đất phải bóc tách tầng đất mặt (m²), nếu có:", "topsoil_area")
    p = paragraph(doc, after=2)
    add_field(p, "6. Diện tích rừng (m²), nếu có:", "forest_area")
    p = paragraph(doc, after=2)
    add_field(p, "7. Để sử dụng vào mục đích", "land_purpose", marker="4")
    p = paragraph(doc, after=2)
    add_field(p, "8. Thời hạn sử dụng đất:", "land_term")
    p = paragraph(doc, after=2)
    add_field(p, "9. Nhu cầu miễn tiền sử dụng đất, tiền thuê đất theo quy định (nếu có):", "exemption_case")

    paragraph(doc,
        "10. Cam kết sử dụng đất, sử dụng rừng đúng mục đích, chấp hành đúng các quy định của pháp luật đất đai, pháp luật lâm nghiệp, pháp luật về đất trồng lúa; nộp tiền sử dụng đất/tiền để Nhà nước bổ sung diện tích đất bị mất hoặc tăng hiệu quả sử dụng đất trồng lúa (nếu có); bóc tách tầng và sử dụng đất mặt đầy đủ, đúng hạn.",
        after=2)
    p = paragraph(doc, after=2)
    add_field(p, "Các cam kết khác (nếu có):", "other_commitments")
    p = paragraph(doc, after=2)
    add_field(p, "11. Tài liệu gửi kèm (nếu có)", "attachments", marker="5")

    p = paragraph(doc, alignment=WD_ALIGN_PARAGRAPH.RIGHT, before=3, after=0, bold=True)
    add_text(p, "NGƯỜI LÀM ĐƠN")
    add_text(p, "6", size=8, superscript=True)
    paragraph(doc, "(Ký và ghi rõ họ tên, đóng dấu nếu có)", alignment=WD_ALIGN_PARAGRAPH.RIGHT, after=1, italic=True, size=9)
    paragraph(doc, "", after=17)
    p = paragraph(doc, alignment=WD_ALIGN_PARAGRAPH.RIGHT, after=0, bold=True)
    add_text(p, "{{signer_name}}", bold=True)

    notes = [
        "Giao đất/thuê đất/cho phép chuyển mục đích sử dụng đất/giao đất và giao rừng/thuê đất và thuê rừng.",
        "Chủ tịch UBND cấp có thẩm quyền giao đất/cho thuê đất/cho phép chuyển mục đích sử dụng đất/giao đất và giao rừng/cho thuê đất và cho thuê rừng.",
        "Đối với cá nhân, người đại diện thì ghi rõ họ tên và thông tin về số, ngày/tháng/năm, cơ quan cấp Căn cước công dân hoặc số định danh hoặc Hộ chiếu...; đối với tổ chức thì ghi rõ thông tin như trong Quyết định thành lập cơ quan, tổ chức sự nghiệp/văn bản công nhận tổ chức tôn giáo/đăng ký kinh doanh/Giấy chứng nhận đầu tư đối với doanh nghiệp/tổ chức kinh tế...",
        "Trường hợp đã được cấp giấy chứng nhận đầu tư/Quyết định, chấp thuận chủ trương đầu tư/quyết định dự án... thì ghi rõ mục đích sử dụng đất để thực hiện dự án đầu tư theo giấy tờ đã cấp. Trường hợp đề nghị giao đất xây dựng công trình ngầm thì ghi rõ diện tích đất xây dựng công trình trên mặt đất phục vụ cho việc vận hành, khai thác sử dụng công trình ngầm quy định tại Điều 216 Luật Đất đai.",
        "Thực hiện trong trường hợp hồ sơ giao đất/thuê đất/chuyển mục đích sử dụng đất/giao đất và giao rừng/thuê đất và thuê rừng do người đề nghị lập phải có theo quy định.",
        "Ghi rõ họ và tên cá nhân, cá nhân đại diện cho người sử dụng đất theo quy định tại Điều 4 Luật Đất đai.",
    ]
    for n, note in enumerate(notes, 1):
        p = paragraph(doc, before=0, after=3, size=9, left_indent=0.55, first_indent=-0.55,
                      page_break_before=(n == 1))
        add_text(p, str(n), size=8, superscript=True)
        add_text(p, " " + note, size=9)

    footer = sec.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_font(footer.add_run("Trang "), size=8)
    page = OxmlElement("w:fldSimple")
    page.set(qn("w:instr"), "PAGE")
    footer._p.append(page)
    doc.save(DOCX_PATH)


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(make_content(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    create_docx()
    print(DOCX_PATH)
    print(JSON_PATH)


if __name__ == "__main__":
    main()
