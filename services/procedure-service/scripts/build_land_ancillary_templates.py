"""Build fillable copies of land gift, inheritance, and mortgage instruments."""
from __future__ import annotations

import json
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "app" / "builtin_forms"
PROFILE_HELP = "Tự lấy từ hồ sơ CCCD; người làm đơn không thể sửa thông tin gốc ở đây."


def field(key, label, word_key, kind="text", required=False, *, autofill=None, help_text=""):
    result = {"key": key, "label": label, "word_key": word_key, "type": kind, "required": required}
    if autofill:
        result["autofill"] = autofill
    if help_text:
        result["help_text"] = help_text
    return result


def block(instance, title, fields, *, kind="free_field"):
    return {"id": instance, "instance_id": instance, "block_key": kind,
            "title": title, "order": 1, "fields": fields}


def section(key, title, blocks):
    return {"id": key, "title": title, "order": 1, "blocks": blocks}


def party_fields(prefix, title):
    return [
        field("name", f"Họ tên {title}", f"{prefix}_name", required=True),
        field("birth_year", "Năm sinh", f"{prefix}_birth_year", "number"),
        field("identity", "Số CCCD / định danh", f"{prefix}_identity", "cccd", True),
        field("issue_date", "Ngày cấp giấy tờ", f"{prefix}_issue_date", "issue_date"),
        field("issuer", "Cơ quan cấp", f"{prefix}_issuer"),
        field("address", "Địa chỉ thường trú", f"{prefix}_address", "textarea", True),
        field("phone", "Điện thoại", f"{prefix}_phone", "phone"),
        field("property_role", "Tư cách / phần quyền sở hữu", f"{prefix}_property_role"),
    ]


def gift_content():
    donors = party_fields("donor", "bên tặng cho")
    donees = party_fields("donee", "bên được tặng cho")
    sections = [
        section("parties", "Các bên ký hợp đồng", [
            block("transaction", "Ngày, nơi lập và số hợp đồng", [
                field("number", "Số hợp đồng", "contract_number"),
                field("day", "Ngày", "contract_day", "number", True),
                field("month", "Tháng", "contract_month", "number", True),
                field("year", "Năm", "contract_year", "number", True),
                field("place", "Nơi lập hợp đồng", "contract_place", required=True),
            ]),
            block("donors", "Bên tặng cho (thêm từng người)", donors, kind="repeat_group"),
            block("donees", "Bên được tặng cho (thêm từng người)", donees, kind="repeat_group"),
        ]),
        section("asset", "Quyền sử dụng đất và tài sản", [block("gift_asset", "Thông tin tài sản tặng cho", [
            field("certificate", "Giấy tờ chứng minh quyền sử dụng / sở hữu", "certificate_basis", "textarea", True),
            field("parcel", "Số thửa đất", "parcel_number", required=True),
            field("map_sheet", "Tờ bản đồ số", "map_sheet_number", required=True),
            field("address", "Địa chỉ thửa đất", "property_address", "textarea", True),
            field("area", "Diện tích (m²)", "land_area", "number", True),
            field("area_words", "Diện tích bằng chữ", "land_area_words"),
            field("private_area", "Diện tích sử dụng riêng (m²)", "private_area", "number"),
            field("shared_area", "Diện tích sử dụng chung (m²)", "shared_area", "number"),
            field("purpose", "Mục đích sử dụng", "land_purpose", required=True),
            field("term", "Thời hạn sử dụng", "land_term", required=True),
            field("origin", "Nguồn gốc sử dụng", "land_origin"),
            field("restrictions", "Hạn chế quyền sử dụng (nếu có)", "land_restrictions", "textarea"),
            field("attached_property", "Tài sản gắn liền với đất", "attached_property", "textarea"),
            field("asset_documents", "Giấy tờ về tài sản gắn liền với đất", "asset_documents", "textarea"),
            field("value", "Giá trị tài sản (đồng)", "property_value", "number"),
            field("value_words", "Giá trị bằng chữ", "property_value_words"),
        ])]),
        section("terms", "Thỏa thuận và giấy tờ", [block("gift_terms", "Nội dung giao nhận và phí", [
            field("handover_date", "Thời điểm giao tài sản / giấy tờ", "handover_date", "date"),
            field("effective_from", "Ngày hợp đồng có hiệu lực từ", "effective_from", "date"),
            field("effective_until", "Ngày hợp đồng có hiệu lực đến", "effective_until", "date"),
            field("registration_payer", "Bên chịu trách nhiệm đăng ký", "registration_payer"),
            field("tax_fee_payer", "Bên chịu thuế, lệ phí", "tax_fee_payer"),
            field("copies", "Số bản hợp đồng lập", "contract_copies", "number", True),
            field("term_note", "Ghi chú khác (nếu có)", "contract_note", "textarea"),
        ])]),
        section("notary", "Lời chứng công chứng", [block("notary_info", "Thông tin công chứng", [
            field("notary_date", "Ngày công chứng", "notary_date", "date"),
            field("notary_place", "Địa điểm công chứng", "notary_place"),
            field("notary_name", "Tên công chứng viên", "notary_name"),
            field("office", "Phòng / văn phòng công chứng", "notary_office"),
            field("city", "Tỉnh / thành phố", "notary_city"),
            field("document_number", "Số công chứng", "notary_number"),
            field("book_number", "Quyển số", "notary_book_number"),
        ])]),
    ]
    return {
        "schema_version": 1,
        "name": "Hợp đồng tặng cho quyền sử dụng đất và tài sản gắn liền với đất",
        "code": "", "procedure_code": "DD-006", "domain": "dat_dai", "procedure_id": None,
        "reference_catalog_names": ["Hợp đồng tặng cho quyền sử dụng đất và tài sản gắn liền với đất"],
        "review_notice": "Bản nguồn trong kho có chỗ đánh số điều trùng lặp và một số chi tiết cần rà soát. Bản nhập liệu giữ ý chính theo nguồn; hãy kiểm tra pháp lý và nội dung trước khi ký công chứng.",
        "sections": sections,
        "repeat_groups": [
            {"key": "donors", "title": "Bên tặng cho", "fields": donors},
            {"key": "donees", "title": "Bên được tặng cho", "fields": donees},
        ],
    }


