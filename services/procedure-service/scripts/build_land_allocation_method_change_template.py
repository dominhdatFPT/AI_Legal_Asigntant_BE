"""Build the fillable form for changing land allocation / lease method."""
from __future__ import annotations

import json
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "app" / "builtin_forms"
SLUG = "don-de-nghi-chuyen-hinh-thuc-giao-dat"
ALIASES = [
    "Đơn đề nghị chuyển hình thức giao đất, cho thuê đất (Mẫu số 02)",
    "Đơn xin giao đất đối với cá nhân, tổ chức (Bản Word)",
]
PROFILE_HELP = "Tự lấy từ hồ sơ CCCD; người làm đơn không thể sửa thông tin gốc ở đây."


def field(key, label, word_key, kind="text", required=False, *, autofill=None, help_text=""):
    item = {"key": key, "label": label, "word_key": word_key, "type": kind, "required": required}
    if autofill:
        item["autofill"] = autofill
    if help_text:
        item["help_text"] = help_text
    return item


def content():
    applicant = [
        field("name", "Họ tên người đề nghị / người đại diện", "applicant_name", required=True,
              autofill="profile.full_name", help_text=PROFILE_HELP),
        field("identity", "Số CCCD / định danh cá nhân", "applicant_identity", "cccd", True,
              autofill="profile.cccd_number", help_text=PROFILE_HELP),
        field("address", "Địa chỉ / trụ sở chính", "applicant_address", "textarea", True,
              autofill="profile.permanent_address", help_text=PROFILE_HELP),
        field("contact", "Điện thoại, fax, email liên hệ", "applicant_contact", "textarea"),
    ]
    parcel = [
        field("decision_details", "Thông tin quyết định giao đất / cho thuê đất / chuyển mục đích đã đăng ký", "decision_details", "textarea", True,
              help_text="Ghi số, ngày, cơ quan ban hành quyết định và thông tin thửa đất như trên quyết định."),
        field("parcel_number", "Số thửa đất (nếu có)", "parcel_number"),
        field("map_sheet_number", "Số tờ bản đồ (nếu có)", "map_sheet_number"),
        field("land_location", "Địa điểm thửa đất / khu đất", "land_location", "textarea", True),
    ]
    request = [
        field("from_method", "Hình thức sử dụng đất hiện tại", "from_method", required=True,
              help_text="Ví dụ: Nhà nước giao đất có thu tiền, giao đất không thu tiền, thuê đất trả tiền hàng năm hoặc trả tiền một lần."),
        field("to_method", "Hình thức đề nghị chuyển sang", "to_method", required=True),
        field("legal_basis", "Căn cứ / lý do đề nghị (nếu có)", "legal_basis", "textarea"),
        field("other_commitments", "Cam kết khác (nếu có)", "other_commitments", "textarea"),
        field("attachments", "Tài liệu gửi kèm", "attachments", "textarea"),
    ]
    closing = [
        field("recipient", "Chủ tịch Ủy ban nhân dân cấp có thẩm quyền", "recipient_unit", required=True),
        field("place", "Địa điểm lập đơn", "signing_place", required=True),
        field("day", "Ngày", "signing_day", "number", True),
        field("month", "Tháng", "signing_month", "number", True),
        field("year", "Năm", "signing_year", "number", True),
        field("signer", "Người ký đơn", "signer_name", required=True,
              autofill="profile.full_name", help_text=PROFILE_HELP),
    ]
    blocks = []
    for section_id, title, block_id, block_title, fields in [
        ("applicant", "Thông tin người đề nghị", "applicant_info", "Người đề nghị", applicant),
        ("parcel", "Thông tin quyết định và khu đất", "parcel_info", "Quyết định đã đăng ký và khu đất", parcel),
        ("request", "Nội dung xin chuyển hình thức", "request_info", "Hình thức hiện tại và đề nghị", request),
        ("closing", "Nơi nhận và ký đơn", "closing_info", "Nơi lập đơn", closing),
    ]:
        blocks.append({"id": section_id, "title": title, "order": len(blocks) + 1,
                       "blocks": [{"id": block_id, "instance_id": block_id, "block_key": "free_field",
                                   "title": block_title, "order": 1, "fields": fields}]})
    return {"schema_version": 1, "name": "Đơn đề nghị chuyển hình thức giao đất, cho thuê đất",
            "code": "", "procedure_code": "DD-017", "domain": "dat_dai", "procedure_id": None,
            "reference_catalog_names": ALIASES, "sections": blocks, "repeat_groups": []}


