"""Build an editable copy of the first land-registration form (Form 15)."""
from __future__ import annotations

import json
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "app" / "builtin_forms"
SLUG = "don-dang-ky-dat-dai-lan-dau"


def field(key, label, word_key, kind="text", required=False, *, autofill=None, help_text="", options=None):
    item = {"key": key, "label": label, "word_key": word_key, "type": kind, "required": required}
    if autofill:
        item["autofill"] = autofill
    if help_text:
        item["help_text"] = help_text
    if options:
        item["options"] = options
    return item


PROFILE_HELP = "Tự lấy từ hồ sơ CCCD; người làm đơn không thể sửa thông tin gốc ở đây."


def content():
    co_users = [
        field("name", "Họ tên người cùng sử dụng / sở hữu", "co_user_name", required=True),
        field("identity", "Số giấy tờ nhân thân", "co_user_identity", required=True),
        field("address", "Địa chỉ", "co_user_address", "textarea", True),
        field("tax_code", "Mã số thuế (nếu có)", "co_user_tax"),
        field("share", "Diện tích / tỷ lệ sử dụng, sở hữu", "co_user_share"),
        field("relation", "Quan hệ / ghi chú", "co_user_relation"),
    ]
    parcels = [
        field("parcel_number", "Số thửa", "parcel_number", required=True),
        field("map_sheet", "Tờ bản đồ", "map_sheet", required=True),
        field("address", "Địa chỉ thửa đất", "parcel_address", "textarea", True),
        field("area", "Diện tích (m²)", "parcel_area", "number", True),
        field("purpose", "Mục đích sử dụng", "parcel_purpose", required=True),
        field("origin", "Nguồn gốc sử dụng", "parcel_origin", required=True),
    ]
    buildings = [
        field("type", "Loại nhà ở / công trình", "building_type", required=True),
        field("construction_area", "Diện tích xây dựng (m²)", "building_construction_area", "number", True),
        field("floor_area", "Diện tích sàn / sử dụng (m²)", "building_floor_area", "number"),
        field("shared_area", "Diện tích sở hữu chung (m²)", "building_shared_area", "number"),
        field("private_area", "Diện tích sở hữu riêng (m²)", "building_private_area", "number"),
        field("floors", "Tổng số tầng", "building_floors", "number"),
        field("aboveground_floors", "Số tầng nổi", "building_aboveground_floors", "number"),
        field("basement_floors", "Số tầng hầm", "building_basement_floors", "number"),
        field("origin", "Nguồn gốc hình thành", "building_origin"),
        field("completion_year", "Năm hoàn thành xây dựng", "building_completion_year", "number"),
        field("ownership_term", "Thời hạn sở hữu đến", "building_ownership_term"),
    ]
    sections = [
        {"id": "owner", "title": "1. Người sử dụng đất / chủ sở hữu", "order": 1, "blocks": [
            {"id": "owner_info", "instance_id": "owner_info", "block_key": "free_field", "title": "Thông tin người đứng đơn", "order": 1,
             "fields": [
                field("name", "Họ và tên", "owner_name", required=True, autofill="profile.full_name", help_text=PROFILE_HELP),
                field("birth_year", "Năm sinh", "owner_birth_year", "number", True, autofill="profile.birth_year", help_text=PROFILE_HELP),
                field("identity", "Số định danh / giấy tờ nhân thân", "owner_identity", "cccd", True, autofill="profile.cccd_number", help_text=PROFILE_HELP),
                field("tax_code", "Mã số thuế (nếu có)", "owner_tax_code"),
                field("address", "Địa chỉ thường trú / trụ sở", "owner_address", "textarea", True, autofill="profile.permanent_address", help_text=PROFILE_HELP),
                field("phone", "Điện thoại liên hệ", "owner_phone", "phone", False, autofill="profile.phone"),
                field("email", "Hộp thư điện tử", "owner_email", "email", False, autofill="profile.email"),
             ]},
            {"id": "co_users", "instance_id": "co_users", "block_key": "repeat_group", "title": "Người cùng sử dụng / sở hữu (nếu có)", "order": 2, "fields": co_users},
        ]},
        {"id": "parcel", "title": "2. Thửa đất đăng ký", "order": 2, "blocks": [
            {"id": "parcel_info", "instance_id": "parcel_info", "block_key": "free_field", "title": "Thông tin thửa đất", "order": 1,
             "fields": [
                field("parcel_number", "Số thửa (nếu có)", "parcel_number_main"),
                field("map_sheet", "Tờ bản đồ (nếu có)", "map_sheet_main"),
                field("address", "Địa chỉ thửa đất", "land_address", "textarea", True),
                field("area", "Tổng diện tích (m²)", "land_area", "number", True),
                field("shared_area", "Diện tích sử dụng chung (m²)", "land_shared_area", "number"),
                field("private_area", "Diện tích sử dụng riêng (m²)", "land_private_area", "number"),
                field("purpose", "Mục đích sử dụng", "land_purpose", required=True),
                field("use_start_date", "Thời điểm bắt đầu sử dụng", "use_start_date", "date"),
                field("requested_term", "Thời hạn đề nghị sử dụng đất", "requested_term"),
                field("origin", "Nguồn gốc sử dụng đất", "land_origin", "textarea", True),
                field("adjacent_rights", "Quyền / hạn chế đối với thửa đất liền kề (nếu có)", "adjacent_rights", "textarea"),
             ]},
            {"id": "parcels_15b", "instance_id": "parcels_15b", "block_key": "repeat_group", "title": "Danh sách nhiều thửa đất nông nghiệp (Mẫu 15b, nếu áp dụng)", "order": 2, "fields": parcels},
        ]},
        {"id": "property", "title": "3. Nhà ở, công trình xây dựng (nếu đăng ký quyền sở hữu)", "order": 3, "blocks": [
            {"id": "building_info", "instance_id": "building_info", "block_key": "free_field", "title": "Thông tin nhà ở / công trình", "order": 1,
             "fields": [
                field("type", "Loại nhà ở / công trình", "building_type_main"),
                field("construction_area", "Diện tích xây dựng (m²)", "building_construction_area_main", "number"),
                field("floor_area", "Diện tích sàn / sử dụng (m²)", "building_floor_area_main", "number"),
                field("shared_area", "Diện tích sở hữu chung (m²)", "building_shared_area_main", "number"),
                field("private_area", "Diện tích sở hữu riêng (m²)", "building_private_area_main", "number"),
                field("floors", "Tổng số tầng", "building_floors_main", "number"),
                field("aboveground_floors", "Số tầng nổi", "building_aboveground_floors_main", "number"),
                field("basement_floors", "Số tầng hầm", "building_basement_floors_main", "number"),
                field("origin", "Nguồn gốc hình thành", "building_origin_main"),
                field("completion_year", "Năm hoàn thành xây dựng", "building_completion_year_main", "number"),
                field("ownership_term", "Thời hạn sở hữu đến", "building_ownership_term_main"),
                field("responsibility", "Cam kết chịu trách nhiệm (trường hợp thuộc diện phải cam kết)", "building_responsibility", "checkbox"),
             ]},
            {"id": "buildings_15c", "instance_id": "buildings_15c", "block_key": "repeat_group", "title": "Danh sách nhiều nhà ở / công trình (Mẫu 15c, nếu áp dụng)", "order": 2, "fields": buildings},
        ]},
        {"id": "request", "title": "4–6. Đề nghị, miễn giảm và giấy tờ kèm theo", "order": 4, "blocks": [
            {"id": "request_info", "instance_id": "request_info", "block_key": "free_field", "title": "Nội dung đề nghị", "order": 1, "fields": [
                field("register_land", "Đề nghị đăng ký đất đai, tài sản gắn liền với đất", "request_register_land", "checkbox"),
                field("issue_certificate", "Đề nghị cấp Giấy chứng nhận", "request_issue_certificate", "checkbox"),
                field("defer_fee", "Đề nghị ghi nợ tiền sử dụng đất (đối với cá nhân)", "request_defer_fee", "checkbox"),
                field("other_request", "Đề nghị khác", "request_other", "textarea"),
                field("fee_exemption", "Thông tin miễn, giảm nghĩa vụ tài chính (nếu có)", "fee_exemption", "textarea"),
                field("attachments", "Giấy tờ nộp kèm theo", "attachments", "textarea", True),
                field("recipient", "Cơ quan tiếp nhận", "recipient", "textarea", True),
                field("place", "Địa điểm lập đơn", "signing_place", required=True),
                field("day", "Ngày", "signing_day", "number", True),
                field("month", "Tháng", "signing_month", "number", True),
                field("year", "Năm", "signing_year", "number", True),
                field("signer", "Người kê khai / người ký", "signer_name", required=True, autofill="profile.full_name", help_text=PROFILE_HELP),
            ]},
        ]},
    ]
    return {
        "schema_version": 1,
        "name": "Đơn đăng ký đất đai, tài sản gắn liền với đất",
        "code": "M15",
        "procedure_code": "DD-004",
        "domain": "dat_dai",
        "procedure_id": None,
        "reference_catalog_names": ["Đơn đăng ký đất đai, cấp Giấy chứng nhận lần đầu (Mẫu 04/ĐK - Bản Word)", "Đơn đăng ký đất đai, cấp Giấy chứng nhận lần đầu (Mẫu 04/ĐK - Bản PDF gốc)"],
        "sections": sections,
        "repeat_groups": [
            {"key": "co_users", "title": "Người cùng sử dụng / sở hữu (Mẫu 15a)", "fields": co_users},
            {"key": "parcels_15b", "title": "Nhiều thửa đất nông nghiệp (Mẫu 15b)", "fields": parcels},
            {"key": "buildings_15c", "title": "Nhiều nhà / công trình (Mẫu 15c)", "fields": buildings},
        ],
    }