def sole_heir_content():
    fields = [
        field("heir", "Họ tên người nhận di sản", "heir_name", required=True),
        field("birth_date", "Ngày sinh", "heir_birth_date", "dob", True),
        field("identity", "Số CMND / CCCD", "heir_identity", "cccd", True),
        field("issue_date", "Ngày cấp", "heir_issue_date", "issue_date", True),
        field("issuer", "Nơi cấp", "heir_issuer", required=True),
        field("address", "Hộ khẩu thường trú", "heir_address", "textarea", True),
        field("deceased", "Họ tên người để lại di sản", "deceased_name", required=True),
        field("death_date", "Ngày mất", "death_date", "past_date", True),
        field("death_certificate", "Số giấy chứng tử / trích lục khai tử", "death_certificate", required=True),
        field("death_issuer", "Ủy ban nhân dân cấp giấy chứng tử", "death_issuer", required=True),
        field("death_issue_date", "Ngày cấp giấy chứng tử", "death_issue_date", "past_date", True),
        field("inheritance_asset", "Tài sản nhận thừa kế", "inheritance_asset", "textarea", True),
        field("place", "Địa điểm ký", "signing_place", required=True),
        field("day", "Ngày", "signing_day", "number", True),
        field("month", "Tháng", "signing_month", "number", True),
        field("year", "Năm", "signing_year", "number", True),
    ]
    return {
        "schema_version": 1, "name": "Văn bản nhận tài sản thừa kế của người thừa kế duy nhất",
        "code": "", "procedure_code": "DD-006", "domain": "dat_dai", "procedure_id": None,
        "reference_catalog_names": [
            "Văn bản nhận tài sản thừa kế (người thừa kế duy nhất)",
            "Văn bản khai nhận di sản thừa kế quyền sử dụng đất",
        ],
        "review_notice": "Mẫu nguồn chỉ dành cho trường hợp người ký là người thừa kế duy nhất theo pháp luật. Không dùng nếu có người thừa kế khác hoặc có di chúc cần xử lý.",
        "sections": [section("inheritance", "Thông tin nhận di sản", [block("heir_info", "Người nhận và người để lại di sản", fields)])],
        "repeat_groups": [],
    }


