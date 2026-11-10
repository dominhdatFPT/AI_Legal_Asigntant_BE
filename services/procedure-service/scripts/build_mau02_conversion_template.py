"""Build the editable citizen-fill version of Land Form 02 from the source PDF text.

The source files remain unchanged. The output omits the "Mẫu số 02" banner and
uses the same {{word_key}} convention as the admin-authored Word templates.
"""
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
DOCX_PATH = OUTPUT / "mau02-chuyen-muc-dich-su-dung-dat.docx"
JSON_PATH = OUTPUT / "mau02-chuyen-muc-dich-su-dung-dat.json"


def set_run_font(run, *, size=10.5, bold=False, italic=False, superscript=False):
    run.font.name = "Times New Roman"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
    run.font.size = Pt(size)
    run.bold = bold
    run.italic = italic
    run.font.superscript = superscript
    return run


def add_text(paragraph, text, **style):
    return set_run_font(paragraph.add_run(text), **style)


def add_field_line(doc, label, word_key, *, marker=None, bold_label=False):
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.space_after = Pt(1.5)
    paragraph.paragraph_format.line_spacing = 1.0
    add_text(paragraph, label, bold=bold_label)
    if marker:
        add_text(paragraph, marker, size=8, superscript=True)
    add_text(paragraph, " ")
    add_text(paragraph, "{{" + word_key + "}}")
    return paragraph


def add_note(doc, number, text):
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.left_indent = Cm(0.55)
    paragraph.paragraph_format.first_line_indent = Cm(-0.55)
    paragraph.paragraph_format.space_after = Pt(3)
    paragraph.paragraph_format.line_spacing = 1.0
    add_text(paragraph, str(number), size=9, superscript=True)
    add_text(paragraph, " " + text, size=9)


def block(instance_id, title, fields):
    return {
        "id": instance_id,
        "instance_id": instance_id,
        "block_key": "free_field",
        "title": title,
        "order": 1,
        "fields": fields,
    }


def field(key, label, word_key, kind="text", required=False, help_text="", autofill=None, options=None):
    result = {
        "key": key,
        "label": label,
        "word_key": word_key,
        "type": kind,
        "required": required,
    }
    if help_text:
        result["help_text"] = help_text
    if autofill:
        result["autofill"] = autofill
    if options:
        result["options"] = options
    return result


