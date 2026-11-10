"""Build the shared fillable Mẫu 18 with an unbounded dynamic Bảng 18a."""
from __future__ import annotations

import json
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "app" / "builtin_forms"
SLUG = "don-dang-ky-bien-dong-mau-18"
PROFILE_HELP = "Tự lấy từ hồ sơ CCCD; người làm đơn không thể sửa thông tin gốc ở đây."


def field(key, label, word_key, kind="text", required=False, *, autofill=None, help_text=""):
    value = {"key": key, "label": label, "word_key": word_key, "type": kind, "required": required}
    if autofill:
        value["autofill"] = autofill
    if help_text:
        value["help_text"] = help_text
    return value


def content():
    people = [
        field("name", "Họ tên người cùng sử dụng / sở hữu", "person_name", required=True),
        field("address", "Địa chỉ", "person_address", "textarea", True),
        field("tax_code", "Mã số thuế (nếu có)", "person_tax"),
        field("identity", "Giấy tờ pháp nhân / số định danh", "person_identity", required=True),
        field("share", "Diện tích sử dụng / tỷ lệ sở hữu", "person_share"),
        field("note", "Ghi chú / quan hệ với người đại diện", "person_note"),
    ]
    sections = [
        {"id": "representative", "title": "I. Người sử dụng đất / người đại diện", "order": 1, "blocks": [
            {"id": "representative_info", "instance_id": "representative_info", "block_key": "free_field", "title": "Thông tin người đại diện", "order": 1, "fields": [
                field("name", "Họ và tên người đại diện", "representative_name", required=True, autofill="profile.full_name", help_text=PROFILE_HELP),
                field("birth_year", "Năm sinh", "representative_birth_year", "number", True, autofill="profile.birth_year", help_text=PROFILE_HELP),
                field("identity", "Số CCCD / định danh", "representative_id", "cccd", True, autofill="profile.cccd_number", help_text=PROFILE_HELP),
                field("address", "Địa chỉ thường trú", "representative_address", "textarea", True, autofill="profile.permanent_address", help_text=PROFILE_HELP),
                field("tax_code", "Mã số thuế (nếu có)", "representative_tax"),
                field("phone", "Điện thoại", "phone", "phone", False, autofill="profile.phone"),
                field("email", "Email", "email", "email", False, autofill="profile.email"),
            ]},
            {"id": "people", "instance_id": "people", "block_key": "repeat_group", "title": "Người cùng sử dụng / sở hữu khác (thêm từng người nếu có)", "order": 2, "fields": people},
        ]},
        {"id": "change", "title": "II–IV. Nội dung biến động và giấy tờ kèm theo", "order": 2, "blocks": [
            {"id": "change_info", "instance_id": "change_info", "block_key": "free_field", "title": "Nội dung đề nghị", "order": 1, "fields": [
                field("receiving_agency", "Văn phòng / Chi nhánh Văn phòng Đăng ký đất đai", "receiving_agency", "textarea", True),
                field("change_content", "Nội dung biến động (ví dụ: nhận chuyển nhượng, tặng cho, cấp đổi)", "change_content", "textarea", True),
                field("tax_exemption", "Đối tượng miễn / giảm nghĩa vụ tài chính (nếu có)", "tax_exemption", "textarea"),
                field("attached_documents", "Giấy tờ liên quan nộp kèm", "attached_documents", "textarea", True),
                field("place", "Địa điểm lập đơn", "place", required=True),
                field("day", "Ngày", "day", "number", True),
                field("month", "Tháng", "month", "number", True),
                field("year", "Năm", "year", "number", True),
                field("signer_name", "Người viết đơn", "signer_name", required=True, autofill="profile.full_name", help_text=PROFILE_HELP),
            ]},
        ]},
    ]
    return {
        "schema_version": 1,
        "name": "Đơn đăng ký biến động đất đai, tài sản gắn liền với đất",
        "code": "M18",
        "procedure_code": "DD-001",
        "domain": "dat_dai",
        "procedure_id": None,
        "reference_catalog_names": ["Đơn đăng ký biến động đất đai, tài sản gắn liền với đất (Mẫu số 18)", "Đơn đăng ký biến động đất đai do chuyển nhượng (Mẫu 11/ĐK - Bản Word)", "Đơn đăng ký biến động đất đai do chuyển nhượng (Mẫu 11/ĐK - Bản PDF gốc)"],
        "sections": sections,
        "repeat_groups": [{"key": "people", "title": "Người cùng sử dụng / sở hữu (Bảng 18a)", "fields": people}],
    }