def partition_content():
    heirs = [
        field("name", "Họ và tên người thừa kế", "heirs_name", required=True),
        field("birth_year", "Năm sinh", "heirs_birth_year", "number"),
        field("identity", "Số CCCD", "heirs_identity", "cccd", True),
        field("issue_date", "Ngày cấp CCCD", "heirs_issue_date", "issue_date"),
        field("issuer", "Cơ quan cấp", "heirs_issuer"),
        field("address", "Địa chỉ thường trú", "heirs_address", "textarea", True),
        field("relation", "Quan hệ với người để lại di sản", "heirs_relation", required=True),
    ]
    assets = [
        field("deceased", "Người để lại di sản", "deceased_name", required=True),
        field("birth_year", "Năm sinh", "deceased_birth_year", "number"),
        field("death_date", "Ngày mất", "death_date", "past_date", True),
        field("death_extract", "Số trích lục khai tử", "death_extract", required=True),
        field("death_authority", "Cơ quan cấp trích lục", "death_authority", required=True),
        field("death_issue_date", "Ngày cấp trích lục", "death_issue_date", "past_date", True),
        field("property_address", "Địa chỉ tài sản", "property_address", "textarea", True),
        field("parcel", "Số thửa đất", "parcel_number", required=True),
        field("map_sheet", "Tờ bản đồ số", "map_sheet_number", required=True),
        field("certificate", "Số Giấy chứng nhận", "certificate_number", required=True),
        field("certificate_issuer", "Cơ quan cấp Giấy chứng nhận", "certificate_issuer"),
        field("certificate_date", "Ngày cấp Giấy chứng nhận", "certificate_date", "past_date"),
        field("surviving_owner", "Người sở hữu phần tài sản còn lại (nếu có)", "surviving_owner"),
        field("distribution_receiver", "Người được nhận toàn bộ phần di sản", "distribution_receiver", required=True),
        field("attachments", "Giấy tờ kèm theo", "attachments", "textarea", True),
        field("place", "Địa điểm ký", "signing_place", required=True),
        field("day", "Ngày", "signing_day", "number", True),
        field("month", "Tháng", "signing_month", "number", True),
        field("year", "Năm", "signing_year", "number", True),
        field("copies", "Số bản chính", "contract_copies", "number", True),
        field("authority", "Cơ quan / địa điểm lưu văn bản", "receiving_authority", required=True),
    ]
    return {
        "schema_version": 1, "name": "Văn bản thỏa thuận phân chia di sản thừa kế quyền sử dụng đất",
        "code": "", "procedure_code": "DD-006", "domain": "dat_dai", "procedure_id": None,
        "reference_catalog_names": [
            "Văn bản thỏa thuận phân chia di sản thừa kế (tình huống hai người, phần di sản 1/2)",
            "Văn bản thỏa thuận phân chia di sản thừa kế quyền sử dụng đất",
        ],
        "review_notice": "Bản nguồn mô tả phần di sản là 1/2 quyền sử dụng đất và tài sản gắn liền với đất. Kiểm tra tỷ lệ sở hữu thực tế, người thừa kế và điều khoản trước khi ký.",
        "sections": [
            section("heirs", "Những người thừa kế", [block("heirs", "Thêm từng người thừa kế", heirs, kind="repeat_group")]),
            section("estate", "Di sản và nội dung thống nhất", [block("estate_info", "Thông tin người mất và tài sản", assets)]),
        ],
        "repeat_groups": [{"key": "heirs", "title": "Người thừa kế tham gia thỏa thuận", "fields": heirs}],
    }