def set_font(run, size=9.5, bold=False, italic=False):
    run.font.name = "Times New Roman"
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Times New Roman")
    run.font.size = Pt(size)
    run.bold, run.italic = bold, italic


def para(doc, text="", *, align=None, size=9.5, bold=False, italic=False, after=2):
    p = doc.add_paragraph()
    if align is not None:
        p.alignment = align
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.line_spacing = 1.0
    if text:
        set_font(p.add_run(text), size, bold, italic)
    return p


def add_repeat_table(doc, title, headers, key, fields):
    para(doc, title, align=WD_ALIGN_PARAGRAPH.CENTER, size=10, bold=True, after=3)
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    for cell, value in zip(table.rows[0].cells, headers):
        cell.text = value
    row = table.add_row()
    row.cells[0].text = f"{{{{#{key}}}}}{{{{{key}.index}}}}"
    for cell, item in zip(row.cells[1:], fields):
        cell.text = f"{{{{{key}.{item['key']}}}}}"
    row.cells[-1].text += f"{{{{/{key}}}}}"
    for tr in table.rows:
        for cell in tr.cells:
            for p in cell.paragraphs:
                for run in p.runs:
                    set_font(run, 7.5, bold=(tr is table.rows[0]))
    doc.add_paragraph()


def create_docx():
    schema = content()
    repeat_fields = {group["key"]: group["fields"] for group in schema["repeat_groups"]}
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.top_margin, sec.bottom_margin = Cm(1.3), Cm(1.3)
    sec.left_margin = sec.right_margin = Cm(1.65)
    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Times New Roman")
    normal.font.size = Pt(9.5)
    normal.paragraph_format.space_after = Pt(2)
    normal.paragraph_format.line_spacing = 1.0

    para(doc, "(Ban hành kèm theo Nghị định số 151/2025/NĐ-CP)", align=WD_ALIGN_PARAGRAPH.CENTER, size=8, italic=True, after=4)
    para(doc, "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", align=WD_ALIGN_PARAGRAPH.CENTER, size=11, bold=True, after=0)
    para(doc, "Độc lập - Tự do - Hạnh phúc", align=WD_ALIGN_PARAGRAPH.CENTER, size=10, bold=True, after=4)
    para(doc, "____________________________", align=WD_ALIGN_PARAGRAPH.CENTER, size=8, after=4)
    para(doc, "ĐƠN ĐĂNG KÝ ĐẤT ĐAI, TÀI SẢN GẮN LIỀN VỚI ĐẤT", align=WD_ALIGN_PARAGRAPH.CENTER, size=12, bold=True, after=5)
    para(doc, "Kính gửi: {{recipient}}", after=4)
    para(doc, "1. NGƯỜI SỬ DỤNG ĐẤT, CHỦ SỞ HỮU TÀI SẢN, NGƯỜI QUẢN LÝ ĐẤT", bold=True)
    para(doc, "Họ và tên: {{owner_name}} · Năm sinh: {{owner_birth_year}} · Giấy tờ nhân thân/pháp nhân: {{owner_identity}}")
    para(doc, "Mã số thuế (nếu có): {{owner_tax_code}} · Điện thoại: {{owner_phone}} · Email: {{owner_email}}")
    para(doc, "Địa chỉ: {{owner_address}}", after=1)
    para(doc, "(Nếu có nhiều người cùng sử dụng / sở hữu, thêm từng người trong danh sách Mẫu 15a đính kèm.)", italic=True, size=8.5, after=4)
    para(doc, "2. THỬA ĐẤT ĐĂNG KÝ", bold=True)
    para(doc, "Số thửa: {{parcel_number_main}} · Tờ bản đồ: {{map_sheet_main}} · Địa chỉ: {{land_address}}")
    para(doc, "Diện tích: {{land_area}} m² · Sử dụng chung: {{land_shared_area}} m² · Sử dụng riêng: {{land_private_area}} m²")
    para(doc, "Mục đích sử dụng: {{land_purpose}} · Từ thời điểm: {{use_start_date}} · Thời hạn đề nghị: {{requested_term}}")
    para(doc, "Nguồn gốc sử dụng đất: {{land_origin}}", after=1)
    para(doc, "Quyền / hạn chế quyền đối với thửa đất liền kề (nếu có): {{adjacent_rights}}", after=4)
    para(doc, "3. NHÀ Ở, CÔNG TRÌNH XÂY DỰNG (nếu đề nghị đăng ký / chứng nhận quyền sở hữu)", bold=True)
    para(doc, "Loại công trình: {{building_type_main}} · Diện tích xây dựng: {{building_construction_area_main}} m² · Diện tích sàn / sử dụng: {{building_floor_area_main}} m²")
    para(doc, "Sở hữu chung: {{building_shared_area_main}} m² · Sở hữu riêng: {{building_private_area_main}} m² · Tổng số tầng: {{building_floors_main}} (nổi: {{building_aboveground_floors_main}}, hầm: {{building_basement_floors_main}})")
    para(doc, "Nguồn gốc: {{building_origin_main}} · Năm hoàn thành: {{building_completion_year_main}} · Thời hạn sở hữu đến: {{building_ownership_term_main}}")
    para(doc, "Cam kết chịu trách nhiệm về nhà ở / công trình (nếu thuộc trường hợp phải cam kết): {{building_responsibility}}", after=4)
    para(doc, "4. ĐỀ NGHỊ CỦA NGƯỜI SỬ DỤNG ĐẤT (đánh dấu lựa chọn)", bold=True)
    para(doc, "Đăng ký đất đai, tài sản: {{request_register_land}} · Cấp Giấy chứng nhận: {{request_issue_certificate}} · Ghi nợ tiền sử dụng đất: {{request_defer_fee}}")
    para(doc, "Đề nghị khác: {{request_other}}", after=3)
    para(doc, "5. THÔNG TIN MIỄN, GIẢM NGHĨA VỤ TÀI CHÍNH (nếu có)", bold=True)
    para(doc, "{{fee_exemption}}", after=3)
    para(doc, "6. GIẤY TỜ NỘP KÈM", bold=True)
    para(doc, "{{attachments}}", after=3)
    para(doc, "Tôi/chúng tôi cam đoan nội dung kê khai trên đơn là đúng sự thật và chịu trách nhiệm trước pháp luật.", after=3)
    para(doc, "{{signing_place}}, ngày {{signing_day}} tháng {{signing_month}} năm {{signing_year}}", align=WD_ALIGN_PARAGRAPH.RIGHT, italic=True, after=1)
    para(doc, "NGƯỜI SỬ DỤNG ĐẤT / NGƯỜI KÊ KHAI", align=WD_ALIGN_PARAGRAPH.RIGHT, bold=True, after=0)
    para(doc, "(Ký, ghi rõ họ tên hoặc đóng dấu nếu có)", align=WD_ALIGN_PARAGRAPH.RIGHT, italic=True, size=8.5, after=4)
    para(doc, "{{signer_name}}", align=WD_ALIGN_PARAGRAPH.RIGHT, bold=True)

    doc.add_page_break()
    para(doc, "DANH SÁCH ĐÍNH KÈM ĐƠN ĐĂNG KÝ", align=WD_ALIGN_PARAGRAPH.CENTER, size=12, bold=True, after=6)
    para(doc, "Dùng các bảng dưới đây khi hồ sơ có nhiều người, nhiều thửa đất nông nghiệp hoặc nhiều công trình; thêm dòng tương ứng trong trang tạo hồ sơ.", italic=True, after=6)
    add_repeat_table(doc, "MẪU 15a · DANH SÁCH NGƯỜI CÙNG SỬ DỤNG / SỞ HỮU", ["STT", "Họ tên", "Giấy tờ nhân thân", "Địa chỉ", "Mã số thuế", "Diện tích / tỷ lệ", "Quan hệ / ghi chú"], "co_users", repeat_fields["co_users"])
    add_repeat_table(doc, "MẪU 15b · DANH SÁCH NHIỀU THỬA ĐẤT NÔNG NGHIỆP", ["STT", "Số thửa", "Tờ bản đồ", "Địa chỉ", "Diện tích", "Mục đích", "Nguồn gốc"], "parcels_15b", repeat_fields["parcels_15b"])
    add_repeat_table(doc, "MẪU 15c · DANH SÁCH NHÀ Ở / CÔNG TRÌNH XÂY DỰNG", ["STT", "Loại công trình", "Diện tích xây dựng", "Diện tích sàn", "Chung / riêng", "Số tầng", "Nguồn gốc / năm / thời hạn"], "buildings_15c", repeat_fields["buildings_15c"])

    doc.add_page_break()
    para(doc, "HƯỚNG DẪN KÊ KHAI", align=WD_ALIGN_PARAGRAPH.CENTER, bold=True, size=11, after=5)
    notes = [
        "Ghi cơ quan tiếp nhận theo loại người đăng ký và nơi có đất; kiểm tra cơ quan có thẩm quyền tại thủ tục đang thực hiện.",
        "Cá nhân kê khai họ tên, năm sinh và số định danh cá nhân; tổ chức ghi theo quyết định thành lập hoặc đăng ký kinh doanh.",
        "Địa chỉ ghi nơi thường trú của cá nhân hoặc trụ sở chính của tổ chức.",
        "Nếu không có số thửa / tờ bản đồ thì có thể để trống để cơ quan giải quyết xác định.",
        "Ghi địa chỉ thửa đất theo số nhà, đường, thôn/xóm hoặc khu vực, xã/phường và tỉnh nơi có đất.",
        "Diện tích ghi bằng số, làm tròn đến một chữ số thập phân; tách rõ phần sử dụng chung và riêng nếu có.",
        "Ghi mục đích chính đang sử dụng và thời điểm bắt đầu sử dụng.",
        "Thời hạn có thể ghi ngày kết thúc, ‘Lâu dài’ hoặc ‘-/-’ nếu không xác định được.",
        "Nguồn gốc có thể là Nhà nước giao / cho thuê, nhận chuyển quyền, thừa kế, tặng cho, ông cha để lại hoặc nguồn gốc khác.",
        "Thông tin quyền đối với thửa liền kề ghi theo văn bản xác lập quyền được sử dụng.",
        "Loại công trình có thể là nhà ở riêng lẻ, căn hộ, văn phòng hoặc nhà xưởng.",
        "Diện tích xây dựng là mặt bằng chiếm đất; diện tích sàn ghi tổng diện tích sàn của các tầng khi có nhiều tầng.",
        "Diện tích sở hữu chung là phần thuộc nhiều người; sở hữu riêng là phần thuộc một chủ thể.",
        "Nguồn gốc công trình có thể là tự đầu tư xây dựng, mua hoặc được tặng cho.",
        "Chủ sở hữu tự xác định và chịu trách nhiệm về năm hoàn thành xây dựng.",
        "Thời hạn sở hữu ghi ngày kết thúc hoặc ‘-/-’ nếu không xác định được.",
        "Đánh dấu các đề nghị phù hợp; không chọn nội dung không áp dụng.",
        "Người được miễn, giảm nghĩa vụ tài chính ghi rõ căn cứ và nộp giấy tờ chứng minh theo quy định.",
        "Kê khai giấy tờ nộp kèm; tổ chức có thể cần nộp báo cáo rà soát hiện trạng sử dụng đất theo mẫu tương ứng.",
    ]
    for i, note in enumerate(notes, 1):
        para(doc, f"({i}) {note}", size=8.5, after=3)
    doc.save(OUTPUT / f"{SLUG}.docx")


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / f"{SLUG}.json").write_text(json.dumps(content(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    create_docx()
    print(OUTPUT / f"{SLUG}.docx")
    print(OUTPUT / f"{SLUG}.json")


if __name__ == "__main__":
    main()
