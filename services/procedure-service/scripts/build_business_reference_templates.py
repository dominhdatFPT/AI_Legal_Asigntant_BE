"""Build fillable Word copies for the business forms in the reference catalog.

The read-only source documents remain untouched. These copies use the same
schema and placeholder format as the Mẫu 18 fill flow, with repeatable rows for
parties, business lines, members, shareholders, tax branches, and change items.
"""
from __future__ import annotations

import json
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "app" / "builtin_forms"
PROFILE_HELP = "Lấy từ hồ sơ CCCD; thông tin định danh không thể sửa trong đơn."
REVIEW_NOTICE = (
    "Bản Word điền được dựng theo tài liệu hiện có trong Kho mẫu. Hãy kiểm tra phiên bản mẫu, "
    "điều kiện và yêu cầu của cơ quan tiếp nhận trước khi ký hoặc nộp."
)


def field(key, label, kind="text", required=False, *, word_key=None, autofill=None,
          help_text="", options=None, condition=None):
    result = {
        "key": key,
        "label": label,
        "word_key": word_key or key,
        "type": kind,
        "required": required,
    }
    if autofill:
        result["autofill"] = autofill
    if help_text:
        result["help_text"] = help_text
    if options:
        result["options"] = options
    if condition:
        result["condition"] = condition
    return result


def block(instance, title, fields, kind="free_field", condition=None):
    result = {"id": instance, "instance_id": instance, "block_key": kind,
              "title": title, "order": 1, "fields": fields}
    if condition:
        result["condition"] = condition
    return result


def section(key, title, blocks, order):
    return {"id": key, "title": title, "order": order, "blocks": blocks}


def repeat_field(key, label, kind="text", required=False, *, options=None, help_text=""):
    return field(key, label, kind, required, word_key=key, options=options, help_text=help_text)


def applicant_fields(prefix="applicant", title="Người làm đơn"):
    return [
        field("name", "Họ và tên", "text", True, word_key=f"{prefix}_name", autofill="profile.full_name", help_text=PROFILE_HELP),
        field("birth_date", "Ngày sinh", "dob", True, word_key=f"{prefix}_birth_date", autofill="profile.dob", help_text=PROFILE_HELP),
        field("identity", "Số định danh cá nhân / CCCD", "cccd", True, word_key=f"{prefix}_identity", autofill="profile.cccd_number", help_text=PROFILE_HELP),
        field("issue_date", "Ngày cấp", "issue_date", False, word_key=f"{prefix}_issue_date", autofill="profile.cccd_issue_date", help_text=PROFILE_HELP),
        field("issue_place", "Nơi cấp", "text", False, word_key=f"{prefix}_issue_place", autofill="profile.cccd_issue_place", help_text=PROFILE_HELP),
        field("address", "Địa chỉ thường trú / liên hệ", "textarea", True, word_key=f"{prefix}_address", autofill="profile.permanent_address", help_text=PROFILE_HELP),
        field("phone", "Điện thoại", "phone", False, word_key=f"{prefix}_phone", autofill="profile.phone"),
        field("email", "Email", "email", False, word_key=f"{prefix}_email", autofill="profile.email"),
    ]


def common_company_fields():
    return [
        field("company_name_vn", "Tên doanh nghiệp bằng tiếng Việt", "text", True),
        field("company_name_foreign", "Tên doanh nghiệp bằng tiếng nước ngoài (nếu có)"),
        field("company_short_name", "Tên viết tắt (nếu có)"),
        field("address_detail", "Số nhà, đường, tổ/xóm/ấp/thôn", "textarea", True),
        field("ward", "Xã / phường / đặc khu", "text", True),
        field("province", "Tỉnh / thành phố", "text", True),
        field("phone", "Điện thoại", "phone", True),
        field("fax", "Fax (nếu có)"),
        field("email", "Email", "email"),
        field("website", "Website (nếu có)"),
        field("headquarters_in_zone", "Khu công nghiệp / khu chế xuất / khu kinh tế / khu công nghệ cao (nếu có)", "textarea"),
        field("social_enterprise", "Doanh nghiệp xã hội (nếu có)", "select", False, options=["Không", "Có"]),
        field("company_phone_for_tax", "Số điện thoại đăng ký thuế", "phone", True),
        field("accounting_year", "Năm tài chính bắt đầu", "text", True, help_text="Ví dụ: ngày 01 tháng 01."),
        field("accounting_method", "Chế độ kế toán áp dụng", "select", True,
              options=["Theo chế độ kế toán doanh nghiệp hiện hành", "Theo chế độ kế toán doanh nghiệp nhỏ và vừa", "Khác"]),
        field("accounting_method_other", "Chế độ kế toán khác", "text", False,
              condition={"field": "company_info.accounting_method", "equals": "Khác"}),
        field("tax_period", "Hình thức kê khai thuế GTGT", "select", True,
              options=["Theo tháng", "Theo quý", "Theo từng lần phát sinh", "Không thuộc trường hợp kê khai GTGT"]),
    ]


def common_business_lines():
    return [
        repeat_field("name", "Tên ngành, nghề kinh doanh", "text", True),
        repeat_field("code", "Mã ngành cấp 4 (nếu đã xác định)"),
        repeat_field("main", "Ngành nghề chính", "select", True, options=["Có", "Không"]),
    ]