def mortgage_content():
    mortgagors = party_fields("mortgagor", "bên thế chấp")
    mortgagees = party_fields("mortgagee", "bên nhận thế chấp")
    properties = [
        field("parcel", "Số thửa đất", "property_parcel", required=True),
        field("map_sheet", "Tờ bản đồ số", "property_map_sheet", required=True),
        field("address", "Địa chỉ tài sản", "property_address", "textarea", True),
        field("land_type", "Loại đất", "property_land_type"),
        field("area", "Diện tích (m²)", "property_area", "number", True),
        field("certificate", "Giấy chứng nhận / số phát hành", "property_certificate", required=True),
        field("certificate_issuer", "Cơ quan cấp", "property_issuer"),
        field("certificate_date", "Ngày cấp", "property_issue_date", "past_date"),
        field("description", "Tài sản gắn liền với đất / mô tả", "property_description", "textarea"),
    ]
    sections = [
        section("parties", "Các bên trong hợp đồng", [
            block("transaction", "Số hợp đồng, ngày và nơi ký", [
                field("number", "Số hợp đồng", "contract_number"),
                field("day", "Ngày", "contract_day", "number", True),
                field("month", "Tháng", "contract_month", "number", True),
                field("year", "Năm", "contract_year", "number", True),
                field("place", "Nơi lập hợp đồng", "contract_place", required=True),
            ]),
            block("mortgagors", "Bên thế chấp (thêm từng người)", mortgagors, kind="repeat_group"),
            block("mortgagees", "Bên nhận thế chấp (thêm từng người)", mortgagees, kind="repeat_group"),
        ]),
        section("obligation", "Nghĩa vụ được bảo đảm", [block("obligation_info", "Nội dung khoản vay / nghĩa vụ", [
            field("obligation", "Nghĩa vụ được bảo đảm", "secured_obligation", "textarea", True),
            field("obligation_amount", "Số tiền / giá trị nghĩa vụ", "obligation_amount", "number", True),
            field("obligation_words", "Số tiền bằng chữ", "obligation_words"),
            field("repayment_terms", "Thời hạn và phương thức thực hiện", "repayment_terms", "textarea", True),
        ])]),
        section("property", "Tài sản thế chấp và giá trị", [
            block("secured_assets", "Từng tài sản thế chấp", properties, kind="repeat_group"),
            block("valuation", "Giá trị và xử lý tài sản", [
                field("value", "Giá trị tài sản thế chấp (đồng)", "collateral_value", "number", True),
                field("value_words", "Giá trị bằng chữ", "collateral_value_words"),
                field("valuation_date", "Ngày xác định giá trị", "valuation_date", "past_date"),
                field("disposal_method", "Phương thức xử lý khi vi phạm nghĩa vụ", "disposal_method", "textarea", True),
                field("fee_payer", "Bên chịu lệ phí đăng ký", "registration_fee_payer"),
            ]),
        ]),
        section("closing", "Thời hạn, chữ ký và công chứng", [block("closing_info", "Hiệu lực và số bản", [
            field("effective_from", "Có hiệu lực từ ngày", "effective_from", "date", True),
            field("effective_until", "Có hiệu lực đến ngày", "effective_until", "date"),
            field("copies", "Số bản hợp đồng", "contract_copies", "number", True),
            field("notary_date", "Ngày công chứng", "notary_date", "date"),
            field("notary_place", "Địa điểm công chứng", "notary_place"),
            field("notary_name", "Công chứng viên", "notary_name"),
            field("notary_office", "Phòng / văn phòng công chứng", "notary_office"),
            field("notary_number", "Số / quyển công chứng", "notary_reference"),
        ])]),
    ]
    return {
        "schema_version": 1, "name": "Hợp đồng thế chấp quyền sử dụng đất và tài sản gắn liền với đất",
        "code": "", "procedure_code": "DD-010", "domain": "dat_dai", "procedure_id": None,
        "reference_catalog_names": [
            "Hợp đồng thế chấp quyền sử dụng đất và tài sản gắn liền với đất",
            "Hợp đồng thế chấp quyền sử dụng đất bảo đảm nghĩa vụ vay vốn",
        ],
        "review_notice": "Bản nguồn có đoạn nhắc đến căn hộ chung cư và số điều bị lặp. Hãy rà soát loại tài sản, điều khoản và nội dung pháp lý trước khi ký hoặc công chứng.",
        "sections": sections,
        "repeat_groups": [
            {"key": "mortgagors", "title": "Bên thế chấp", "fields": mortgagors},
            {"key": "mortgagees", "title": "Bên nhận thế chấp", "fields": mortgagees},
            {"key": "secured_assets", "title": "Tài sản thế chấp", "fields": properties},
        ],
    }


def set_font(run, *, size=9.5, bold=False, italic=False):
    run.font.name = "Times New Roman"
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Times New Roman")
    run.font.size = Pt(size)
    run.bold = bold
    run.italic = italic


def para(doc, text="", *, align=None, size=9.5, bold=False, italic=False, after=3, page_break=False):
    p = doc.add_paragraph()
    if align is not None:
        p.alignment = align
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.line_spacing = 1.0
    p.paragraph_format.keep_together = True
    if page_break:
        p.paragraph_format.page_break_before = True
    if text:
        set_font(p.add_run(text), size=size, bold=bold, italic=italic)
    return p


def base_doc(title):
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.top_margin, sec.bottom_margin = Cm(1.7), Cm(1.7)
    sec.left_margin = sec.right_margin = Cm(1.8)
    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Times New Roman")
    normal.font.size = Pt(9.5)
    para(doc, "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", align=WD_ALIGN_PARAGRAPH.CENTER, size=11, bold=True, after=0)
    para(doc, "Độc lập - Tự do - Hạnh phúc", align=WD_ALIGN_PARAGRAPH.CENTER, size=10, bold=True, after=5)
    para(doc, title, align=WD_ALIGN_PARAGRAPH.CENTER, size=12, bold=True, after=6)
    return doc