def make_content():
    forms_of_use = [
        "Giao đất không thu tiền sử dụng đất",
        "Giao đất có thu tiền sử dụng đất",
        "Cho thuê đất thu tiền thuê đất một lần cho cả thời gian thuê",
        "Cho thuê đất thu tiền thuê đất hằng năm",
    ]
    sections = [
        {
            "id": "applicant",
            "title": "Thông tin người đề nghị",
            "order": 1,
            "blocks": [block("applicant_info", "Người đề nghị", [
                field("name", "Họ và tên người đề nghị", "applicant_name", required=True,
                      help_text="Tự lấy họ tên từ hồ sơ CCCD; không thể sửa trong đơn.", autofill="profile.full_name"),
                field("identity", "Số CCCD / định danh cá nhân", "applicant_identity", "cccd", True,
                      "Tự lấy số CCCD từ hồ sơ; không thể sửa trong đơn.", "profile.cccd_number"),
                field("identity_issue_date", "Ngày cấp CCCD", "applicant_issue_date", "issue_date", True,
                      "Tự lấy ngày cấp từ hồ sơ CCCD; không thể sửa trong đơn.", "profile.cccd_issue_date"),
                field("identity_issue_place", "Nơi cấp CCCD", "applicant_issue_place", required=True,
                      help_text="Tự lấy nơi cấp từ hồ sơ CCCD; không thể sửa trong đơn.", autofill="profile.cccd_issue_place"),
                field("address", "Địa chỉ thường trú / trụ sở chính", "applicant_address", "textarea", True,
                      "Tự lấy địa chỉ thường trú từ hồ sơ; không thể sửa trong đơn.", "profile.permanent_address"),
                field("phone", "Điện thoại liên hệ", "applicant_phone", "phone", False,
                      "Tự điền từ hồ sơ cá nhân; có thể sửa cho đơn này.", "profile.phone"),
                field("email", "Email liên hệ", "applicant_email", "email", False,
                      "Tự điền từ hồ sơ cá nhân; có thể sửa cho đơn này.", "profile.email"),
            ])],
        },
        {
            "id": "recipient-date",
            "title": "Nơi nhận và ngày lập đơn",
            "order": 2,
            "blocks": [block("recipient_info", "Nơi nhận đơn", [
                field("recipient", "Đơn vị / xã, phường nơi có đất", "recipient_unit", required=True,
                      help_text="Ghi địa danh theo hướng dẫn của thủ tục.")
            ]), block("signing_date", "Ngày lập đơn", [
                field("place", "Địa điểm lập đơn", "signing_place", required=True),
                field("day", "Ngày", "signing_day", "number", True),
                field("month", "Tháng", "signing_month", "number", True),
                field("year", "Năm", "signing_year", "number", True),
            ])],
        },
        {
            "id": "land-info",
            "title": "Thông tin thửa đất",
            "order": 3,
            "blocks": [block("land_info", "Thửa đất / khu đất", [
                field("location", "Địa điểm thửa đất / khu đất", "land_location", "textarea", True,
                      "Ghi xã/phường và tỉnh/thành phố nơi có đất."),
                field("current_area", "Diện tích hiện tại (m²)", "current_area", "number", True),
                field("current_purpose", "Mục đích sử dụng hiện tại", "current_purpose", required=True,
                      help_text="Ghi theo Giấy chứng nhận hoặc giấy tờ liên quan."),
                field("current_term", "Thời hạn sử dụng đất hiện tại", "current_term", required=True),
                field("current_use_form", "Hình thức sử dụng đất hiện tại", "current_use_form", "select", True,
                      options=forms_of_use),
                field("authority_document", "Số, ngày và cơ quan ban hành văn bản có thẩm quyền (nếu có)",
                      "authority_document", "textarea", False),
            ])],
        },
        {
            "id": "conversion",
            "title": "Nội dung đề nghị chuyển mục đích",
            "order": 4,
            "blocks": [block("conversion_info", "Nội dung chuyển mục đích", [
                field("requested_area_purpose", "Diện tích và mục đích sử dụng đất đề nghị chuyển",
                      "requested_area_purpose", "textarea", True),
                field("rice_area", "Diện tích đất chuyên trồng lúa (m²), nếu có", "rice_area", "number"),
                field("requested_term", "Thời hạn sử dụng đất đề nghị", "requested_term", required=True),
                field("requested_use_form", "Hình thức sử dụng đất đề nghị", "requested_use_form", "select", True,
                      options=forms_of_use),
                field("exemption_case", "Trường hợp được miễn tiền sử dụng đất / tiền thuê đất (nếu có)",
                      "exemption_case", "textarea"),
            ])],
        },
        {
            "id": "commitments",
            "title": "Cam kết và tài liệu kèm theo",
            "order": 5,
            "blocks": [block("commitment_info", "Cam kết, tài liệu", [
                field("other_commitments", "Cam kết khác (nếu có)", "other_commitments", "textarea"),
                field("attachments", "Tài liệu gửi kèm", "attachments", "textarea"),
                field("signer", "Họ tên người ký", "signer_name", required=True,
                      help_text="Tự lấy từ hồ sơ CCCD; người làm đơn không thể sửa.", autofill="profile.full_name"),
            ])],
        },
    ]
    return {
        "schema_version": 1,
        "name": "Đơn đề nghị chuyển mục đích sử dụng đất",
        "code": "",
        "procedure_code": "DD-008",
        "domain": "dat_dai",
        "procedure_id": None,
        "sections": sections,
        "repeat_groups": [],
    }