def business_schema(slug):
    if slug == "hop-dong-thue-mat-bang-kinh-doanh":
        lessors = [
            repeat_field("name", "Họ tên / tên tổ chức cho thuê", "text", True),
            repeat_field("birth_year", "Năm sinh (nếu là cá nhân)", "number"),
            repeat_field("identity", "CCCD / mã số doanh nghiệp", "text", True),
            repeat_field("address", "Địa chỉ", "textarea", True),
            repeat_field("phone", "Điện thoại", "phone"),
            repeat_field("representative", "Người đại diện (nếu là tổ chức)"),
        ]
        lessees = [
            repeat_field("name", "Họ tên / tên doanh nghiệp thuê", "text", True),
            repeat_field("birth_year", "Năm sinh người ký (nếu là cá nhân)", "number"),
            repeat_field("identity", "CCCD / mã số doanh nghiệp", "text", True),
            repeat_field("address", "Địa chỉ", "textarea", True),
            repeat_field("phone", "Điện thoại", "phone"),
            repeat_field("representative", "Người đại diện doanh nghiệp (nếu có)"),
        ]
        sections = [
            section("agreement", "Ngày, nơi ký và các bên", [
                block("agreement_info", "Thông tin hợp đồng", [field("number", "Số hợp đồng"), field("signing_date", "Ngày ký", "date", True), field("signing_place", "Nơi ký", "text", True)]),
                block("lessors", "Bên cho thuê (có thể thêm nhiều người)", lessors, "repeat_group"),
                block("lessees", "Bên thuê (có thể thêm nhiều người)", lessees, "repeat_group"),
            ], 1),
            section("premises", "Mặt bằng cho thuê", [block("premises_info", "Thông tin mặt bằng", [
                field("address", "Địa chỉ mặt bằng", "textarea", True), field("area", "Diện tích (m²)", "number", True),
                field("condition", "Hiện trạng bàn giao", "textarea", True), field("ownership_basis", "Giấy tờ chứng minh quyền cho thuê", "textarea", True),
                field("business_purpose", "Mục đích kinh doanh", "textarea", True),
            ])], 2),
            section("payment", "Thời hạn và thanh toán", [block("payment_info", "Giá, cọc, thanh toán", [
                field("start_date", "Ngày bắt đầu thuê", "date", True), field("end_date", "Ngày kết thúc thuê", "date", True),
                field("monthly_rent", "Giá thuê mỗi tháng (đồng)", "number", True), field("rent_in_words", "Giá thuê bằng chữ", "text", True),
                field("payment_method", "Phương thức và ngày thanh toán", "textarea", True), field("deposit", "Tiền đặt cọc (đồng)", "number", True),
                field("deposit_return", "Điều kiện hoàn trả tiền cọc", "textarea", True), field("utilities", "Điện, nước và chi phí khác", "textarea"),
            ])], 3),
            section("terms", "Cam kết và chữ ký", [block("terms_info", "Điều khoản", [
                field("lessor_obligations", "Quyền và nghĩa vụ bên cho thuê", "textarea", True),
                field("lessee_obligations", "Quyền và nghĩa vụ bên thuê", "textarea", True),
                field("termination_terms", "Chấm dứt hợp đồng / báo trước", "textarea", True),
                field("dispute_terms", "Giải quyết tranh chấp", "textarea", True),
                field("copy_count", "Số bản hợp đồng", "number", True),
            ])], 4),
        ]
        return make_schema("Hợp đồng cho thuê mặt bằng kinh doanh", "doanh_nghiep", "DN-006", [
            "Hợp đồng cho thuê mặt bằng kinh doanh"], sections,
            {"lessors": lessors, "lessees": lessees}, REVIEW_NOTICE)

    if slug == "giay-de-nghi-dang-ky-ho-kinh-doanh":
        lines = [repeat_field("name", "Tên ngành, nghề kinh doanh", "text", True), repeat_field("code", "Mã ngành (nếu có)")]
        f = [field("recipient", "Cơ quan đăng ký kinh doanh cấp xã", "text", True),
             field("business_name", "Tên hộ kinh doanh (viết bằng chữ in hoa)", "text", True),
             field("business_address", "Địa điểm kinh doanh", "textarea", True),
             field("phone", "Điện thoại", "phone", True), field("email", "Email", "email"),
             field("capital", "Vốn kinh doanh (đồng)", "number", True), field("capital_words", "Vốn kinh doanh bằng chữ", "text", True),
             field("employees", "Số lao động dự kiến", "number"), field("owner_name", "Chủ hộ kinh doanh", "text", True, autofill="profile.full_name", help_text=PROFILE_HELP),
             field("owner_birth_date", "Ngày sinh chủ hộ", "dob", True, autofill="profile.dob", help_text=PROFILE_HELP),
             field("owner_gender", "Giới tính", "select", True, options=["Nam", "Nữ", "Khác"]),
             field("owner_identity", "Số định danh cá nhân / CCCD", "cccd", True, autofill="profile.cccd_number", help_text=PROFILE_HELP),
             field("owner_issue_date", "Ngày cấp CCCD", "issue_date", False, autofill="profile.cccd_issue_date", help_text=PROFILE_HELP),
             field("owner_issue_place", "Nơi cấp CCCD", "text", False, autofill="profile.cccd_issue_place", help_text=PROFILE_HELP),
             field("owner_address", "Địa chỉ thường trú", "textarea", True, autofill="profile.permanent_address", help_text=PROFILE_HELP),
             field("place", "Địa điểm lập", "text", True), field("signing_date", "Ngày lập", "date", True)]
        sections = [section("application", "Thông tin đăng ký hộ kinh doanh", [block("application_info", "Giấy đề nghị đăng ký hộ kinh doanh", f)], 1),
                    section("business_lines", "Ngành, nghề kinh doanh", [block("business_lines", "Thêm ngành nghề", lines, "repeat_group")], 2)]
        return make_schema("Giấy đề nghị đăng ký hộ kinh doanh", "ho_kinh_doanh", "HKD-001",
                           ["Giấy đề nghị đăng ký hộ kinh doanh"], sections, {"business_lines": lines}, REVIEW_NOTICE)

    if slug == "thong-bao-giai-the-doanh-nghiep":
        f = [field("recipient", "Cơ quan đăng ký kinh doanh cấp tỉnh", "text", True), field("notice_number", "Số thông báo"),
             field("company_name", "Tên doanh nghiệp (viết bằng chữ in hoa)", "text", True),
             field("company_code", "Mã số doanh nghiệp / mã số thuế", "text", True),
             field("certificate_number", "Số giấy chứng nhận đăng ký kinh doanh (nếu chưa có mã số)", "text"),
             field("certificate_date", "Ngày cấp giấy chứng nhận (nếu có)", "date"),
             field("certificate_place", "Nơi cấp giấy chứng nhận (nếu có)"),
             field("settlement_confirmed", "Xác nhận đã thanh toán hết nợ, nghĩa vụ tài sản và không có tranh chấp", "select", True,
                  options=["Xác nhận"]),
             field("signer_name", "Người đại diện theo pháp luật", "text", True, autofill="profile.full_name", help_text=PROFILE_HELP),
             field("signer_identity", "Số định danh / CCCD người ký", "cccd", True, autofill="profile.cccd_number", help_text=PROFILE_HELP),
             field("signer_position", "Chức danh người ký", "text", True), field("place", "Địa điểm lập", "text", True), field("signing_date", "Ngày lập", "date", True)]
        return make_schema("Thông báo về việc giải thể doanh nghiệp", "doanh_nghiep", "DN-009",
                           ["Thông báo về việc giải thể doanh nghiệp"], [section("notice", "Thông báo giải thể", [block("notice_info", "Thông tin doanh nghiệp", f)], 1)], {}, REVIEW_NOTICE)

    if slug == "thay-doi-dang-ky-ho-kinh-doanh":
        changes = [
            repeat_field("kind", "Nội dung đăng ký thay đổi", "select", True, options=[
                "Thay đổi tên hộ kinh doanh", "Thay đổi trụ sở / địa điểm kinh doanh", "Thay đổi ngành, nghề kinh doanh",
                "Thay đổi vốn kinh doanh", "Thay đổi chủ hộ", "Thay đổi thành viên hộ kinh doanh", "Cập nhật / bổ sung / hiệu đính thông tin", "Nội dung khác"]),
            repeat_field("before", "Nội dung hiện tại", "textarea", True),
            repeat_field("after", "Nội dung sau thay đổi", "textarea", True),
        ]
        f = [field("recipient", "Cơ quan đăng ký kinh doanh cấp xã", "text", True),
             field("business_name", "Tên hộ kinh doanh hiện tại", "text", True), field("business_code", "Mã số hộ kinh doanh / mã số thuế", "text", True),
             field("old_certificate_date", "Ngày cấp giấy chứng nhận hiện tại (nếu có)", "date"),
             field("owner_name", "Chủ hộ / người làm đơn", "text", True, autofill="profile.full_name", help_text=PROFILE_HELP),
             field("owner_identity", "Số định danh cá nhân / CCCD", "cccd", True, autofill="profile.cccd_number", help_text=PROFILE_HELP),
             field("owner_address", "Địa chỉ thường trú", "textarea", True, autofill="profile.permanent_address", help_text=PROFILE_HELP),
             field("place", "Địa điểm lập", "text", True), field("signing_date", "Ngày lập", "date", True)]
        sections = [section("hkd", "Hộ kinh doanh hiện tại", [block("hkd_info", "Thông tin đăng ký", f)], 1),
                    section("changes", "Các nội dung thay đổi", [block("changes", "Thêm từng nội dung thay đổi", changes, "repeat_group")], 2)]
        return make_schema("Giấy đề nghị đăng ký thay đổi nội dung đăng ký hộ kinh doanh", "ho_kinh_doanh", "HKD-002",
                           ["Giấy đề nghị đăng ký thay đổi nội dung đăng ký hộ kinh doanh"], sections, {"changes": changes}, REVIEW_NOTICE)

    if slug == "to-khai-thue-gtgt-01-gtgt":
        branches = [repeat_field("name", "Tên đơn vị phụ thuộc / địa điểm kinh doanh", "text", True),
                    repeat_field("tax_code", "Mã số thuế đơn vị / mã địa điểm", "text", True),
                    repeat_field("address", "Địa chỉ tại tỉnh khác", "textarea", True)]
        identifiers = [
            field("activity", "Hoạt động sản xuất kinh doanh", "select", True, options=["Sản xuất kinh doanh thông thường", "Xổ số kiến thiết / xổ số điện toán", "Thăm dò, khai thác dầu khí", "Dự án đầu tư hạ tầng, nhà để chuyển nhượng khác tỉnh", "Nhà máy sản xuất điện khác tỉnh"]),
            field("tax_period_type", "Kỳ khai thuế", "select", True, options=["Tháng", "Quý"]), field("tax_period", "Tháng / quý và năm", "text", True),
            field("declaration_type", "Loại tờ khai", "select", True, options=["Lần đầu", "Bổ sung"]),
            field("supplement_number", "Lần bổ sung thứ", "number", True, condition={"field": "tax_identity.declaration_type", "equals": "Bổ sung"}),
            field("taxpayer_name", "Tên người nộp thuế", "text", True), field("taxpayer_code", "Mã số thuế", "text", True),
            field("tax_agent_name", "Tên đại lý thuế (nếu có)"), field("tax_agent_code", "Mã số thuế đại lý (nếu có)"),
            field("tax_agent_contract", "Số hợp đồng đại lý thuế (nếu có)"), field("tax_agent_contract_date", "Ngày hợp đồng đại lý thuế (nếu có)", "date"),
            field("has_other_province_branch", "Có đơn vị / địa điểm kinh doanh khác tỉnh thuộc trường hợp phải kê khai chỉ tiêu này?", "select", True, options=["Không", "Có"]),
        ]
        codes = ["22", "23", "24", "23a", "24a", "25", "26", "27", "28", "29", "30", "31", "32", "33", "32a", "34", "35", "36", "37", "38", "39a", "40a", "40b", "40", "41", "42", "43"]
        tax_values = [field(f"item_{code}", f"Chỉ tiêu [{code}] - giá trị / số thuế theo tờ khai", "number") for code in codes]
        sections = [
            section("tax_identity", "Thông tin người nộp thuế và kỳ tính thuế", [block("tax_identity", "Tờ khai thuế GTGT", identifiers)], 1),
            section("branches", "Đơn vị phụ thuộc khác tỉnh (nếu có)", [block("branches", "Thêm địa điểm thuộc diện kê khai", branches, "repeat_group", {"field": "tax_identity.has_other_province_branch", "equals": "Có"})], 2),
            section("tax_amounts", "Các chỉ tiêu trên tờ khai", [block("tax_amounts", "Nhập giá trị theo từng chỉ tiêu của bản kê khai", tax_values)], 3),
            section("tax_signature", "Người nộp thuế / đại lý thuế", [block("tax_signature", "Xác nhận và ký", [
                field("no_purchase_sales", "Đánh dấu chỉ tiêu [21] nếu không phát sinh mua bán trong kỳ", "select", True, options=["Không đánh dấu", "Đánh dấu X"]),
                field("tax_agent_staff", "Nhân viên đại lý thuế (nếu có)"), field("tax_agent_certificate", "Số chứng chỉ hành nghề (nếu có)"),
                field("legal_representative", "Người nộp thuế / đại diện hợp pháp", "text", True), field("legal_representative_title", "Chức vụ (nếu có)"),
                field("place", "Địa điểm lập", "text", True), field("signing_date", "Ngày ký", "date", True),
            ])], 4),
        ]
        return make_schema("Tờ khai thuế giá trị gia tăng theo phương pháp khấu trừ (01/GTGT)", "doanh_nghiep", None,
                           ["Tờ khai thuế giá trị gia tăng theo phương pháp khấu trừ (01/GTGT)"], sections,
                           {"other_province_branches": {"fields": branches, "condition": {"field": "tax_identity.has_other_province_branch", "equals": "Có"}}},
                           REVIEW_NOTICE + " Các chỉ tiêu thuế được nhập riêng, hệ thống không tự tính và không tự kiểm tra số học.")

    if slug in {"dang-ky-cong-ty-co-phan", "dang-ky-cong-ty-hop-danh", "dang-ky-cong-ty-tnhh-mot-thanh-vien", "dang-ky-doanh-nghiep-tu-nhan"}:
        corp = slug == "dang-ky-cong-ty-co-phan"
        partnership = slug == "dang-ky-cong-ty-hop-danh"
        llc = slug == "dang-ky-cong-ty-tnhh-mot-thanh-vien"
        pvt = slug == "dang-ky-doanh-nghiep-tu-nhan"
        form_name = {
            "dang-ky-cong-ty-co-phan": "Giấy đề nghị đăng ký doanh nghiệp công ty cổ phần",
            "dang-ky-cong-ty-hop-danh": "Giấy đề nghị đăng ký doanh nghiệp công ty hợp danh",
            "dang-ky-cong-ty-tnhh-mot-thanh-vien": "Giấy đề nghị đăng ký doanh nghiệp công ty TNHH một thành viên",
            "dang-ky-doanh-nghiep-tu-nhan": "Giấy đề nghị đăng ký doanh nghiệp tư nhân",
        }[slug]
        proc = "DN-003" if corp else "DN-008" if partnership else "DN-004" if llc else "DN-005"
        industries = common_business_lines()
        party_group = "shareholders" if corp else "partners" if partnership else None
        parties = []
        if corp:
            parties = [repeat_field("kind", "Loại cổ đông", "select", True, options=["Cá nhân", "Tổ chức"]),
                       repeat_field("name", "Tên cổ đông / tổ chức", "text", True), repeat_field("birth_date", "Ngày sinh (cá nhân)", "date"),
                       repeat_field("gender", "Giới tính (cá nhân)", "select", False, options=["Nam", "Nữ", "Khác"]),
                       repeat_field("identity", "CCCD / mã số doanh nghiệp", "text", True), repeat_field("address", "Địa chỉ", "textarea", True),
                       repeat_field("representative", "Người đại diện (nếu cổ đông là tổ chức)"), repeat_field("shares", "Số cổ phần đăng ký mua", "number", True),
                       repeat_field("share_class", "Loại cổ phần", "select", True, options=["Phổ thông", "Ưu đãi biểu quyết", "Ưu đãi cổ tức", "Ưu đãi hoàn lại", "Loại khác"]),
                       repeat_field("contribution_value", "Giá trị vốn góp (đồng)", "number", True), repeat_field("contribution_deadline", "Thời hạn góp vốn"),
                       repeat_field("note", "Ghi chú / tư cách cổ đông sáng lập")]
        elif partnership:
            parties = [repeat_field("role", "Tư cách thành viên", "select", True, options=["Thành viên hợp danh", "Thành viên góp vốn"]),
                       repeat_field("kind", "Loại thành viên", "select", True, options=["Cá nhân", "Tổ chức"]),
                       repeat_field("name", "Họ tên / tên tổ chức", "text", True), repeat_field("birth_date", "Ngày sinh (cá nhân)", "date"),
                       repeat_field("identity", "CCCD / mã số doanh nghiệp", "text", True), repeat_field("address", "Địa chỉ", "textarea", True),
                       repeat_field("representative", "Người đại diện (nếu là tổ chức)"), repeat_field("capital", "Phần vốn góp (đồng)", "number", True),
                       repeat_field("capital_ratio", "Tỷ lệ vốn góp (%)", "number"), repeat_field("note", "Ghi chú")]
        applicant = applicant_fields("applicant")
        company = common_company_fields()
        company.extend([
            field("recipient", "Cơ quan đăng ký kinh doanh cấp tỉnh", "text", True),
            field("formation_type", "Tình trạng thành lập", "select", True, options=["Thành lập mới", "Hợp nhất doanh nghiệp", "Chuyển đổi loại hình doanh nghiệp", "Chuyển đổi từ hộ kinh doanh", "Trường hợp khác"]),
            field("formation_other", "Nêu trường hợp thành lập khác", "text", False, condition={"field": "company_info.formation_type", "equals": "Trường hợp khác"}),
            field("charter_capital", "Vốn điều lệ (đồng)", "number", True), field("charter_capital_words", "Vốn điều lệ bằng chữ", "text", True),
            field("legal_representative_name", "Họ tên người đại diện theo pháp luật", "text", True),
            field("legal_representative_birth_date", "Ngày sinh người đại diện", "dob", True),
            field("legal_representative_identity", "Số định danh / CCCD người đại diện", "cccd", True),
            field("legal_representative_issue_date", "Ngày cấp CCCD", "issue_date", False),
            field("legal_representative_issue_place", "Nơi cấp CCCD", "text", False),
            field("legal_representative_address", "Địa chỉ liên lạc người đại diện", "textarea", True),
            field("legal_representative_title", "Chức danh người đại diện", "text", True),
            field("authorized_representative", "Người được ủy quyền thực hiện thủ tục (nếu khác người đại diện)"),
            field("place", "Địa điểm lập", "text", True), field("signing_date", "Ngày lập", "date", True),
        ])
        if corp:
            company.extend([field("share_total", "Tổng số cổ phần đăng ký", "number", True),
                            field("share_nominal_value", "Mệnh giá một cổ phần (đồng)", "number", True),
                            field("share_currency", "Loại tài sản dùng để góp vốn", "text", True)])
        elif partnership:
            company.extend([field("partnership_name_note", "Tên giao dịch / tên viết tắt (nếu có)"),
                            field("management_authority", "Thành viên quản lý / đại diện công ty", "text", True)])
        elif llc:
            company.extend([field("owner_kind", "Loại chủ sở hữu duy nhất", "select", True, options=["Cá nhân", "Tổ chức"]),
                            field("owner_name", "Tên chủ sở hữu", "text", True), field("owner_identity", "CCCD / mã số doanh nghiệp chủ sở hữu", "text", True),
                            field("owner_address", "Địa chỉ chủ sở hữu", "textarea", True), field("owner_representative", "Người đại diện theo ủy quyền của tổ chức (nếu có)"),
                            field("owner_representative_identity", "CCCD người đại diện theo ủy quyền (nếu có)")])
        elif pvt:
            company.extend([field("owner_nationality", "Quốc tịch chủ doanh nghiệp", "text", True),
                            field("owner_tax_code", "Mã số thuế cá nhân (nếu có)"),
                            field("owner_commitment", "Cam kết chủ doanh nghiệp tư nhân chịu trách nhiệm bằng toàn bộ tài sản", "select", True, options=["Xác nhận"]),
                            field("branch_count", "Số chi nhánh / văn phòng đại diện dự kiến", "number")])

        groups = {"business_lines": industries}
        blocks = [block("applicant_info", "Người kê khai / người nộp hồ sơ", applicant), block("company_info", "Thông tin doanh nghiệp", company)]
        if party_group:
            blocks.append(block(party_group, "Thêm từng thành viên" if partnership else "Thêm từng cổ đông sáng lập", parties, "repeat_group"))
            groups[party_group] = parties
        sections = [section("registration", "Thông tin đăng ký doanh nghiệp", blocks, 1),
                    section("industries", "Ngành, nghề kinh doanh", [block("business_lines", "Thêm từng ngành nghề", industries, "repeat_group")], 2)]
        return make_schema(form_name, "doanh_nghiep", proc, [form_name], sections, groups, REVIEW_NOTICE)

    if slug == "cap-lai-giay-chung-nhan-ho-kinh-doanh":
        f = [field("household_name", "Tên hộ kinh doanh (viết bằng chữ in hoa)", "text", True),
             field("business_code", "Mã số hộ kinh doanh", "text", True),
             field("recipient", "Cơ quan đăng ký kinh doanh cấp xã", "text", True),
             field("reason", "Lý do đề nghị cấp lại", "textarea", True), field("application_number", "Số giấy đề nghị"),
             field("owner_name", "Chủ hộ kinh doanh ký đơn", "text", True, autofill="profile.full_name", help_text=PROFILE_HELP),
             field("owner_identity", "Số định danh cá nhân / CCCD", "cccd", True, autofill="profile.cccd_number", help_text=PROFILE_HELP),
             field("place", "Địa điểm lập", "text", True), field("signing_date", "Ngày lập", "date", True)]
        return make_schema("Giấy đề nghị cấp lại Giấy chứng nhận đăng ký hộ kinh doanh", "ho_kinh_doanh", None,
                           ["Giấy đề nghị cấp lại Giấy chứng nhận đăng ký hộ kinh doanh"],
                           [section("reissue", "Đề nghị cấp lại giấy chứng nhận", [block("reissue_info", "Thông tin hộ kinh doanh", f)], 1)], {}, REVIEW_NOTICE)
    raise KeyError(slug)