def add_group_table(doc, title, headings, key, cells):
    para(doc, title, bold=True, after=2)
    table = doc.add_table(rows=1, cols=len(headings))
    table.style = "Table Grid"
    for cell, heading in zip(table.rows[0].cells, headings):
        cell.text = heading
    row = table.add_row()
    row.cells[0].text = f"{{{{#{key}}}}} {{{{{key}.index}}}}"
    for cell, text in zip(row.cells[1:], cells):
        cell.text = text
    row.cells[-1].text += f" {{{{/{key}}}}}"
    for r in table.rows:
        for c in r.cells:
            for p in c.paragraphs:
                for run in p.runs:
                    set_font(run, size=7.5, bold=(r is table.rows[0]))
    doc.add_paragraph()


def gift_doc():
    doc = base_doc("HỢP ĐỒNG TẶNG CHO QUYỀN SỬ DỤNG ĐẤT VÀ TÀI SẢN GẮN LIỀN VỚI ĐẤT")
    para(doc, "Số: {{contract_number}}", align=WD_ALIGN_PARAGRAPH.RIGHT, after=3)
    para(doc, "Hôm nay, ngày {{contract_day}} tháng {{contract_month}} năm {{contract_year}}, tại {{contract_place}}, chúng tôi gồm có:", after=4)
    add_group_table(doc, "BÊN TẶNG CHO (BÊN A)", ["STT", "Họ tên · năm sinh", "CCCD · ngày cấp · nơi cấp", "Địa chỉ · điện thoại", "Tư cách / phần quyền"], "donors", ["{{donors.name}} · {{donors.birth_year}}", "{{donors.identity}} · {{donors.issue_date}} · {{donors.issuer}}", "{{donors.address}} · {{donors.phone}}", "{{donors.property_role}}"])
    add_group_table(doc, "BÊN ĐƯỢC TẶNG CHO (BÊN B)", ["STT", "Họ tên · năm sinh", "CCCD · ngày cấp · nơi cấp", "Địa chỉ · điện thoại", "Tư cách"], "donees", ["{{donees.name}} · {{donees.birth_year}}", "{{donees.identity}} · {{donees.issue_date}} · {{donees.issuer}}", "{{donees.address}} · {{donees.phone}}", "{{donees.property_role}}"])
    para(doc, "ĐIỀU 1. ĐỐI TƯỢNG CỦA HỢP ĐỒNG", bold=True, after=2)
    para(doc, "Quyền sử dụng đất theo {{certificate_basis}}; thửa {{parcel_number}}, tờ bản đồ {{map_sheet_number}}, tại {{property_address}}. Diện tích {{land_area}} m² (bằng chữ: {{land_area_words}}); sử dụng riêng {{private_area}} m², sử dụng chung {{shared_area}} m². Mục đích {{land_purpose}}, thời hạn {{land_term}}, nguồn gốc {{land_origin}}.")
    para(doc, "Hạn chế quyền sử dụng (nếu có): {{land_restrictions}}. Tài sản gắn liền với đất: {{attached_property}}. Giấy tờ sở hữu tài sản: {{asset_documents}}.")
    para(doc, "Giá trị tài sản là {{property_value}} đồng (bằng chữ: {{property_value_words}}).", after=5)
    para(doc, "ĐIỀU 2. VIỆC ĐĂNG KÝ, THUẾ VÀ LỆ PHÍ", bold=True, after=2)
    para(doc, "Việc đăng ký tặng cho do {{registration_payer}} thực hiện. Thuế và lệ phí do {{tax_fee_payer}} chịu theo thỏa thuận của các bên.")
    para(doc, "ĐIỀU 3. GIAO NHẬN VÀ ĐĂNG KÝ QUYỀN", bold=True, after=2)
    para(doc, "Bên A giao tài sản và giấy tờ liên quan cho Bên B vào {{handover_date}}. Bên B thực hiện đăng ký quyền sử dụng đất, quyền sở hữu tài sản tại cơ quan có thẩm quyền.")
    para(doc, "ĐIỀU 4. QUYỀN, NGHĨA VỤ VÀ CAM ĐOAN", bold=True, after=2)
    para(doc, "Bên A giao đúng tài sản và giấy tờ như đã thỏa thuận. Bên B đăng ký quyền, bảo đảm quyền của người thứ ba và thực hiện nghĩa vụ theo pháp luật đất đai. Các bên cam đoan thông tin nhân thân và tài sản là đúng; việc giao kết tự nguyện, không bị lừa dối hoặc ép buộc; tài sản không tranh chấp, không bị kê biên để bảo đảm thi hành án tại thời điểm ký.")
    para(doc, "ĐIỀU 5. GIẢI QUYẾT TRANH CHẤP VÀ HIỆU LỰC", bold=True, after=2)
    para(doc, "Tranh chấp được ưu tiên giải quyết bằng thương lượng; nếu không thành, mỗi bên có quyền yêu cầu Tòa án có thẩm quyền giải quyết. Hợp đồng có hiệu lực từ {{effective_from}} đến {{effective_until}} và được lập thành {{contract_copies}} bản có giá trị như nhau. {{contract_note}}")
    para(doc, "BÊN TẶNG CHO (BÊN A)\n(Ký / điểm chỉ, ghi rõ họ tên)", after=12)
    para(doc, "BÊN ĐƯỢC TẶNG CHO (BÊN B)\n(Ký / điểm chỉ, ghi rõ họ tên)", after=12)
    para(doc, "LỜI CHỨNG CỦA CÔNG CHỨNG VIÊN", bold=True)
    para(doc, "Ngày {{notary_date}}, tại {{notary_place}}, tôi {{notary_name}}, công chứng viên tại {{notary_office}}, tỉnh / thành phố {{notary_city}}, chứng nhận các bên tự nguyện giao kết; có năng lực hành vi dân sự phù hợp; nội dung không vi phạm điều cấm của pháp luật và không trái đạo đức xã hội.")
    para(doc, "Số công chứng {{notary_number}} · Quyển số {{notary_book_number}}", after=14)
    para(doc, "CÔNG CHỨNG VIÊN", align=WD_ALIGN_PARAGRAPH.RIGHT, bold=True)
    return doc