def set_font(run, *, size=10, bold=False, italic=False):
    run.font.name = "Times New Roman"
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Times New Roman")
    run.font.size = Pt(size)
    run.bold = bold
    run.italic = italic


def para(doc, text="", *, align=None, after=3, before=0, size=10, bold=False, italic=False):
    p = doc.add_paragraph()
    if align is not None:
        p.alignment = align
    p.paragraph_format.space_before = Pt(before)
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.line_spacing = 1.0
    if text:
        set_font(p.add_run(text), size=size, bold=bold, italic=italic)
    return p


def create_docx():
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.top_margin, section.bottom_margin = Cm(1.8), Cm(1.7)
    section.left_margin = section.right_margin = Cm(2.0)
    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Times New Roman")
    normal.font.size = Pt(10)
    normal.paragraph_format.space_after = Pt(3)

    para(doc, "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", align=WD_ALIGN_PARAGRAPH.CENTER, size=11, bold=True, after=0)
    para(doc, "Độc lập - Tự do - Hạnh phúc", align=WD_ALIGN_PARAGRAPH.CENTER, size=10, bold=True, after=1)
    para(doc, "________________________", align=WD_ALIGN_PARAGRAPH.CENTER, size=8, after=6)
    para(doc, "{{signing_place}}, ngày {{signing_day}} tháng {{signing_month}} năm {{signing_year}}",
         align=WD_ALIGN_PARAGRAPH.RIGHT, italic=True, after=5)
    para(doc, "ĐƠN ĐỀ NGHỊ CHUYỂN HÌNH THỨC GIAO ĐẤT / CHO THUÊ ĐẤT",
         align=WD_ALIGN_PARAGRAPH.CENTER, size=12, bold=True, after=7)
    para(doc, "Kính gửi: Chủ tịch Ủy ban nhân dân {{recipient_unit}}", after=5)
    para(doc, "1. Người đề nghị:", bold=True, after=1)
    para(doc, "{{applicant_name}} · Số CCCD / định danh: {{applicant_identity}}", after=2)
    para(doc, "2. Địa chỉ / trụ sở chính: {{applicant_address}}", after=2)
    para(doc, "3. Địa chỉ liên hệ (điện thoại, fax, email): {{applicant_contact}}", after=4)
    para(doc, "4. Thông tin trong quyết định giao đất / cho thuê đất / cho phép chuyển mục đích sử dụng đất đã đăng ký:", bold=True, after=2)
    para(doc, "{{decision_details}}", after=2)
    para(doc, "Thửa đất số: {{parcel_number}} · Tờ bản đồ số: {{map_sheet_number}}", after=2)
    para(doc, "Địa điểm thửa đất / khu đất: {{land_location}}", after=4)
    para(doc, "5. Nội dung đề nghị chuyển hình thức sử dụng đất từ {{from_method}} sang {{to_method}}.", after=3)
    para(doc, "Căn cứ / lý do đề nghị (nếu có): {{legal_basis}}", after=3)
    para(doc, "6. Tôi cam kết sử dụng đất đúng mục đích, chấp hành quy định của pháp luật đất đai và nộp tiền sử dụng đất / tiền thuê đất (nếu có) đầy đủ, đúng hạn.", after=3)
    para(doc, "Cam kết khác (nếu có): {{other_commitments}}", after=3)
    para(doc, "7. Tài liệu gửi kèm (nếu có): {{attachments}}", after=11)
    para(doc, "NGƯỜI LÀM ĐƠN", align=WD_ALIGN_PARAGRAPH.RIGHT, bold=True, after=1)
    para(doc, "(Ký, ghi rõ họ tên, đóng dấu nếu có)", align=WD_ALIGN_PARAGRAPH.RIGHT, italic=True, after=13)
    para(doc, "{{signer_name}}", align=WD_ALIGN_PARAGRAPH.RIGHT, bold=True)
    doc.save(OUTPUT / f"{SLUG}.docx")


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / f"{SLUG}.json").write_text(json.dumps(content(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    create_docx()
    print(OUTPUT / f"{SLUG}.docx")


if __name__ == "__main__":
    main()