def make_schema(name, domain, procedure_code, references, sections, groups, notice):
    repeat_groups = []
    for key, value in groups.items():
        if isinstance(value, dict) and "fields" in value:
            item = {"key": key, "title": key.replace("_", " "), "fields": value["fields"]}
            if value.get("condition"):
                item["condition"] = value["condition"]
        else:
            item = {"key": key, "title": key.replace("_", " "), "fields": value}
        repeat_groups.append(item)
    return {"schema_version": 1, "name": name, "code": "", "procedure_code": procedure_code,
            "domain": domain, "procedure_id": None, "reference_catalog_names": references,
            "review_notice": notice, "sections": sections,
            "repeat_groups": repeat_groups}


def set_font(run, size=9.5, bold=False, italic=False):
    run.font.name = "Times New Roman"
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Times New Roman")
    run.font.size = Pt(size)
    run.bold, run.italic = bold, italic


def para(doc, text="", *, align=None, size=9.5, bold=False, italic=False, after=3):
    p = doc.add_paragraph()
    if align is not None:
        p.alignment = align
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.line_spacing = 1.0
    p.paragraph_format.keep_together = True
    if text:
        set_font(p.add_run(text), size, bold, italic)
    return p


def start_doc(title):
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.top_margin, sec.bottom_margin = Cm(1.6), Cm(1.6)
    sec.left_margin = sec.right_margin = Cm(1.8)
    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Times New Roman")
    normal.font.size = Pt(9.5)
    normal.paragraph_format.space_after = Pt(3)
    para(doc, "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", align=WD_ALIGN_PARAGRAPH.CENTER, size=11, bold=True, after=0)
    para(doc, "Độc lập - Tự do - Hạnh phúc", align=WD_ALIGN_PARAGRAPH.CENTER, size=10, bold=True, after=5)
    para(doc, title.upper(), align=WD_ALIGN_PARAGRAPH.CENTER, size=12, bold=True, after=7)
    return doc