def sole_heir_doc():
    doc = base_doc("VĂN BẢN NHẬN TÀI SẢN THỪA KẾ")
    para(doc, "Tôi là: {{heir_name}} · Sinh ngày: {{heir_birth_date}}", after=5)
    para(doc, "CMND / CCCD số: {{heir_identity}} · Cấp ngày: {{heir_issue_date}} · Nơi cấp: {{heir_issuer}}", after=4)
    para(doc, "Hộ khẩu thường trú: {{heir_address}}", after=6)
    para(doc, "Tôi là người thừa kế duy nhất theo pháp luật của ông / bà {{deceased_name}}, chết ngày {{death_date}} theo Giấy chứng tử / trích lục khai tử số {{death_certificate}}, do {{death_issuer}} cấp ngày {{death_issue_date}}.", after=7)
    para(doc, "Tôi xin nhận tài sản thừa kế của ông / bà {{deceased_name}} để lại như sau:", after=3)
    para(doc, "{{inheritance_asset}}", after=10)
    para(doc, "Tôi xin cam đoan:", bold=True, after=2)
    para(doc, "- Những thông tin đã ghi trong văn bản này là đúng sự thật.")
    para(doc, "- Ngoài tôi ra, ông / bà {{deceased_name}} không còn người thừa kế nào khác.", after=15)
    para(doc, "{{signing_place}}, ngày {{signing_day}} tháng {{signing_month}} năm {{signing_year}}", align=WD_ALIGN_PARAGRAPH.RIGHT, italic=True)
    para(doc, "NGƯỜI NHẬN TÀI SẢN THỪA KẾ", align=WD_ALIGN_PARAGRAPH.RIGHT, bold=True, after=14)
    para(doc, "(Ký và ghi rõ họ tên)", align=WD_ALIGN_PARAGRAPH.RIGHT, italic=True)
    para(doc, "{{heir_name}}", align=WD_ALIGN_PARAGRAPH.RIGHT, bold=True)
    return doc