def create_docx():
    doc = Document()
    section = doc.sections[0]
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(1.45)
    section.bottom_margin = Cm(1.35)
    section.left_margin = Cm(1.65)
    section.right_margin = Cm(1.65)

    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(1.5)
    normal.paragraph_format.line_spacing = 1.0

    for text in ("CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", "Độc lập - Tự do - Hạnh phúc"):
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(0)
        add_text(p, text, bold=True, size=11)
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(6)
    add_text(p, "____________________________", size=8)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p.paragraph_format.space_after = Pt(2)
    add_text(p, "{{signing_place}}, ngày {{signing_day}} tháng {{signing_month}} năm {{signing_year}}", italic=True)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(1)
    p.paragraph_format.space_after = Pt(5)
    add_text(p, "ĐƠN ĐỀ NGHỊ CHUYỂN MỤC ĐÍCH SỬ DỤNG ĐẤT", bold=True, size=12)

    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(3)
    add_text(p, "Kính gửi: Chủ tịch Ủy ban nhân dân", bold=True)
    add_text(p, "1", size=8, superscript=True)
    add_text(p, ": {{recipient_unit}}")

    add_field_line(doc, "1. Người đề nghị chuyển mục đích sử dụng đất", "applicant_name", marker="2")
    p = doc.paragraphs[-1]
    add_text(p, " · Số CCCD/định danh: {{applicant_identity}} · Ngày cấp: {{applicant_issue_date}} · Nơi cấp: {{applicant_issue_place}}")
    add_field_line(doc, "2. Địa chỉ / trụ sở chính:", "applicant_address")
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(1.5)
    add_text(p, "3. Thông tin liên hệ (điện thoại, email): ")
    add_text(p, "{{applicant_phone}} · {{applicant_email}}")

    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(1)
    add_text(p, "4. Thông tin thửa đất/khu đất:", bold=True)
    add_field_line(doc, "- Địa điểm thửa đất/khu đất:", "land_location")
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(1.5)
    add_text(p, "- Diện tích và mục đích sử dụng hiện tại", bold=False)
    add_text(p, "3", size=8, superscript=True)
    add_text(p, ": {{current_area}} m² · {{current_purpose}}")
    add_field_line(doc, "- Thời hạn sử dụng đất:", "current_term")
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(1.5)
    add_text(p, "- Hình thức sử dụng đất")
    add_text(p, "4", size=8, superscript=True)
    add_text(p, ": {{current_use_form}}")
    add_field_line(doc, "- Số, ngày và cơ quan ban hành văn bản có thẩm quyền:", "authority_document")

    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(1)
    add_text(p, "5. Nội dung đề nghị chuyển mục đích sử dụng đất:", bold=True)
    add_field_line(doc, "- Diện tích và mục đích sử dụng đất đề nghị chuyển:", "requested_area_purpose")
    add_field_line(doc, "- Diện tích đất chuyên trồng lúa phải nộp tiền (m²), nếu có:", "rice_area")
    add_field_line(doc, "- Thời hạn sử dụng đất:", "requested_term")
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(1.5)
    add_text(p, "- Hình thức sử dụng đất")
    add_text(p, "5", size=8, superscript=True)
    add_text(p, ": {{requested_use_form}}")

    add_field_line(doc, "6. Trường hợp được miễn tiền sử dụng đất/tiền thuê đất (nếu có)", "exemption_case", marker="6")

    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(1.5)
    add_text(p, "7. Cam kết: sử dụng đất đúng mục đích; chấp hành pháp luật về đất đai và đất trồng lúa; nộp đầy đủ, đúng hạn các khoản tiền sử dụng đất, tiền thuê đất, khoản phải nộp để Nhà nước bổ sung diện tích đất chuyên trồng lúa bị mất hoặc tăng hiệu quả sử dụng đất trồng lúa (nếu có), cùng các nghĩa vụ tài chính khác theo quy định.")
    add_field_line(doc, "Các cam kết khác (nếu có):", "other_commitments")
    add_field_line(doc, "8. Tài liệu gửi kèm (nếu có)", "attachments", marker="7")

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(0)
    add_text(p, "NGƯỜI LÀM ĐƠN", bold=True)
    add_text(p, "8", size=8, superscript=True)
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p.paragraph_format.space_after = Pt(0)
    add_text(p, "(Ký và ghi rõ họ tên, đóng dấu nếu có)", italic=True, size=9)
    for _ in range(2):
        doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    add_text(p, "{{signer_name}}", bold=True)

    p = doc.add_paragraph()
    p.paragraph_format.page_break_before = True
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(8)
    add_text(p, "HƯỚNG DẪN KÊ KHAI ĐƠN", bold=True, size=11)
    notes = [
        "Chủ tịch UBND cấp có thẩm quyền cho phép chuyển mục đích sử dụng đất.",
        "Đối với cá nhân, người đại diện thì ghi rõ họ tên và thông tin về số, ngày/tháng/năm, cơ quan cấp Căn cước công dân hoặc số định danh hoặc Hộ chiếu…; đối với tổ chức thì ghi rõ thông tin như trong Quyết định thành lập cơ quan, tổ chức sự nghiệp/văn bản công nhận tổ chức tôn giáo/đăng ký kinh doanh/Giấy chứng nhận đầu tư đối với doanh nghiệp/tổ chức kinh tế…",
        "Ghi theo loại đất trong giấy chứng nhận về quyền sử dụng đất đã cấp; trường hợp không có giấy chứng nhận về quyền sử dụng đất thì ghi theo giấy tờ khác có liên quan thể hiện mục đích sử dụng đất đó.",
        "Giao đất không thu tiền sử dụng đất; giao đất có thu tiền sử dụng đất; cho thuê đất thu tiền thuê đất một lần cho cả thời gian thuê; cho thuê đất thu tiền thuê đất hằng năm.",
        "Giao đất không thu tiền sử dụng đất; giao đất có thu tiền sử dụng đất; cho thuê đất thu tiền thuê đất một lần cho cả thời gian thuê; cho thuê đất thu tiền thuê đất hằng năm.",
        "Nêu rõ trường hợp được miễn tiền sử dụng đất, miễn tiền thuê đất theo quy định nào của pháp luật.",
        "Thực hiện trong trường hợp hồ sơ chuyển mục đích sử dụng đất do người đề nghị lập phải có theo quy định.",
        "Ghi rõ họ và tên cá nhân, cá nhân đại diện cho người sử dụng đất theo quy định tại Điều 4 Luật Đất đai.",
    ]
    for number, note in enumerate(notes, 1):
        add_note(doc, number, note)

    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer.add_run("Trang ")
    set_run_font(run, size=8)
    field_code = OxmlElement("w:fldSimple")
    field_code.set(qn("w:instr"), "PAGE")
    footer._p.append(field_code)
    doc.save(DOCX_PATH)


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    content = make_content()
    JSON_PATH.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    create_docx()
    print(DOCX_PATH)
    print(JSON_PATH)


if __name__ == "__main__":
    main()