def line(doc, label, key, *, after=3, italic=False):
    return para(doc, f"{label}: {{{{{key}}}}}", after=after, italic=italic)


def repeat_table(doc, title, headers, group, cells, *, widths=None):
    para(doc, title, bold=True, after=2)
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    if widths:
        for cell, width in zip(table.rows[0].cells, widths):
            cell.width = Cm(width)
    for cell, value in zip(table.rows[0].cells, headers):
        cell.text = value
    row = table.add_row()
    row.cells[0].text = f"{{{{#{group}}}}} {{{{{group}.index}}}}"
    for cell, value in zip(row.cells[1:], cells):
        cell.text = value
    row.cells[-1].text += f" {{{{/{group}}}}}"
    for r in table.rows:
        for c in r.cells:
            for p in c.paragraphs:
                p.paragraph_format.space_after = Pt(1)
                p.paragraph_format.line_spacing = 1.0
                for run in p.runs:
                    set_font(run, 7.5, bold=(r is table.rows[0]))
    doc.add_paragraph()
    return table


def simple_doc(slug, title):
    doc = start_doc(title)
    if slug == "hop-dong-thue-mat-bang-kinh-doanh":
        line(doc, "Số hợp đồng", "number"); line(doc, "Hôm nay, ngày ký", "signing_date"); line(doc, "Nơi ký", "signing_place")
        repeat_table(doc, "BÊN CHO THUÊ", ["STT", "Họ tên / tổ chức", "CCCD / MST", "Địa chỉ", "Điện thoại", "Đại diện"], "lessors",
                     ["{{lessors.name}} · {{lessors.birth_year}}", "{{lessors.identity}}", "{{lessors.address}}", "{{lessors.phone}}", "{{lessors.representative}}"])
        repeat_table(doc, "BÊN THUÊ", ["STT", "Họ tên / doanh nghiệp", "CCCD / MST", "Địa chỉ", "Điện thoại", "Đại diện"], "lessees",
                     ["{{lessees.name}} · {{lessees.birth_year}}", "{{lessees.identity}}", "{{lessees.address}}", "{{lessees.phone}}", "{{lessees.representative}}"])
        para(doc, "ĐIỀU 1. ĐỐI TƯỢNG HỢP ĐỒNG", bold=True)
        para(doc, "Bên A đồng ý cho Bên B thuê mặt bằng tại {{address}}, diện tích {{area}} m², hiện trạng: {{condition}}. Căn cứ quyền cho thuê: {{ownership_basis}}. Mặt bằng được sử dụng để kinh doanh: {{business_purpose}}.")
        para(doc, "ĐIỀU 2. THỜI HẠN THUÊ", bold=True)
        para(doc, "Thời hạn thuê từ {{start_date}} đến {{end_date}}. Việc gia hạn được hai bên thỏa thuận bằng văn bản.")
        para(doc, "ĐIỀU 3. GIÁ THUÊ, ĐẶT CỌC VÀ THANH TOÁN", bold=True)
        para(doc, "Giá thuê {{monthly_rent}} đồng/tháng (bằng chữ: {{rent_in_words}}). Phương thức và thời hạn thanh toán: {{payment_method}}. Tiền đặt cọc {{deposit}} đồng; điều kiện hoàn cọc: {{deposit_return}}. Điện, nước và khoản phí khác: {{utilities}}.")
        para(doc, "ĐIỀU 4. QUYỀN VÀ NGHĨA VỤ BÊN A", bold=True); para(doc, "{{lessor_obligations}}")
        para(doc, "ĐIỀU 5. QUYỀN VÀ NGHĨA VỤ BÊN B", bold=True); para(doc, "{{lessee_obligations}}")
        para(doc, "ĐIỀU 6. CHẤM DỨT HỢP ĐỒNG", bold=True); para(doc, "{{termination_terms}}")
        para(doc, "ĐIỀU 7. GIẢI QUYẾT TRANH CHẤP", bold=True); para(doc, "{{dispute_terms}}")
        para(doc, "ĐIỀU 8. ĐIỀU KHOẢN CHUNG", bold=True); para(doc, "Hợp đồng có hiệu lực kể từ ngày ký và lập thành {{copy_count}} bản có giá trị như nhau.")
        para(doc, "BÊN CHO THUÊ (BÊN A)                                        BÊN THUÊ (BÊN B)", align=WD_ALIGN_PARAGRAPH.CENTER, bold=True, after=18)
        para(doc, "(Ký, ghi rõ họ tên)                                                     (Ký, ghi rõ họ tên)", align=WD_ALIGN_PARAGRAPH.CENTER, italic=True)
        return doc

    if slug == "giay-de-nghi-dang-ky-ho-kinh-doanh":
        line(doc, "Kính gửi", "recipient")
        line(doc, "1. Tên hộ kinh doanh (viết bằng chữ in hoa)", "business_name")
        line(doc, "2. Địa điểm kinh doanh", "business_address")
        line(doc, "Điện thoại / Email", "phone"); line(doc, "Email", "email")
        repeat_table(doc, "3. NGÀNH, NGHỀ KINH DOANH", ["STT", "Tên ngành, nghề", "Mã ngành (nếu có)"], "business_lines", ["{{business_lines.name}}", "{{business_lines.code}}"])
        line(doc, "4. Vốn kinh doanh", "capital"); line(doc, "Bằng chữ", "capital_words")
        line(doc, "Số lao động dự kiến", "employees")
        para(doc, "5. THÔNG TIN CHỦ HỘ KINH DOANH", bold=True)
        line(doc, "Họ tên", "owner_name"); line(doc, "Ngày sinh / giới tính", "owner_birth_date"); line(doc, "Giới tính", "owner_gender")
        line(doc, "Số định danh cá nhân / CCCD", "owner_identity"); line(doc, "Ngày cấp / nơi cấp", "owner_issue_date"); line(doc, "Nơi cấp CCCD", "owner_issue_place"); line(doc, "Địa chỉ thường trú", "owner_address")
        para(doc, "Hộ kinh doanh cam kết chịu trách nhiệm trước pháp luật về tính hợp pháp, chính xác và trung thực của nội dung đăng ký.")
        line(doc, "Địa điểm lập", "place"); line(doc, "Ngày lập", "signing_date")
        para(doc, "CHỦ HỘ KINH DOANH", align=WD_ALIGN_PARAGRAPH.RIGHT, bold=True, after=20)
        para(doc, "(Ký và ghi rõ họ tên)", align=WD_ALIGN_PARAGRAPH.RIGHT, italic=True)
        return doc

    if slug == "thong-bao-giai-the-doanh-nghiep":
        line(doc, "Số", "notice_number"); line(doc, "Kính gửi", "recipient")
        line(doc, "Tên doanh nghiệp (viết bằng chữ in hoa)", "company_name"); line(doc, "Mã số doanh nghiệp / mã số thuế", "company_code")
        line(doc, "Số giấy chứng nhận đăng ký kinh doanh (nếu chưa có mã số)", "certificate_number")
        line(doc, "Ngày cấp / nơi cấp", "certificate_date"); line(doc, "Nơi cấp", "certificate_place")
        para(doc, "Doanh nghiệp thông báo về việc giải thể. Doanh nghiệp xác nhận {{settlement_confirmed}} đã thanh toán hết các khoản nợ, nghĩa vụ tài sản khác và không trong quá trình giải quyết tranh chấp tại Tòa án hoặc Trọng tài.")
        para(doc, "Đề nghị cơ quan đăng ký kinh doanh cập nhật tình trạng pháp lý của doanh nghiệp trong Cơ sở dữ liệu quốc gia về đăng ký doanh nghiệp sang tình trạng đã giải thể.")
        para(doc, "Doanh nghiệp cam kết chịu trách nhiệm trước pháp luật về tính hợp pháp, chính xác, trung thực của nội dung thông báo. Người ký có quyền và nghĩa vụ thực hiện thủ tục theo pháp luật và Điều lệ công ty.")
        line(doc, "Địa điểm lập", "place"); line(doc, "Ngày lập", "signing_date")
        line(doc, "Người ký xác nhận / chức danh / CCCD", "signer_position")
        para(doc, "NGƯỜI ĐẠI DIỆN THEO PHÁP LUẬT", align=WD_ALIGN_PARAGRAPH.RIGHT, bold=True, after=20)
        para(doc, "(Ký và ghi rõ họ tên)", align=WD_ALIGN_PARAGRAPH.RIGHT, italic=True)
        line(doc, "Họ tên / CCCD", "signer_name"); line(doc, "CCCD người ký", "signer_identity")
        return doc

    if slug == "thay-doi-dang-ky-ho-kinh-doanh":
        line(doc, "Kính gửi", "recipient"); line(doc, "Tên hộ kinh doanh", "business_name"); line(doc, "Mã số hộ kinh doanh / mã số thuế", "business_code"); line(doc, "Ngày cấp giấy chứng nhận hiện tại", "old_certificate_date")
        line(doc, "Chủ hộ / người làm đơn", "owner_name"); line(doc, "Số định danh / CCCD", "owner_identity"); line(doc, "Địa chỉ thường trú", "owner_address")
        repeat_table(doc, "NỘI DUNG ĐĂNG KÝ THAY ĐỔI (thêm một dòng cho mỗi nội dung)", ["STT", "Nội dung", "Thông tin hiện tại", "Thông tin sau thay đổi"], "changes",
                     ["{{changes.kind}}", "{{changes.before}}", "{{changes.after}}"])
        para(doc, "Hộ kinh doanh cam kết các nội dung kê khai là đúng sự thật và chịu trách nhiệm trước pháp luật.")
        line(doc, "Địa điểm lập", "place"); line(doc, "Ngày lập", "signing_date"); para(doc, "CHỦ HỘ KINH DOANH", align=WD_ALIGN_PARAGRAPH.RIGHT, bold=True, after=20)
        para(doc, "(Ký và ghi rõ họ tên)", align=WD_ALIGN_PARAGRAPH.RIGHT, italic=True)
        return doc

    if slug == "to-khai-thue-gtgt-01-gtgt":
        line(doc, "[01a] Hoạt động sản xuất kinh doanh", "activity"); line(doc, "[01b] Hình thức kỳ tính thuế", "tax_period_type"); line(doc, "Tháng / quý và năm", "tax_period")
        line(doc, "[02] Lần đầu / [03] Bổ sung lần thứ", "declaration_type"); line(doc, "[03] Số lần bổ sung", "supplement_number"); line(doc, "[04] Tên người nộp thuế", "taxpayer_name"); line(doc, "[05] Mã số thuế", "taxpayer_code")
        line(doc, "[06] Tên đại lý thuế", "tax_agent_name"); line(doc, "[07] Mã số thuế đại lý thuế", "tax_agent_code")
        line(doc, "[08] Số hợp đồng đại lý thuế", "tax_agent_contract"); line(doc, "Ngày hợp đồng đại lý thuế", "tax_agent_contract_date")
        line(doc, "Có đơn vị / địa điểm khác tỉnh phải kê khai?", "has_other_province_branch")
        para(doc, "[09] - [11] Đơn vị phụ thuộc / địa điểm khác tỉnh (nếu thuộc trường hợp phải khai)", bold=True)
        repeat_table(doc, "THÔNG TIN ĐƠN VỊ KHÁC TỈNH", ["STT", "Tên đơn vị", "Mã số thuế / mã địa điểm", "Địa chỉ"], "other_province_branches",
                     ["{{other_province_branches.name}}", "{{other_province_branches.tax_code}}", "{{other_province_branches.address}}"])
        para(doc, "CÁC CHỈ TIÊU KÊ KHAI (đơn vị tiền: đồng Việt Nam)", bold=True)
        codes = ["22", "23", "24", "23a", "24a", "25", "26", "27", "28", "29", "30", "31", "32", "33", "32a", "34", "35", "36", "37", "38", "39a", "40a", "40b", "40", "41", "42", "43"]
        table = doc.add_table(rows=1, cols=3); table.style = "Table Grid"
        for cell, text in zip(table.rows[0].cells, ["Chỉ tiêu", "Nội dung", "Giá trị / số thuế"]): cell.text = text
        labels = {"22":"Thuế GTGT còn được khấu trừ kỳ trước chuyển sang", "23":"Hàng hóa, dịch vụ mua vào", "24":"Thuế GTGT hàng hóa mua vào", "23a":"Hàng hóa nhập khẩu", "24a":"Thuế GTGT hàng hóa nhập khẩu", "25":"Thuế GTGT được khấu trừ kỳ này", "26":"Hàng hóa bán ra không chịu thuế", "27":"Hàng hóa bán ra chịu thuế GTGT", "28":"Thuế GTGT đầu ra", "29":"Hàng hóa bán ra thuế suất 0%", "30":"Hàng hóa bán ra thuế suất 5%", "31":"Thuế GTGT bán ra thuế suất 5%", "32":"Hàng hóa bán ra thuế suất 10%", "33":"Thuế GTGT bán ra thuế suất 10%", "32a":"Hàng hóa bán ra không tính thuế", "34":"Tổng doanh thu bán ra", "35":"Tổng thuế GTGT bán ra", "36":"Thuế GTGT phát sinh", "37":"Điều chỉnh giảm", "38":"Điều chỉnh tăng", "39a":"Thuế GTGT nhận bàn giao được khấu trừ", "40a":"Thuế GTGT phải nộp", "40b":"Thuế GTGT đầu tư được bù trừ", "40":"Thuế GTGT còn phải nộp", "41":"Thuế GTGT chưa khấu trừ hết", "42":"Thuế GTGT đề nghị hoàn", "43":"Thuế GTGT chuyển kỳ sau"}
        for code in codes:
            row = table.add_row().cells
            row[0].text, row[1].text, row[2].text = f"[{code}]", labels[code], f"{{{{item_{code}}}}}"
            for cell in row:
                for p in cell.paragraphs:
                    for run in p.runs: set_font(run, 8)
        para(doc, "Người nộp thuế cam đoan số liệu khai trên là đúng và chịu trách nhiệm trước pháp luật về số liệu đã khai.", after=8)
        line(doc, "[21] Không phát sinh mua bán trong kỳ - đánh dấu X", "no_purchase_sales")
        line(doc, "Địa điểm lập", "place"); line(doc, "Ngày ký", "signing_date")
        para(doc, "NGƯỜI NỘP THUẾ / ĐẠI DIỆN HỢP PHÁP", align=WD_ALIGN_PARAGRAPH.RIGHT, bold=True, after=16)
        line(doc, "Họ tên / chức vụ", "legal_representative"); line(doc, "Chức vụ", "legal_representative_title")
        line(doc, "Nhân viên đại lý thuế / chứng chỉ (nếu có)", "tax_agent_staff"); line(doc, "Số chứng chỉ hành nghề", "tax_agent_certificate")
        return doc

    if slug in {"dang-ky-cong-ty-co-phan", "dang-ky-cong-ty-hop-danh", "dang-ky-cong-ty-tnhh-mot-thanh-vien", "dang-ky-doanh-nghiep-tu-nhan"}:
        line(doc, "Kính gửi cơ quan đăng ký kinh doanh cấp tỉnh", "recipient")
        line(doc, "Người kê khai", "applicant_name"); line(doc, "Ngày sinh / số định danh", "applicant_birth_date")
        line(doc, "Số định danh cá nhân / CCCD", "applicant_identity")
        line(doc, "Ngày cấp giấy tờ", "applicant_issue_date"); line(doc, "Nơi cấp giấy tờ", "applicant_issue_place")
        line(doc, "Địa chỉ liên lạc", "applicant_address"); line(doc, "Điện thoại / Email", "applicant_phone"); line(doc, "Email", "applicant_email")
        para(doc, "Đăng ký doanh nghiệp với nội dung sau", bold=True)
        line(doc, "Tình trạng thành lập", "formation_type"); line(doc, "Trường hợp thành lập khác", "formation_other"); line(doc, "Tên doanh nghiệp bằng tiếng Việt", "company_name_vn")
        line(doc, "Tên bằng tiếng nước ngoài / tên viết tắt", "company_name_foreign"); line(doc, "Tên viết tắt", "company_short_name")
        para(doc, "ĐỊA CHỈ TRỤ SỞ CHÍNH", bold=True)
        line(doc, "Số nhà, đường", "address_detail"); line(doc, "Xã / phường / đặc khu", "ward"); line(doc, "Tỉnh / thành phố", "province")
        line(doc, "Điện thoại / Fax / Email / Website", "phone"); line(doc, "Fax", "fax"); line(doc, "Email", "email"); line(doc, "Website", "website")
        line(doc, "Khu công nghiệp / khu kinh tế (nếu có)", "headquarters_in_zone")
        line(doc, "Doanh nghiệp xã hội", "social_enterprise")
        para(doc, "NGÀNH, NGHỀ KINH DOANH", bold=True)
        repeat_table(doc, "Danh sách ngành, nghề", ["STT", "Tên ngành, nghề", "Mã ngành", "Ngành chính"], "business_lines",
                     ["{{business_lines.name}}", "{{business_lines.code}}", "{{business_lines.main}}"])
        if slug == "dang-ky-cong-ty-co-phan":
            line(doc, "Vốn điều lệ / bằng chữ", "charter_capital"); line(doc, "Vốn điều lệ bằng chữ", "charter_capital_words")
            line(doc, "Tổng số cổ phần / mệnh giá", "share_total"); line(doc, "Mệnh giá mỗi cổ phần", "share_nominal_value"); line(doc, "Loại tài sản góp vốn", "share_currency")
            repeat_table(doc, "CỔ ĐÔNG SÁNG LẬP", ["STT", "Thông tin cổ đông: loại, tên, ngày sinh, giới tính, CCCD / mã số, địa chỉ", "Cổ phần · loại", "Giá trị góp vốn · hạn góp", "Đại diện · ghi chú"], "shareholders",
                         ["{{shareholders.kind}} · {{shareholders.name}} · {{shareholders.birth_date}} · {{shareholders.gender}} · {{shareholders.identity}} · {{shareholders.address}}", "{{shareholders.shares}} · {{shareholders.share_class}}", "{{shareholders.contribution_value}} · {{shareholders.contribution_deadline}}", "{{shareholders.representative}} · {{shareholders.note}}"])
        elif slug == "dang-ky-cong-ty-hop-danh":
            line(doc, "Vốn điều lệ / bằng chữ", "charter_capital"); line(doc, "Vốn điều lệ bằng chữ", "charter_capital_words")
            line(doc, "Tên giao dịch / tên viết tắt", "partnership_name_note"); line(doc, "Thành viên quản lý / đại diện", "management_authority")
            repeat_table(doc, "THÀNH VIÊN CÔNG TY HỢP DANH", ["STT", "Tư cách · loại · tên · ngày sinh · CCCD / mã số · địa chỉ", "Vốn góp", "Tỷ lệ", "Đại diện · ghi chú"], "partners",
                         ["{{partners.role}} · {{partners.kind}} · {{partners.name}} · {{partners.birth_date}} · {{partners.identity}} · {{partners.address}}", "{{partners.capital}}", "{{partners.capital_ratio}}%", "{{partners.representative}} · {{partners.note}}"])
        elif slug == "dang-ky-cong-ty-tnhh-mot-thanh-vien":
            line(doc, "Loại chủ sở hữu", "owner_kind"); line(doc, "Chủ sở hữu cá nhân / tổ chức", "owner_name"); line(doc, "CCCD / mã số doanh nghiệp", "owner_identity")
            line(doc, "Địa chỉ chủ sở hữu / người đại diện theo ủy quyền", "owner_address"); line(doc, "Vốn điều lệ / bằng chữ", "charter_capital")
            line(doc, "Người đại diện theo ủy quyền của tổ chức", "owner_representative"); line(doc, "CCCD người đại diện theo ủy quyền", "owner_representative_identity"); line(doc, "Vốn điều lệ bằng chữ", "charter_capital_words")
        else:
            line(doc, "Chủ doanh nghiệp tư nhân", "legal_representative_name"); line(doc, "Quốc tịch / mã số thuế cá nhân", "owner_nationality"); line(doc, "Mã số thuế cá nhân", "owner_tax_code")
            line(doc, "Vốn đầu tư ban đầu / bằng chữ", "charter_capital"); line(doc, "Vốn đầu tư bằng chữ", "charter_capital_words"); line(doc, "Số chi nhánh / văn phòng đại diện dự kiến", "branch_count")
            line(doc, "Cam kết trách nhiệm bằng toàn bộ tài sản", "owner_commitment")
        para(doc, "THÔNG TIN ĐĂNG KÝ THUẾ", bold=True)
        line(doc, "Số điện thoại đăng ký thuế", "company_phone_for_tax")
        line(doc, "Năm tài chính / chế độ kế toán / kỳ kê khai GTGT", "accounting_year"); line(doc, "Chế độ kế toán", "accounting_method"); line(doc, "Chế độ kế toán khác", "accounting_method_other"); line(doc, "Kỳ kê khai thuế GTGT", "tax_period")
        para(doc, "NGƯỜI ĐẠI DIỆN THEO PHÁP LUẬT", bold=True)
        line(doc, "Họ tên / ngày sinh / CCCD", "legal_representative_name"); line(doc, "Ngày sinh", "legal_representative_birth_date"); line(doc, "CCCD", "legal_representative_identity")
        line(doc, "Ngày cấp / nơi cấp CCCD", "legal_representative_issue_date"); line(doc, "Nơi cấp CCCD", "legal_representative_issue_place")
        line(doc, "Địa chỉ / chức danh", "legal_representative_address"); line(doc, "Chức danh", "legal_representative_title")
        line(doc, "Người được ủy quyền nộp hồ sơ (nếu có)", "authorized_representative")
        line(doc, "Địa điểm lập", "place"); line(doc, "Ngày lập", "signing_date"); line(doc, "Khu kinh tế / doanh nghiệp xã hội", "headquarters_in_zone")
        para(doc, "Người ký cam kết thông tin kê khai là đúng sự thật và chịu trách nhiệm trước pháp luật.")
        para(doc, "NGƯỜI ĐỀ NGHỊ / NGƯỜI ĐẠI DIỆN THEO PHÁP LUẬT", align=WD_ALIGN_PARAGRAPH.RIGHT, bold=True, after=18)
        para(doc, "(Ký, ghi rõ họ tên)", align=WD_ALIGN_PARAGRAPH.RIGHT, italic=True)
        return doc

    if slug == "cap-lai-giay-chung-nhan-ho-kinh-doanh":
        line(doc, "Số", "application_number"); line(doc, "Kính gửi", "recipient")
        line(doc, "Tên hộ kinh doanh (viết bằng chữ in hoa)", "household_name"); line(doc, "Mã số hộ kinh doanh", "business_code")
        para(doc, "Đề nghị được cấp lại Giấy chứng nhận đăng ký hộ kinh doanh.")
        line(doc, "Lý do đề nghị cấp lại", "reason")
        para(doc, "Hộ kinh doanh cam kết chịu trách nhiệm trước pháp luật về tính hợp pháp, chính xác và trung thực của nội dung giấy đề nghị.")
        line(doc, "Địa điểm, ngày lập", "place"); line(doc, "Ngày lập", "signing_date")
        para(doc, "CHỦ HỘ KINH DOANH", align=WD_ALIGN_PARAGRAPH.RIGHT, bold=True, after=18)
        para(doc, "(Ký trực tiếp và ghi rõ họ tên)", align=WD_ALIGN_PARAGRAPH.RIGHT, italic=True)
        line(doc, "Họ tên / CCCD chủ hộ", "owner_name"); line(doc, "Số CCCD", "owner_identity")
        return doc
    raise KeyError(slug)