def partition_doc():
    doc = base_doc("VĂN BẢN THỎA THUẬN PHÂN CHIA DI SẢN THỪA KẾ")
    para(doc, "Hôm nay, ngày {{signing_day}} tháng {{signing_month}} năm {{signing_year}}, tại {{signing_place}}, chúng tôi gồm có:", after=4)
    add_group_table(doc, "I. NHỮNG NGƯỜI THỪA KẾ THEO PHÁP LUẬT", ["STT", "Họ tên · năm sinh", "CCCD · ngày cấp · nơi cấp", "Địa chỉ thường trú", "Quan hệ với người mất"], "heirs", ["{{heirs.name}} · {{heirs.birth_year}}", "{{heirs.identity}} · {{heirs.issue_date}} · {{heirs.issuer}}", "{{heirs.address}}", "{{heirs.relation}}"])
    para(doc, "II. NỘI DUNG THỐNG NHẤT", bold=True, after=2)
    para(doc, "Chúng tôi là những người thừa kế theo pháp luật, thuộc hàng thừa kế thứ nhất, nhận di sản của ông / bà {{deceased_name}}, sinh năm {{deceased_birth_year}}, chết ngày {{death_date}} theo trích lục khai tử số {{death_extract}}, do {{death_authority}} cấp ngày {{death_issue_date}}.")
    para(doc, "Di sản là một phần hai (1/2) quyền sử dụng đất và tài sản gắn liền với đất tại {{property_address}}, thửa {{parcel_number}}, tờ bản đồ {{map_sheet_number}}, theo Giấy chứng nhận số {{certificate_number}}, do {{certificate_issuer}} cấp ngày {{certificate_date}}. Phần còn lại là tài sản riêng của {{surviving_owner}}.")
    para(doc, "Chúng tôi thống nhất để {{distribution_receiver}} được quản lý, sử dụng, sở hữu và định đoạt toàn bộ phần di sản nêu trên.")
    para(doc, "III. CAM ĐOAN", bold=True, after=2)
    para(doc, "Thông tin trong văn bản là đúng sự thật. Chúng tôi không bỏ sót người thừa kế; nếu có người khác xuất trình được căn cứ chứng minh quyền thừa kế, chúng tôi chịu trách nhiệm trước pháp luật và thực hiện nghĩa vụ bằng tài sản riêng.")
    para(doc, "IV. GIẤY TỜ KÈM THEO", bold=True, after=2)
    para(doc, "{{attachments}}")
    para(doc, "Văn bản lập thành {{contract_copies}} bản chính; các bản có giá trị như nhau và lưu tại {{receiving_authority}}.", after=12)
    add_group_table(doc, "NHỮNG NGƯỜI THỎA THUẬN PHÂN CHIA DI SẢN", ["STT", "Họ và tên", "Chữ ký"], "heirs", ["{{heirs.name}}", "Ký / điểm chỉ"])
    return doc