def set_font(run, size=10, bold=False, italic=False):
    run.font.name = "Times New Roman"
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Times New Roman")
    run.font.size = Pt(size)
    run.bold, run.italic = bold, italic


def para(doc, text="", *, align=None, size=10, bold=False, italic=False, after=3):
    p = doc.add_paragraph()
    if align is not None:
        p.alignment = align
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.line_spacing = 1.0
    if text:
        set_font(p.add_run(text), size, bold, italic)
    return p


def create_docx():
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.top_margin, sec.bottom_margin = Cm(1.6), Cm(1.6)
    sec.left_margin = sec.right_margin = Cm(1.9)
    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Times New Roman")
    normal.font.size = Pt(10)
    normal.paragraph_format.space_after = Pt(3)
    normal.paragraph_format.line_spacing = 1.0

    para(doc, "(Ban hành kèm theo Nghị định số 151/2025/NĐ-CP)", align=WD_ALIGN_PARAGRAPH.CENTER, size=8, italic=True, after=3)
    para(doc, "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", align=WD_ALIGN_PARAGRAPH.CENTER, size=11, bold=True, after=0)
    para(doc, "Độc lập - Tự do - Hạnh phúc", align=WD_ALIGN_PARAGRAPH.CENTER, size=10, bold=True, after=4)
    para(doc, "____________________________", align=WD_ALIGN_PARAGRAPH.CENTER, size=8, after=5)
    para(doc, "ĐƠN ĐĂNG KÝ BIẾN ĐỘNG ĐẤT ĐAI, TÀI SẢN GẮN LIỀN VỚI ĐẤT", align=WD_ALIGN_PARAGRAPH.CENTER, size=12, bold=True, after=6)
    para(doc, "Kính gửi: {{receiving_agency}}", after=5)
    para(doc, "I. NGƯỜI SỬ DỤNG ĐẤT, CHỦ SỞ HỮU TÀI SẢN GẮN LIỀN VỚI ĐẤT", bold=True)
    para(doc, "Người đại diện: {{representative_name}}, sinh năm {{representative_birth_year}}", after=2)
    para(doc, "Giấy tờ nhân thân: {{representative_id}} · Địa chỉ: {{representative_address}}", after=2)
    para(doc, "Mã số thuế: {{representative_tax}} · Điện thoại: {{phone}} · Email: {{email}}", after=2)
    para(doc, "(Nếu có nhiều người cùng sử dụng / sở hữu, thêm thông tin từng người trong Bảng 18a đính kèm.)", italic=True, size=8.5, after=5)
    para(doc, "II. NỘI DUNG BIẾN ĐỘNG", bold=True)
    para(doc, "{{change_content}}", after=5)
    para(doc, "III. THÔNG TIN MIỄN, GIẢM NGHĨA VỤ TÀI CHÍNH VỀ ĐẤT ĐAI (nếu có)", bold=True)
    para(doc, "{{tax_exemption}}", after=5)
    para(doc, "IV. GIẤY TỜ LIÊN QUAN NỘP KÈM", bold=True)
    para(doc, "{{attached_documents}}", after=5)
    para(doc, "Cam đoan nội dung kê khai trên đơn là đúng sự thật và chịu trách nhiệm trước pháp luật.", after=8)
    para(doc, "{{place}}, ngày {{day}} tháng {{month}} năm {{year}}", align=WD_ALIGN_PARAGRAPH.RIGHT, italic=True, after=0)
    para(doc, "NGƯỜI VIẾT ĐƠN", align=WD_ALIGN_PARAGRAPH.RIGHT, bold=True, after=0)
    para(doc, "(Ký, ghi rõ họ tên và đóng dấu nếu có)", align=WD_ALIGN_PARAGRAPH.RIGHT, italic=True, size=8.5, after=10)
    para(doc, "{{signer_name}}", align=WD_ALIGN_PARAGRAPH.RIGHT, bold=True)

    doc.add_page_break()
    para(doc, "HƯỚNG DẪN KÊ KHAI", bold=True, after=5)
    notes = [
        "Đối với hộ gia đình, cá nhân, cộng đồng dân cư và người gốc Việt Nam định cư ở nước ngoài, ghi Văn phòng đăng ký đất đai / Chi nhánh Văn phòng đăng ký đất đai nơi có đất. Một số trường hợp ghi cơ quan cấp xã theo quy định.",
        "Cá nhân ghi họ tên bằng chữ in hoa và năm sinh. Người gốc Việt Nam định cư ở nước ngoài ghi họ tên, năm sinh, quốc tịch. Cộng đồng dân cư ghi tên cộng đồng. Tổ chức ghi theo quyết định thành lập hoặc giấy đăng ký kinh doanh. Bên nhận chuyển quyền là người kê khai thông tin.",
        "Cá nhân ghi số định danh cá nhân hoặc số, ngày cấp và nơi cấp hộ chiếu. Tổ chức ghi số, ngày ký, cơ quan ký văn bản thành lập hoặc đăng ký kinh doanh.",
        "Cá nhân ghi địa chỉ thường trú; người gốc Việt Nam định cư ở nước ngoài ghi địa chỉ thường trú tại Việt Nam nếu có; tổ chức ghi địa chỉ trụ sở chính.",
        "Ghi nội dung biến động như nhận chuyển nhượng, nhận tặng cho, cấp lại do bị mất hoặc cấp đổi. Nếu cấp lại do mất, ghi số vào sổ hoặc số seri của Giấy chứng nhận nếu biết; cơ quan giải quyết sẽ kiểm tra hồ sơ địa chính. Nếu đề nghị cấp mới thì ghi rõ nhu cầu.",
        "Người thuộc diện miễn, giảm nghĩa vụ tài chính ghi rõ trường hợp và nộp giấy tờ chứng minh theo quy định pháp luật về quản lý thuế.",
        "Ghi các loại giấy tờ nộp kèm theo đơn.",
    ]
    for i, note in enumerate(notes, 1):
        para(doc, f"({i}) {note}", size=9, after=6)
    doc.add_page_break()
    para(doc, "BẢNG 18a: DANH SÁCH NHỮNG NGƯỜI SỬ DỤNG ĐẤT, TÀI SẢN GẮN LIỀN VỚI ĐẤT", align=WD_ALIGN_PARAGRAPH.CENTER, bold=True, size=10, after=5)
    headers = ["STT", "Tên người sử dụng đất / người nhận chuyển quyền", "Địa chỉ", "Mã số thuế", "Giấy tờ nhân thân", "Diện tích / tỷ lệ sở hữu", "Ghi chú"]
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    for cell, value in zip(table.rows[0].cells, headers):
        cell.text = value
    row = table.add_row()
    row.cells[0].text = "{{#people}}{{people.index}}"
    for cell, field_key in zip(row.cells[1:], ["name", "address", "tax_code", "identity", "share", "note"]):
        cell.text = f"{{{{people.{field_key}}}}}"
    row.cells[-1].text += "{{/people}}"
    for table_row in table.rows:
        for cell in table_row.cells:
            for p in cell.paragraphs:
                for run in p.runs:
                    set_font(run, 7.5, bold=(table_row is table.rows[0]))
    para(doc, "(*) Đối với vợ chồng, ghi rõ quan hệ vợ/chồng; đối với hộ gia đình, ghi thành viên có chung quyền sử dụng đất của hộ.", italic=True, size=8.5, after=0)
    doc.save(OUTPUT / f"{SLUG}.docx")


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / f"{SLUG}.json").write_text(json.dumps(content(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    create_docx()
    print(OUTPUT / f"{SLUG}.docx")
    print(OUTPUT / f"{SLUG}.json")


if __name__ == "__main__":
    main()