BUILDERS = [
    ("hop-dong-thue-mat-bang-kinh-doanh", "hop-dong-thue-mat-bang-kinh-doanh", "Hợp đồng cho thuê mặt bằng kinh doanh", "doanh_nghiep", "DN-006", "Hợp đồng cho thuê mặt bằng kinh doanh"),
    ("giay-de-nghi-dang-ky-ho-kinh-doanh", "giay-de-nghi-dang-ky-ho-kinh-doanh", "Giấy đề nghị đăng ký hộ kinh doanh", "ho_kinh_doanh", "HKD-001", "Giấy đề nghị đăng ký hộ kinh doanh"),
    ("thong-bao-giai-the-doanh-nghiep", "thong-bao-giai-the-doanh-nghiep", "Thông báo về việc giải thể doanh nghiệp", "doanh_nghiep", "DN-009", "Thông báo về việc giải thể doanh nghiệp"),
    ("thay-doi-dang-ky-ho-kinh-doanh", "giay-de-nghi-thay-doi-dang-ky-ho-kinh-doanh", "Giấy đề nghị đăng ký thay đổi nội dung đăng ký hộ kinh doanh", "ho_kinh_doanh", "HKD-002", "Giấy đề nghị đăng ký thay đổi nội dung đăng ký hộ kinh doanh"),
    ("to-khai-thue-gtgt-01-gtgt", "to-khai-thue-gtgt-01-gtgt", "Tờ khai thuế giá trị gia tăng theo phương pháp khấu trừ (01/GTGT)", "doanh_nghiep", None, "Tờ khai thuế giá trị gia tăng theo phương pháp khấu trừ (01/GTGT)"),
    ("dang-ky-cong-ty-co-phan", "giay-de-nghi-dang-ky-cong-ty-co-phan", "Giấy đề nghị đăng ký doanh nghiệp công ty cổ phần", "doanh_nghiep", "DN-003", "Giấy đề nghị đăng ký doanh nghiệp công ty cổ phần"),
    ("dang-ky-cong-ty-hop-danh", "giay-de-nghi-dang-ky-cong-ty-hop-danh", "Giấy đề nghị đăng ký doanh nghiệp công ty hợp danh", "doanh_nghiep", "DN-008", "Giấy đề nghị đăng ký doanh nghiệp công ty hợp danh"),
    ("dang-ky-cong-ty-tnhh-mot-thanh-vien", "giay-de-nghi-dang-ky-cong-ty-tnhh-mot-thanh-vien", "Giấy đề nghị đăng ký doanh nghiệp công ty TNHH một thành viên", "doanh_nghiep", "DN-004", "Giấy đề nghị đăng ký doanh nghiệp công ty TNHH một thành viên"),
    ("dang-ky-doanh-nghiep-tu-nhan", "giay-de-nghi-dang-ky-doanh-nghiep-tu-nhan", "Giấy đề nghị đăng ký doanh nghiệp tư nhân", "doanh_nghiep", "DN-005", "Giấy đề nghị đăng ký doanh nghiệp tư nhân"),
    ("cap-lai-giay-chung-nhan-ho-kinh-doanh", "giay-de-nghi-cap-lai-giay-chung-nhan-ho-kinh-doanh", "Giấy đề nghị cấp lại Giấy chứng nhận đăng ký hộ kinh doanh", "ho_kinh_doanh", None, "Giấy đề nghị cấp lại Giấy chứng nhận đăng ký hộ kinh doanh"),
]


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for slug, filename, title, *_ in BUILDERS:
        content = business_schema(slug)
        (OUTPUT / f"{filename}.json").write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        simple_doc(slug, title).save(OUTPUT / f"{filename}.docx")
        print(filename)


if __name__ == "__main__":
    main()