def mortgage_doc():
    doc = base_doc("HỢP ĐỒNG THẾ CHẤP QUYỀN SỬ DỤNG ĐẤT VÀ TÀI SẢN GẮN LIỀN VỚI ĐẤT")
    para(doc, "Số: {{contract_number}}", align=WD_ALIGN_PARAGRAPH.RIGHT)
    para(doc, "Hôm nay, ngày {{contract_day}} tháng {{contract_month}} năm {{contract_year}}, tại {{contract_place}}, chúng tôi gồm có:", after=4)
    add_group_table(doc, "BÊN THẾ CHẤP (BÊN A)", ["STT", "Họ tên · năm sinh", "CCCD · ngày cấp · nơi cấp", "Địa chỉ · điện thoại", "Tư cách"], "mortgagors", ["{{mortgagors.name}} · {{mortgagors.birth_year}}", "{{mortgagors.identity}} · {{mortgagors.issue_date}} · {{mortgagors.issuer}}", "{{mortgagors.address}} · {{mortgagors.phone}}", "{{mortgagors.property_role}}"])
    add_group_table(doc, "BÊN NHẬN THẾ CHẤP (BÊN B)", ["STT", "Họ tên · năm sinh", "CCCD · ngày cấp · nơi cấp", "Địa chỉ · điện thoại", "Tư cách"], "mortgagees", ["{{mortgagees.name}} · {{mortgagees.birth_year}}", "{{mortgagees.identity}} · {{mortgagees.issue_date}} · {{mortgagees.issuer}}", "{{mortgagees.address}} · {{mortgagees.phone}}", "{{mortgagees.property_role}}"])
    para(doc, "ĐIỀU 1. NGHĨA VỤ ĐƯỢC BẢO ĐẢM", bold=True)
    para(doc, "{{secured_obligation}} · Giá trị nghĩa vụ: {{obligation_amount}} đồng (bằng chữ: {{obligation_words}}). Thời hạn và phương thức thực hiện: {{repayment_terms}}.")
    para(doc, "ĐIỀU 2. TÀI SẢN THẾ CHẤP", bold=True)
    add_group_table(doc, "Danh sách tài sản", ["STT", "Thửa · tờ · địa chỉ", "Loại đất · diện tích", "Giấy chứng nhận", "Mô tả tài sản"], "secured_assets", ["Thửa {{secured_assets.parcel}} · tờ {{secured_assets.map_sheet}} · {{secured_assets.address}}", "{{secured_assets.land_type}} · {{secured_assets.area}} m²", "{{secured_assets.certificate}} · {{secured_assets.certificate_issuer}} · {{secured_assets.certificate_date}}", "{{secured_assets.description}}"])
    para(doc, "ĐIỀU 3. GIÁ TRỊ TÀI SẢN THẾ CHẤP", bold=True)
    para(doc, "Giá trị tài sản là {{collateral_value}} đồng (bằng chữ: {{collateral_value_words}}), xác định ngày {{valuation_date}}.")
    para(doc, "ĐIỀU 4. QUYỀN VÀ NGHĨA VỤ CỦA BÊN A", bold=True)
    para(doc, "Bên A giao giấy chứng nhận cho Bên B; không chuyển nhượng, tặng cho, cho thuê hoặc dùng tài sản để bảo đảm nghĩa vụ khác nếu không được Bên B đồng ý; bảo quản tài sản và tạo điều kiện để Bên B kiểm tra. Bên A được tiếp tục sử dụng tài sản, nhận tiền vay và nhận lại giấy tờ sau khi hoàn thành nghĩa vụ.")
    para(doc, "ĐIỀU 5. QUYỀN VÀ NGHĨA VỤ CỦA BÊN B", bold=True)
    para(doc, "Bên B phối hợp đăng ký thế chấp, giữ gìn giấy tờ được giao, kiểm tra tài sản và trả lại giấy chứng nhận khi nghĩa vụ bảo đảm đã hoàn thành.")
    para(doc, "ĐIỀU 6. ĐĂNG KÝ, LỆ PHÍ VÀ XỬ LÝ TÀI SẢN", bold=True)
    para(doc, "Việc đăng ký thế chấp do các bên thực hiện theo quy định. Lệ phí do {{registration_fee_payer}} chịu. Khi nghĩa vụ đến hạn mà không được thực hiện, tài sản được xử lý theo phương thức: {{disposal_method}}.")
    para(doc, "ĐIỀU 7. TRANH CHẤP VÀ CAM ĐOAN", bold=True)
    para(doc, "Các bên thương lượng giải quyết tranh chấp; nếu không đạt được thỏa thuận thì yêu cầu Tòa án có thẩm quyền giải quyết. Các bên cam đoan thông tin nhân thân, tài sản là đúng; việc ký kết tự nguyện; tài sản đủ điều kiện thế chấp và không tranh chấp, không bị kê biên tại thời điểm giao kết.")
    para(doc, "ĐIỀU 8. HIỆU LỰC HỢP ĐỒNG", bold=True)
    para(doc, "Hợp đồng có hiệu lực từ {{effective_from}} đến {{effective_until}}; được lập thành {{contract_copies}} bản, mỗi bên giữ một bản có giá trị như nhau.")
    para(doc, "BÊN THẾ CHẤP (BÊN A)\n(Ký / điểm chỉ, ghi rõ họ tên)", after=12)
    para(doc, "BÊN NHẬN THẾ CHẤP (BÊN B)\n(Ký / điểm chỉ, ghi rõ họ tên)", after=12)
    para(doc, "PHẦN GHI CỦA CƠ QUAN ĐĂNG KÝ", bold=True, page_break=True)
    para(doc, "Xác nhận giấy tờ sử dụng đất, hiện trạng thửa đất, chủ sử dụng, diện tích và loại đất: .............................................................................................................................", after=8)
    para(doc, "LỜI CHỨNG CỦA CÔNG CHỨNG VIÊN", bold=True)
    para(doc, "Ngày {{notary_date}}, tại {{notary_place}}, công chứng viên {{notary_name}} thuộc {{notary_office}} chứng nhận các bên tự nguyện giao kết; có năng lực hành vi phù hợp; nội dung không vi phạm điều cấm của pháp luật và không trái đạo đức xã hội.")
    para(doc, "Số / quyển công chứng: {{notary_reference}}", after=14)
    para(doc, "CÔNG CHỨNG VIÊN", align=WD_ALIGN_PARAGRAPH.RIGHT, bold=True)
    return doc


BUILDERS = [
    ("hop-dong-tang-cho-dat", gift_content, gift_doc),
    ("van-ban-nhan-tai-san-thua-ke-duy-nhat", sole_heir_content, sole_heir_doc),
    ("van-ban-thoa-thuan-phan-chia-di-san", partition_content, partition_doc),
    ("hop-dong-the-chap-quyen-su-dung-dat", mortgage_content, mortgage_doc),
]


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for slug, schema_builder, document_builder in BUILDERS:
        schema = schema_builder()
        (OUTPUT / f"{slug}.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        document_builder().save(OUTPUT / f"{slug}.docx")
        print(OUTPUT / f"{slug}.docx")


if __name__ == "__main__":
    main()
