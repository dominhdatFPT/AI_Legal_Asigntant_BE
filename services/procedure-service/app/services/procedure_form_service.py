import json
import logging
import re
from datetime import date
from typing import Any, Callable, Dict, List, Optional

from app.schemas.form_schema import (
    FieldType,
    FormField,
    FormFieldOption,
    FormSection,
    ProcedureFormSchema,
)
from app.services.submission_recipient import is_submission_recipient

logger = logging.getLogger(__name__)

# ==============================================================================
# REUSABLE BLOCKS (CÁC KHỐI TRƯỜNG DÙNG CHUNG)
# ==============================================================================

def get_applicant_block(prefix: str = "applicant", section: str = "applicant") -> List[FormField]:
    """Khối thông tin cá nhân/người làm đơn"""
    return [
        FormField(
            name=f"{prefix}_name",
            label="Họ và tên người làm đơn",
            field_type=FieldType.text,
            required=True,
            placeholder="NGUYỄN VĂN A",
            section=section,
            description="Ghi rõ họ và tên bằng chữ in hoa có dấu",
        ),
        FormField(
            name=f"{prefix}_gender",
            label="Giới tính",
            field_type=FieldType.select,
            required=True,
            default_value="Nam",
            options=[
                FormFieldOption(value="Nam", label="Nam"),
                FormFieldOption(value="Nữ", label="Nữ"),
                FormFieldOption(value="Khác", label="Khác"),
            ],
            section=section,
        ),
        FormField(
            name=f"{prefix}_dob",
            label="Ngày tháng năm sinh",
            field_type=FieldType.date,
            required=True,
            section=section,
        ),
        FormField(
            name=f"{prefix}_id_type",
            label="Loại giấy tờ định danh",
            field_type=FieldType.select,
            required=True,
            default_value="CCCD",
            options=[
                FormFieldOption(value="CCCD", label="Căn cước công dân"),
                FormFieldOption(value="CMND", label="Chứng minh nhân dân"),
                FormFieldOption(value="HoChieu", label="Hộ chiếu"),
            ],
            section=section,
        ),
        FormField(
            name=f"{prefix}_id_number",
            label="Số CCCD/Định danh cá nhân",
            field_type=FieldType.text,
            required=True,
            placeholder="001095012345",
            section=section,
            validation_regex="^[0-9]{9,12}$",
        ),
        FormField(
            name=f"{prefix}_id_issue_date",
            label="Ngày cấp CCCD",
            field_type=FieldType.date,
            required=True,
            section=section,
        ),
        FormField(
            name=f"{prefix}_id_issue_place",
            label="Nơi cấp",
            field_type=FieldType.text,
            required=True,
            default_value="Bộ Công an",
            section=section,
        ),
        FormField(
            name=f"{prefix}_phone",
            label="Số điện thoại liên hệ",
            field_type=FieldType.text,
            required=True,
            placeholder="0912345678",
            section=section,
            validation_regex="^[0-9]{10,11}$",
        ),
        FormField(
            name=f"{prefix}_email",
            label="Địa chỉ email",
            field_type=FieldType.text,
            required=False,
            placeholder="example@gmail.com",
            section=section,
        ),
        FormField(
            name=f"{prefix}_permanent_address",
            label="Nơi đăng ký thường trú",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Số nhà, đường/thôn, xã/phường, quận/huyện, tỉnh/thành phố",
            section=section,
        ),
        FormField(
            name=f"{prefix}_current_address",
            label="Nơi ở hiện nay",
            field_type=FieldType.textarea,
            required=False,
            placeholder="Ghi cụ thể nơi cư trú hiện nay nếu khác hộ khẩu thường trú",
            section=section,
        ),
    ]


def get_land_plot_block(section: str = "property") -> List[FormField]:
    """Khối thông tin thửa đất"""
    return [
        FormField(
            name="plot_number",
            label="Thửa đất số",
            field_type=FieldType.text,
            required=True,
            placeholder="123",
            section=section,
            description="Số hiệu thửa đất ghi trên Giấy chứng nhận hoặc mảnh trích đo",
        ),
        FormField(
            name="map_sheet_number",
            label="Tờ bản đồ số",
            field_type=FieldType.text,
            required=True,
            placeholder="45",
            section=section,
            description="Số tờ bản đồ địa chính",
        ),
        FormField(
            name="land_address",
            label="Địa chỉ thửa đất",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Thôn/xóm/số nhà, xã/phường, huyện/quận, tỉnh/thành phố",
            section=section,
        ),
        FormField(
            name="land_area",
            label="Diện tích thửa đất (m²)",
            field_type=FieldType.number,
            required=True,
            placeholder="150.5",
            section=section,
        ),
        FormField(
            name="land_use_purpose",
            label="Mục đích sử dụng đất",
            field_type=FieldType.select,
            required=True,
            default_value="Đất ở tại đô thị (ODT)",
            options=[
                FormFieldOption(value="Đất ở tại nông thôn (ONT)", label="Đất ở tại nông thôn (ONT)"),
                FormFieldOption(value="Đất ở tại đô thị (ODT)", label="Đất ở tại đô thị (ODT)"),
                FormFieldOption(value="Đất trồng cây lâu năm (CLN)", label="Đất trồng cây lâu năm (CLN)"),
                FormFieldOption(value="Đất trồng cây hàng năm khác (HNK)", label="Đất trồng cây hàng năm khác (HNK)"),
                FormFieldOption(value="Đất trồng lúa (LUC)", label="Đất trồng lúa (LUC)"),
                FormFieldOption(value="Đất thương mại, dịch vụ (TMD)", label="Đất thương mại, dịch vụ (TMD)"),
                FormFieldOption(value="Đất sản xuất phi nông nghiệp (SKC)", label="Đất sản xuất phi nông nghiệp (SKC)"),
            ],
            section=section,
        ),
        FormField(
            name="land_use_term",
            label="Thời hạn sử dụng đất",
            field_type=FieldType.text,
            required=False,
            default_value="Lâu dài",
            placeholder="Lâu dài hoặc ghi rõ đến ngày/tháng/năm",
            section=section,
        ),
        FormField(
            name="land_use_origin",
            label="Nguồn gốc sử dụng đất",
            field_type=FieldType.text,
            required=False,
            placeholder="Công nhận QSDĐ như giao đất có thu tiền sử dụng đất...",
            section=section,
        ),
        FormField(
            name="certificate_number",
            label="Số seri Giấy chứng nhận (Số phát hành)",
            field_type=FieldType.text,
            required=False,
            placeholder="DA 123456",
            section=section,
            description="Số seri in đỏ/đen trên góc Giấy chứng nhận đã cấp",
        ),
        FormField(
            name="certificate_book_number",
            label="Số vào sổ cấp GCN",
            field_type=FieldType.text,
            required=False,
            placeholder="CS 01234",
            section=section,
        ),
        FormField(
            name="certificate_issue_date",
            label="Ngày cấp Giấy chứng nhận",
            field_type=FieldType.date,
            required=False,
            section=section,
        ),
        FormField(
            name="certificate_issue_agency",
            label="Cơ quan cấp GCN",
            field_type=FieldType.text,
            required=False,
            placeholder="Sở Tài nguyên và Môi trường tỉnh... hoặc UBND huyện...",
            section=section,
        ),
    ]


def get_business_info_block(section: str = "business") -> List[FormField]:
    """Khối thông tin doanh nghiệp / hộ kinh doanh"""
    return [
        FormField(
            name="business_name_vn",
            label="Tên tiếng Việt",
            field_type=FieldType.text,
            required=True,
            placeholder="CÔNG TY CỔ PHẦN ĐẦU TƯ ABC",
            section=section,
            description="Viết hoa toàn bộ tên doanh nghiệp/hộ kinh doanh",
        ),
        FormField(
            name="business_name_en",
            label="Tên tiếng nước ngoài (nếu có)",
            field_type=FieldType.text,
            required=False,
            placeholder="ABC INVESTMENT JOINT STOCK COMPANY",
            section=section,
        ),
        FormField(
            name="business_name_abbr",
            label="Tên viết tắt (nếu có)",
            field_type=FieldType.text,
            required=False,
            placeholder="ABC CORP",
            section=section,
        ),
        FormField(
            name="headquarters_address",
            label="Địa chỉ trụ sở chính",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Số nhà, đường/phố, phường/xã, quận/huyện, tỉnh/thành phố",
            section=section,
        ),
        FormField(
            name="headquarters_phone",
            label="Điện thoại trụ sở",
            field_type=FieldType.text,
            required=True,
            placeholder="02838123456",
            section=section,
        ),
        FormField(
            name="headquarters_email",
            label="Email trụ sở",
            field_type=FieldType.text,
            required=False,
            placeholder="contact@company.vn",
            section=section,
        ),
        FormField(
            name="charter_capital",
            label="Vốn điều lệ / Vốn kinh doanh (VNĐ)",
            field_type=FieldType.number,
            required=True,
            placeholder="1000000000",
            section=section,
        ),
        FormField(
            name="charter_capital_in_words",
            label="Vốn điều lệ bằng chữ",
            field_type=FieldType.text,
            required=True,
            placeholder="Một tỷ đồng",
            section=section,
        ),
        FormField(
            name="main_business_lines",
            label="Ngành nghề kinh doanh chính",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Hoạt động tư vấn quản lý (Mã ngành: 7020); Bán buôn tổng hợp (Mã ngành: 4690)...",
            section=section,
            description="Ghi rõ tên ngành và mã ngành cấp 4 theo Hệ thống ngành kinh tế Việt Nam",
        ),
        FormField(
            name="total_employees",
            label="Quy mô số lao động dự kiến",
            field_type=FieldType.number,
            required=False,
            default_value=5,
            section=section,
        ),
    ]


# ==============================================================================
# 12 HÀM CHI TIẾT LĨNH VỰC ĐẤT ĐAI (LAND PROCEDURES)
# ==============================================================================

def get_fields_mau_so_18(change_hint: Optional[str] = None) -> List[FormField]:
    """
    Biểu mẫu chuẩn quốc gia: Mẫu số 18 - Đơn đăng ký biến động đất đai, tài sản gắn liền với đất
    (Ban hành kèm theo Nghị định số 151/2025/NĐ-CP của Chính phủ).
    Áp dụng thống nhất cho các thủ tục đăng ký biến động quyền sử dụng đất.
    """
    default_change_hint = (
        "Ghi rõ nội dung biến động (nhận chuyển nhượng, tặng cho, thừa kế, cấp đổi, cấp lại...).\n"
        "- Thông tin thửa đất: Số thửa, tờ bản đồ số, địa chỉ nơi có đất, diện tích (m²).\n"
        "- Thông tin Giấy chứng nhận: Số phát hành (seri), số vào sổ cấp Giấy chứng nhận.\n"
        "- Hợp đồng / Văn bản giao dịch: Số hợp đồng, ngày lập, tổ chức hành nghề công chứng.\n"
        "- Nhu cầu cấp Giấy chứng nhận: Ghi rõ 'Có nhu cầu cấp mới Giấy chứng nhận' hoặc 'Xác nhận thay đổi vào Giấy chứng nhận đã cấp'."
    )
    return [
        FormField(
            name="receiving_agency",
            label="Kính gửi (Cơ quan tiếp nhận giải quyết)",
            field_type=FieldType.text,
            required=True,
            default_value="Chi nhánh Văn phòng Đăng ký Đất đai",
            placeholder="Chi nhánh Văn phòng Đăng ký Đất đai nơi có đất / Bộ phận Một cửa",
            section="receiving",
            description="Đối với hộ gia đình, cá nhân: ghi Văn phòng đăng ký đất đai / Chi nhánh Văn phòng đăng ký đất đai nơi có đất.",
        ),
        FormField(
            name="applicant_name",
            label="Họ và tên cá nhân / Tên tổ chức (chữ in hoa)",
            field_type=FieldType.text,
            required=True,
            placeholder="NGUYỄN VĂN AN",
            section="applicant",
            description="Cá nhân: Ghi họ và tên bằng chữ in hoa có dấu; Trường hợp nhận chuyển quyền thì ghi thông tin của bên nhận chuyển quyền.",
        ),
        FormField(
            name="applicant_id_document",
            label="Giấy tờ nhân thân / pháp nhân (Số CCCD, ngày cấp, nơi cấp)",
            field_type=FieldType.text,
            required=True,
            placeholder="CCCD số 079090001234, cấp ngày 15/04/2021 tại Cục CSQLHC về TTXH",
            section="applicant",
            description="Cá nhân ghi số định danh cá nhân / CCCD / Hộ chiếu; Tổ chức ghi theo quyết định thành lập hoặc GCN ĐKKD.",
        ),
        FormField(
            name="applicant_address",
            label="Địa chỉ nơi đăng ký thường trú / Trụ sở chính",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Số 123 đường Nguyễn Huệ, Phường Bến Nghé, Quận 1, TP. Hồ Chí Minh",
            section="applicant",
            description="Cá nhân ghi địa chỉ nơi đăng ký thường trú; Tổ chức ghi địa chỉ trụ sở chính theo ĐKKD.",
        ),
        FormField(
            name="applicant_tax_code",
            label="Mã số thuế (nếu có)",
            field_type=FieldType.text,
            required=False,
            placeholder="0312345678",
            section="applicant",
        ),
        FormField(
            name="applicant_phone",
            label="Điện thoại liên hệ",
            field_type=FieldType.text,
            required=True,
            placeholder="0912345678",
            section="applicant",
            validation_regex="^[0-9]{10,11}$",
        ),
        FormField(
            name="applicant_email",
            label="Hộp thư điện tử (nếu có)",
            field_type=FieldType.text,
            required=False,
            placeholder="example@gmail.com",
            section="applicant",
        ),
        FormField(
            name="change_content",
            label="Nội dung biến động đề nghị đăng ký",
            field_type=FieldType.textarea,
            required=True,
            placeholder=change_hint or default_change_hint,
            section="change",
            description="Nội dung biến động chi tiết: nhận chuyển quyền, thông tin thửa đất, số GCN, hợp đồng giao dịch, nhu cầu cấp đổi/cấp mới GCN.",
        ),
        FormField(
            name="tax_exemption_info",
            label="Thông tin đối tượng được miễn, giảm nghĩa vụ tài chính về đất đai (nếu có)",
            field_type=FieldType.textarea,
            required=False,
            default_value="Không thuộc đối tượng miễn, giảm nghĩa vụ tài chính.",
            placeholder="Ghi rõ trường hợp thuộc diện được miễn, giảm (ví dụ: chuyển nhượng/tặng cho giữa cha mẹ và con...), hoặc Không có.",
            section="tax_exemption",
            description="Kê khai diện miễn giảm theo quy định pháp luật và nộp kèm giấy tờ chứng minh.",
        ),
        FormField(
            name="attached_documents",
            label="Giấy tờ liên quan đến nội dung biến động nộp kèm theo đơn",
            field_type=FieldType.textarea,
            required=True,
            default_value="1. Bản gốc Giấy chứng nhận quyền sử dụng đất đã cấp;\n2. Hợp đồng chuyển nhượng/tặng cho quyền sử dụng đất (bản chính công chứng);\n3. Bản sao Căn cước công dân của các bên;\n4. Tờ khai thuế thu nhập cá nhân và lệ phí trước bạ.",
            placeholder="Liệt kê danh mục các giấy tờ nộp kèm theo Đơn này.",
            section="attachment",
            description="Ghi các loại giấy tờ nộp kèm: Bản gốc GCN, hợp đồng giao dịch, giấy tờ nhân thân, hồ sơ thuế...",
        ),
        FormField(
            name="declarer_signature_name",
            label="Họ và tên người viết đơn (Người làm đơn ký và ghi rõ họ tên)",
            field_type=FieldType.text,
            required=True,
            placeholder="NGUYỄN VĂN AN",
            section="declaration",
            description="Người viết đơn ký, ghi rõ họ tên (chữ in hoa) và đóng dấu nếu là tổ chức.",
        ),
    ]


def get_fields_dd_001() -> List[FormField]:
    """DD-001: Đăng ký biến động quyền sử dụng đất do chuyển nhượng (Mẫu số 18 - Nghị định 151/2025/NĐ-CP)"""
    hint = (
        "Nhận chuyển nhượng quyền sử dụng đất theo Hợp đồng chuyển nhượng số ... lập ngày ... tại Văn phòng Công chứng ...\n"
        "- Thửa đất số: ..., Tờ bản đồ số: ...\n"
        "- Địa chỉ thửa đất: ...\n"
        "- Diện tích: ... m²\n"
        "- Giấy chứng nhận số phát hành: ..., Số vào sổ cấp GCN: ... do UBND/Sở TNMT cấp ngày ...\n"
        "- Nhu cầu cấp Giấy chứng nhận: Có nhu cầu cấp mới Giấy chứng nhận quyền sử dụng đất (hoặc Xác nhận thay đổi vào Giấy chứng nhận đã cấp)."
    )
    return get_fields_mau_so_18(change_hint=hint)


def get_fields_dd_002() -> List[FormField]:
    """DD-002: Cấp đổi Giấy chứng nhận quyền sử dụng đất"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend(get_land_plot_block(section="property"))
    fields.extend([
        FormField(
            name="reissue_reason",
            label="Lý do đề nghị cấp đổi",
            field_type=FieldType.select,
            required=True,
            default_value="change_new_sample",
            options=[
                FormFieldOption(value="change_new_sample", label="Có nhu cầu đổi sang mẫu Giấy chứng nhận mới theo Luật 2024"),
                FormFieldOption(value="torn_damaged", label="Giấy chứng nhận bị ố, nhòe, rách, hư hỏng"),
                FormFieldOption(value="survey_area_change", label="Do đo đạc lại xác định lại kích thước, diện tích thửa đất"),
                FormFieldOption(value="full_endorse_page", label="Trang 4 xác nhận thay đổi đã kín dòng"),
            ],
            section="request",
        ),
        FormField(
            name="survey_request_reason",
            label="Thông tin bản vẽ đo đạc lại (nếu có)",
            field_type=FieldType.textarea,
            required=False,
            placeholder="Mảnh trích đo địa chính số... do Công ty đo đạc lập ngày...",
            section="request",
        ),
    ])
    return fields


def get_fields_dd_003() -> List[FormField]:
    """DD-003: Tách thửa hoặc hợp thửa đất"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend(get_land_plot_block(section="property"))
    fields.extend([
        FormField(
            name="split_merge_type",
            label="Hình thức yêu cầu",
            field_type=FieldType.select,
            required=True,
            default_value="split",
            options=[
                FormFieldOption(value="split", label="Tách thửa đất thành các thửa nhỏ"),
                FormFieldOption(value="merge", label="Hợp các thửa đất liền kề thành một thửa"),
            ],
            section="request",
        ),
        FormField(
            name="split_plots_count",
            label="Số lượng thửa đất dự kiến sau khi tách/hợp",
            field_type=FieldType.number,
            required=True,
            default_value=2,
            section="request",
        ),
        FormField(
            name="split_target_area",
            label="Diện tích từng thửa dự kiến tách (m²)",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Thửa 1: 80m² (mặt tiền 5m), Thửa 2: 70.5m² (mặt tiền 5m)",
            section="request",
            description="Đảm bảo diện tích tối thiểu và kích thước mặt tiền theo quy định của UBND cấp tỉnh",
        ),
        FormField(
            name="split_reason",
            label="Lý do đề nghị tách/hợp thửa",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Tách thửa để tặng cho con gái / chuyển nhượng một phần thửa đất...",
            section="request",
        ),
    ])
    return fields


def get_fields_dd_004() -> List[FormField]:
    """DD-004: Thủ tục đăng ký đất đai, cấp Giấy chứng nhận lần đầu"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend(get_land_plot_block(section="property"))
    fields.extend([
        FormField(
            name="land_origin_detail",
            label="Thời điểm và nguồn gốc bắt đầu sử dụng đất",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Sử dụng ổn định từ năm 1992 do ông cha để lại / Khai hoang / Nhận chuyển nhượng giấy viết tay...",
            section="origin",
        ),
        FormField(
            name="house_construction_year",
            label="Tài sản gắn liền với đất (Nhà ở/Công trình)",
            field_type=FieldType.textarea,
            required=False,
            placeholder="Nhà bê tông 2 tầng, diện tích xây dựng 90m², hoàn thành năm 2010...",
            section="origin",
        ),
        FormField(
            name="dispute_commitment",
            label="Cam kết ranh giới và tình trạng tranh chấp",
            field_type=FieldType.checkbox,
            required=True,
            default_value=True,
            description="Tôi cam kết ranh giới thửa đất sử dụng ổn định, không có tranh chấp hay lấn chiếm với các hộ liền kề",
            section="origin",
        ),
    ])
    return fields


def get_fields_dd_005() -> List[FormField]:
    """DD-005: Thủ tục cấp lại Giấy chứng nhận do bị mất"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend(get_land_plot_block(section="property"))
    fields.extend([
        FormField(
            name="loss_reason",
            label="Lý do bị mất Giấy chứng nhận",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Bị thất lạc trong quá trình chuyển nhà / Do hỏa hoạn, lũ lụt...",
            section="request",
        ),
        FormField(
            name="loss_report_date",
            label="Ngày khai báo mất tại UBND cấp xã",
            field_type=FieldType.date,
            required=True,
            section="request",
        ),
        FormField(
            name="loss_commitment",
            label="Cam kết không mang đi thế chấp",
            field_type=FieldType.checkbox,
            required=True,
            default_value=True,
            description="Tôi xin cam đoan Giấy chứng nhận không cầm cố, thế chấp trái phép. Nếu sai tôi hoàn toàn chịu trách nhiệm trước pháp luật",
            section="request",
        ),
    ])
    return fields


def get_fields_dd_006() -> List[FormField]:
    """DD-006: Đăng ký biến động do tặng cho, thừa kế quyền sử dụng đất, nhà ở (Mẫu số 18 - Nghị định 151/2025/NĐ-CP)"""
    hint = (
        "Nhận tặng cho / Thừa kế quyền sử dụng đất theo Hợp đồng tặng cho / Văn bản khai nhận di sản thừa kế số ... lập ngày ... tại Văn phòng Công chứng ...\n"
        "- Thửa đất số: ..., Tờ bản đồ số: ...\n"
        "- Địa chỉ thửa đất: ...\n"
        "- Diện tích: ... m²\n"
        "- Giấy chứng nhận số phát hành: ..., Số vào sổ cấp GCN: ...\n"
        "- Nhu cầu cấp Giấy chứng nhận: Có nhu cầu cấp mới Giấy chứng nhận quyền sử dụng đất."
    )
    return get_fields_mau_so_18(change_hint=hint)


def get_fields_dd_007() -> List[FormField]:
    """DD-007: Thủ tục đính chính Giấy chứng nhận đã cấp do sai sót thông tin"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend(get_land_plot_block(section="property"))
    fields.extend([
        FormField(
            name="correction_field",
            label="Nội dung thông tin bị sai sót",
            field_type=FieldType.select,
            required=True,
            default_value="applicant_info",
            options=[
                FormFieldOption(value="applicant_info", label="Sai sót thông tin người sử dụng đất (Họ tên, năm sinh, số CCCD, địa chỉ)"),
                FormFieldOption(value="plot_info", label="Sai sót thông tin thửa đất (Số thửa, tờ bản đồ, diện tích)"),
                FormFieldOption(value="purpose_info", label="Sai sót mục đích sử dụng đất hoặc thời hạn sử dụng"),
            ],
            section="request",
        ),
        FormField(
            name="correction_wrong_value",
            label="Thông tin ghi sai trên Giấy chứng nhận",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Ghi sai năm sinh: 1978, sai họ tên: Nguyễn Văn An...",
            section="request",
        ),
        FormField(
            name="correction_correct_value",
            label="Thông tin đúng theo giấy tờ pháp lý gốc",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Năm sinh đúng là: 1980 (theo CCCD số...), họ tên đúng: Nguyễn Văn Anh...",
            section="request",
        ),
    ])
    return fields


def get_fields_dd_008() -> List[FormField]:
    """DD-008: Thủ tục chuyển mục đích sử dụng đất"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend(get_land_plot_block(section="property"))
    fields.extend([
        FormField(
            name="conversion_from_purpose",
            label="Mục đích sử dụng đất hiện tại",
            field_type=FieldType.text,
            required=True,
            default_value="Đất trồng cây lâu năm (CLN)",
            section="conversion",
        ),
        FormField(
            name="conversion_to_purpose",
            label="Mục đích sử dụng đất xin chuyển sang",
            field_type=FieldType.select,
            required=True,
            default_value="Đất ở tại đô thị (ODT)",
            options=[
                FormFieldOption(value="Đất ở tại nông thôn (ONT)", label="Đất ở tại nông thôn (ONT)"),
                FormFieldOption(value="Đất ở tại đô thị (ODT)", label="Đất ở tại đô thị (ODT)"),
                FormFieldOption(value="Đất thương mại, dịch vụ (TMD)", label="Đất thương mại, dịch vụ (TMD)"),
            ],
            section="conversion",
        ),
        FormField(
            name="conversion_area",
            label="Diện tích xin chuyển mục đích (m²)",
            field_type=FieldType.number,
            required=True,
            placeholder="100.0",
            section="conversion",
        ),
        FormField(
            name="conversion_reason",
            label="Lý do xin chuyển mục đích",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Chuyển mục đích sang đất ở để xây dựng nhà ở cho gia đình...",
            section="conversion",
        ),
    ])
    return fields


def get_fields_dd_009() -> List[FormField]:
    """DD-009: Thủ tục gia hạn thời hạn sử dụng đất"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend(get_land_plot_block(section="property"))
    fields.extend([
        FormField(
            name="current_expired_date",
            label="Thời hạn sử dụng đất ghi trên GCN hết hạn ngày",
            field_type=FieldType.date,
            required=True,
            section="request",
        ),
        FormField(
            name="extension_duration_years",
            label="Thời gian đề nghị gia hạn (số năm)",
            field_type=FieldType.number,
            required=True,
            default_value=50,
            section="request",
        ),
        FormField(
            name="extension_reason",
            label="Lý do đề nghị gia hạn / xác nhận tiếp tục sử dụng",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Gia đình trực tiếp sản xuất nông nghiệp, có nhu cầu tiếp tục sử dụng đất...",
            section="request",
        ),
    ])
    return fields


def get_fields_dd_010() -> List[FormField]:
    """DD-010: Thủ tục đăng ký thế chấp quyền sử dụng đất, tài sản gắn liền với đất"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend(get_land_plot_block(section="property"))
    fields.extend([
        FormField(
            name="mortgage_party_name",
            label="Bên nhận thế chấp (Ngân hàng / Tổ chức tín dụng)",
            field_type=FieldType.text,
            required=True,
            placeholder="NGÂN HÀNG TMCP NGOẠI THƯƠNG VIỆT NAM (VIETCOMBANK) - CHI NHÁNH...",
            section="mortgage",
        ),
        FormField(
            name="mortgage_party_address",
            label="Địa chỉ bên nhận thế chấp",
            field_type=FieldType.textarea,
            required=True,
            section="mortgage",
        ),
        FormField(
            name="mortgage_contract_number",
            label="Số Hợp đồng thế chấp",
            field_type=FieldType.text,
            required=True,
            placeholder="HĐTC-2026/01/VCB",
            section="mortgage",
        ),
        FormField(
            name="mortgage_contract_date",
            label="Ngày ký Hợp đồng thế chấp",
            field_type=FieldType.date,
            required=True,
            section="mortgage",
        ),
        FormField(
            name="secured_obligation_amount",
            label="Giá trị nghĩa vụ được bảo đảm (VNĐ)",
            field_type=FieldType.number,
            required=True,
            placeholder="2000000000",
            section="mortgage",
        ),
    ])
    return fields


def get_fields_dd_011() -> List[FormField]:
    """DD-011: Thủ tục xóa đăng ký thế chấp quyền sử dụng đất (Giải chấp Sổ đỏ)"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend(get_land_plot_block(section="property"))
    fields.extend([
        FormField(
            name="mortgage_party_name",
            label="Bên nhận thế chấp (Ngân hàng đã giải chấp)",
            field_type=FieldType.text,
            required=True,
            placeholder="NGÂN HÀNG...",
            section="release",
        ),
        FormField(
            name="mortgage_registration_number",
            label="Số hồ sơ đăng ký thế chấp trước đây",
            field_type=FieldType.text,
            required=True,
            placeholder="Số: 1234/ĐKTC ngày...",
            section="release",
        ),
        FormField(
            name="mortgage_release_reason",
            label="Lý do xóa đăng ký thế chấp",
            field_type=FieldType.text,
            required=True,
            default_value="Đã hoàn thành toàn bộ nghĩa vụ trả nợ vay theo Hợp đồng tín dụng",
            section="release",
        ),
    ])
    return fields


def get_fields_dd_012() -> List[FormField]:
    """DD-012: Thủ tục cung cấp thông tin, dữ liệu đất đai, trích lục bản đồ địa chính"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend(get_land_plot_block(section="property"))
    fields.extend([
        FormField(
            name="data_request_purpose",
            label="Mục đích yêu cầu khai thác thông tin",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Phục vụ tìm hiểu quy hoạch xây dựng, giải quyết tranh chấp hoặc kiểm tra tình trạng pháp lý thửa đất...",
            section="request",
        ),
        FormField(
            name="requested_data_type",
            label="Loại thông tin yêu cầu cung cấp",
            field_type=FieldType.select,
            required=True,
            default_value="map_extract",
            options=[
                FormFieldOption(value="map_extract", label="Trích lục bản đồ địa chính thửa đất"),
                FormFieldOption(value="planning_info", label="Thông tin quy hoạch sử dụng đất"),
                FormFieldOption(value="dispute_status", label="Thông tin tình trạng ngăn chặn, thế chấp, tranh chấp"),
            ],
            section="request",
        ),
    ])
    return fields


def get_fields_dd_013() -> List[FormField]:
    """DD-013: Giao đất, thuê đất, chuyển mục đích sử dụng đất (Mẫu số 02d)"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend(get_land_plot_block(section="property"))
    fields.extend([
        FormField(
            name="allocation_request_type",
            label="Hình thức đề nghị giải quyết",
            field_type=FieldType.select,
            required=True,
            default_value="land_allocation_with_fee",
            options=[
                FormFieldOption(value="land_allocation_with_fee", label="Giao đất có thu tiền sử dụng đất"),
                FormFieldOption(value="land_allocation_no_fee", label="Giao đất không thu tiền sử dụng đất"),
                FormFieldOption(value="land_lease_annual", label="Thuê đất trả tiền thuê đất hằng năm"),
                FormFieldOption(value="land_lease_lump_sum", label="Thuê đất trả tiền thuê đất một lần cho cả thời gian thuê"),
                FormFieldOption(value="purpose_conversion", label="Cho phép chuyển mục đích sử dụng đất"),
            ],
            section="request",
        ),
        FormField(
            name="receiving_authority",
            label="Kính gửi (Cơ quan có thẩm quyền tiếp nhận)",
            field_type=FieldType.text,
            required=True,
            default_value="Ủy ban nhân dân cấp huyện nơi có đất",
            placeholder="Ủy ban nhân dân tỉnh/thành phố hoặc UBND quận/huyện...",
            section="request",
        ),
        FormField(
            name="rice_land_area",
            label="Diện tích đất chuyên trồng lúa (m², nếu có)",
            field_type=FieldType.number,
            required=False,
            placeholder="0",
            section="request",
        ),
        FormField(
            name="topsoil_strip_area",
            label="Diện tích phải bóc tách tầng đất mặt (m², nếu có)",
            field_type=FieldType.number,
            required=False,
            placeholder="0",
            section="request",
        ),
        FormField(
            name="proposed_land_use_term",
            label="Thời hạn sử dụng đất đề nghị",
            field_type=FieldType.text,
            required=True,
            default_value="Lâu dài (hoặc 50 năm theo dự án)",
            section="request",
        ),
        FormField(
            name="fee_exemption_request",
            label="Đề nghị miễn, giảm tiền sử dụng đất / tiền thuê đất (nếu có)",
            field_type=FieldType.textarea,
            required=False,
            placeholder="Ghi rõ căn cứ miễn, giảm nếu thuộc đối tượng ưu đãi...",
            section="request",
        ),
        FormField(
            name="attached_documents",
            label="Tài liệu gửi kèm theo đơn",
            field_type=FieldType.textarea,
            required=True,
            default_value="1. Bản sao giấy tờ nhân thân / pháp nhân;\n2. Văn bản phê duyệt dự án hoặc trích lục bản đồ địa chính thửa đất.",
            section="request",
        ),
    ])
    return fields


def get_fields_dd_014() -> List[FormField]:
    """DD-014: Hòa giải tranh chấp đất đai"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend(get_land_plot_block(section="property"))
    fields.extend([
        FormField(
            name="mediation_commune_committee",
            label="Kính gửi Ủy ban nhân dân cấp xã nơi có đất tranh chấp",
            field_type=FieldType.text,
            required=True,
            default_value="Ủy ban nhân dân phường/xã...",
            section="request",
        ),
        FormField(
            name="disputing_party_name",
            label="Họ và tên người / hộ gia đình phía bên kia tranh chấp",
            field_type=FieldType.text,
            required=True,
            placeholder="TRẦN VĂN B",
            section="request",
        ),
        FormField(
            name="disputing_party_address",
            label="Địa chỉ cư trú của bên có tranh chấp",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Số nhà, thôn/xóm, xã/phường, quận/huyện, tỉnh/thành phố",
            section="request",
        ),
        FormField(
            name="disputed_area",
            label="Diện tích đất đang tranh chấp (m²)",
            field_type=FieldType.number,
            required=True,
            placeholder="25.5",
            section="request",
        ),
        FormField(
            name="dispute_summary",
            label="Tóm tắt nguồn gốc đất, nội dung và nguyên nhân phát sinh tranh chấp",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Trình bày rõ nguồn gốc sử dụng đất, thời điểm phát sinh tranh chấp ranh giới hoặc quyền sử dụng...",
            section="request",
        ),
        FormField(
            name="mediation_request_content",
            label="Yêu cầu, kiến nghị hòa giải tại UBND cấp xã",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Đề nghị UBND cấp xã tổ chức hòa giải, xác định lại mốc giới thửa đất theo đúng Giấy chứng nhận...",
            section="request",
        ),
        FormField(
            name="attached_evidence",
            label="Tài liệu, chứng cứ gửi kèm theo đơn",
            field_type=FieldType.textarea,
            required=False,
            default_value="Bản sao Giấy chứng nhận QSDĐ, bản vẽ hiện trạng thửa đất, biên bản làm việc tại thôn/tổ dân phố...",
            section="request",
        ),
    ])
    return fields


def get_fields_dd_015() -> List[FormField]:
    """DD-015: Yêu cầu đo đạc địa chính, tách thửa phục vụ đăng ký đất đai"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend(get_land_plot_block(section="property"))
    fields.extend([
        FormField(
            name="survey_receiving_agency",
            label="Đơn vị tiếp nhận yêu cầu đo đạc",
            field_type=FieldType.text,
            required=True,
            default_value="Chi nhánh Văn phòng Đăng ký đất đai / Ủy ban nhân dân cấp xã",
            section="request",
        ),
        FormField(
            name="applicant_land_relation",
            label="Tư cách của người yêu cầu đối với thửa đất",
            field_type=FieldType.select,
            required=True,
            default_value="owner",
            options=[
                FormFieldOption(value="owner", label="Người sử dụng đất đứng tên trên Giấy chứng nhận"),
                FormFieldOption(value="transferee", label="Người nhận chuyển nhượng, tặng cho, thừa kế quyền sử dụng đất"),
                FormFieldOption(value="first_time_applicant", label="Người đang sử dụng đất đề nghị cấp GCN lần đầu"),
                FormFieldOption(value="related_party", label="Người có quyền lợi, nghĩa vụ liên quan"),
            ],
            section="request",
        ),
        FormField(
            name="survey_purpose",
            label="Mục đích yêu cầu đo đạc địa chính",
            field_type=FieldType.select,
            required=True,
            default_value="boundary_area_check",
            options=[
                FormFieldOption(value="boundary_area_check", label="Xác định lại ranh giới, diện tích thực tế thửa đất"),
                FormFieldOption(value="split_merge_plot", label="Lập bản vẽ trích đo phục vụ tách thửa, hợp thửa đất"),
                FormFieldOption(value="new_or_reissue_cert", label="Lập bản vẽ phục vụ cấp mới / cấp đổi Giấy chứng nhận"),
                FormFieldOption(value="dispute_resolution", label="Đo đạc hiện trạng phục vụ hòa giải / giải quyết tranh chấp"),
            ],
            section="request",
        ),
        FormField(
            name="survey_detail_reason",
            label="Nội dung sự việc và yêu cầu đo đạc cụ thể",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Trình bày rõ hiện trạng sử dụng đất và yêu cầu trích đo hiện trạng...",
            section="request",
        ),
        FormField(
            name="adjacent_owners_info",
            label="Thông tin các hộ sử dụng đất giáp ranh (Đông, Tây, Nam, Bắc - nếu có)",
            field_type=FieldType.textarea,
            required=False,
            placeholder="Phía Đông giáp hộ ông/bà...; Phía Tây giáp...",
            section="request",
        ),
    ])
    return fields


def get_fields_dd_016() -> List[FormField]:
    """DD-016: Xác nhận lại thời hạn sử dụng đất nông nghiệp của hộ gia đình, cá nhân (Mẫu số 09)"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend(get_land_plot_block(section="property"))
    fields.extend([
        FormField(
            name="current_expired_date",
            label="Thời hạn sử dụng đất ghi trên Giấy chứng nhận hết hạn ngày",
            field_type=FieldType.date,
            required=True,
            section="request",
        ),
        FormField(
            name="proposed_confirm_until_date",
            label="Đề nghị xác nhận tiếp tục sử dụng đất nông nghiệp đến ngày",
            field_type=FieldType.date,
            required=True,
            section="request",
        ),
        FormField(
            name="attached_documents",
            label="Giấy tờ nộp kèm theo đơn",
            field_type=FieldType.textarea,
            required=True,
            default_value="Bản gốc Giấy chứng nhận quyền sử dụng đất đã cấp.",
            section="request",
        ),
        FormField(
            name="direct_agricultural_commitment",
            label="Cam kết trực tiếp sản xuất nông nghiệp",
            field_type=FieldType.checkbox,
            required=True,
            default_value=True,
            description="Tôi cam kết hộ gia đình, cá nhân đang trực tiếp sử dụng đất để sản xuất nông nghiệp ổn định và có nhu cầu tiếp tục sử dụng",
            section="request",
        ),
    ])
    return fields


def get_fields_dd_017() -> List[FormField]:
    """DD-017: Chuyển hình thức giao đất, cho thuê đất (Mẫu số 02)"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend(get_land_plot_block(section="property"))
    fields.extend([
        FormField(
            name="previous_decision_info",
            label="Quyết định giao đất / cho thuê đất / chuyển mục đích sử dụng đất trước đây",
            field_type=FieldType.text,
            required=True,
            placeholder="Quyết định số .../QĐ-UBND ngày .../.../... của UBND...",
            section="conversion",
        ),
        FormField(
            name="current_allocation_form",
            label="Hình thức giao đất / thuê đất hiện tại",
            field_type=FieldType.select,
            required=True,
            default_value="lease_annual",
            options=[
                FormFieldOption(value="lease_annual", label="Thuê đất trả tiền thuê đất hằng năm"),
                FormFieldOption(value="lease_lump_sum", label="Thuê đất trả tiền thuê đất một lần cho cả thời gian thuê"),
                FormFieldOption(value="allocation_no_fee", label="Giao đất không thu tiền sử dụng đất"),
                FormFieldOption(value="allocation_with_fee", label="Giao đất có thu tiền sử dụng đất"),
            ],
            section="conversion",
        ),
        FormField(
            name="target_allocation_form",
            label="Hình thức giao đất / thuê đất đề nghị chuyển sang",
            field_type=FieldType.select,
            required=True,
            default_value="lease_lump_sum",
            options=[
                FormFieldOption(value="lease_lump_sum", label="Thuê đất trả tiền thuê đất một lần cho cả thời gian thuê"),
                FormFieldOption(value="lease_annual", label="Thuê đất trả tiền thuê đất hằng năm"),
                FormFieldOption(value="allocation_with_fee", label="Giao đất có thu tiền sử dụng đất"),
            ],
            section="conversion",
        ),
        FormField(
            name="conversion_reason",
            label="Lý do và căn cứ đề nghị chuyển hình thức sử dụng đất",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Chuyển hình thức thuê đất theo quy định tại Luật Đất đai 2024 để thực hiện dự án đầu tư...",
            section="conversion",
        ),
        FormField(
            name="financial_commitment",
            label="Cam kết thực hiện nghĩa vụ tài chính",
            field_type=FieldType.checkbox,
            required=True,
            default_value=True,
            description="Tôi cam kết chấp hành đúng quy định của pháp luật đất đai và nộp đầy đủ tiền sử dụng đất / tiền thuê đất theo thông báo",
            section="conversion",
        ),
    ])
    return fields


def get_fields_dd_018() -> List[FormField]:
    """DD-018: Thủ tục cấp Giấy phép xây dựng mới đối với nhà ở riêng lẻ"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend(get_land_plot_block(section="property"))
    fields.extend([
        FormField(
            name="licensing_authority",
            label="Cơ quan cấp phép xây dựng (Kính gửi)",
            field_type=FieldType.text,
            required=True,
            default_value="Ủy ban nhân dân Quận/Huyện nơi có công trình",
            section="request",
        ),
        FormField(
            name="building_type",
            label="Loại công trình nhà ở riêng lẻ",
            field_type=FieldType.select,
            required=True,
            default_value="urban_detached_house",
            options=[
                FormFieldOption(value="urban_detached_house", label="Nhà ở riêng lẻ tại đô thị"),
                FormFieldOption(value="rural_detached_house", label="Nhà ở riêng lẻ tại nông thôn"),
            ],
            section="request",
        ),
        FormField(
            name="building_grade",
            label="Cấp công trình xây dựng",
            field_type=FieldType.select,
            required=True,
            default_value="grade_3",
            options=[
                FormFieldOption(value="grade_3", label="Công trình cấp III"),
                FormFieldOption(value="grade_4", label="Công trình cấp IV"),
                FormFieldOption(value="grade_2", label="Công trình cấp II"),
            ],
            section="request",
        ),
        FormField(
            name="design_organization_name",
            label="Tổ chức / cá nhân lập thiết kế xây dựng",
            field_type=FieldType.text,
            required=True,
            placeholder="Công ty TNHH Tư vấn Thiết kế Xây dựng...",
            section="request",
        ),
        FormField(
            name="building_footprint_area",
            label="Diện tích xây dựng tầng 1 (tầng trệt) (m²)",
            field_type=FieldType.number,
            required=True,
            placeholder="85.0",
            section="request",
        ),
        FormField(
            name="total_floor_area",
            label="Tổng diện tích sàn xây dựng (bao gồm tầng hầm, lửng, tum) (m²)",
            field_type=FieldType.number,
            required=True,
            placeholder="260.0",
            section="request",
        ),
        FormField(
            name="building_height",
            label="Chiều cao công trình tính từ cốt nền (m)",
            field_type=FieldType.number,
            required=True,
            placeholder="11.5",
            section="request",
        ),
        FormField(
            name="number_of_floors",
            label="Tổng số tầng xây dựng",
            field_type=FieldType.number,
            required=True,
            default_value=3,
            section="request",
        ),
        FormField(
            name="expected_completion_months",
            label="Thời gian dự kiến hoàn thành công trình (số tháng)",
            field_type=FieldType.number,
            required=True,
            default_value=6,
            section="request",
        ),
    ])
    return fields


def get_fields_dd_019() -> List[FormField]:
    """DD-019: Thủ tục cấp Giấy phép sửa chữa, cải tạo công trình nhà ở riêng lẻ"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend(get_land_plot_block(section="property"))
    fields.extend([
        FormField(
            name="licensing_authority",
            label="Cơ quan cấp phép xây dựng (Kính gửi)",
            field_type=FieldType.text,
            required=True,
            default_value="Ủy ban nhân dân Quận/Huyện nơi có công trình",
            section="request",
        ),
        FormField(
            name="existing_building_info",
            label="Quy mô công trình nhà ở hiện hữu (diện tích xây dựng, tổng diện tích sàn, chiều cao, số tầng)",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Nhà ở cấp III hiện trạng 2 tầng, diện tích xây dựng tầng 1: 75m², tổng diện tích sàn: 150m², chiều cao: 7.6m...",
            section="request",
        ),
        FormField(
            name="renovation_content",
            label="Nội dung đề nghị sửa chữa, cải tạo và quy mô sau cải tạo",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Cải tạo mặt tiền, gia cố kết cấu và nâng thêm 1 tầng; tổng diện tích sàn sau cải tạo: 225m², chiều cao: 11.2m...",
            section="request",
        ),
        FormField(
            name="design_organization_name",
            label="Tổ chức / cá nhân lập thiết kế sửa chữa, cải tạo",
            field_type=FieldType.text,
            required=True,
            placeholder="Công ty Cổ phần Tư vấn Thiết kế Kiến trúc...",
            section="request",
        ),
        FormField(
            name="expected_completion_months",
            label="Thời gian dự kiến hoàn thành sửa chữa, cải tạo (số tháng)",
            field_type=FieldType.number,
            required=True,
            default_value=4,
            section="request",
        ),
        FormField(
            name="adjacent_safety_commitment",
            label="Cam kết đảm bảo an toàn công trình và công trình lân cận",
            field_type=FieldType.checkbox,
            required=True,
            default_value=True,
            description="Tôi cam kết đảm bảo an toàn cho công trình, các công trình lân cận và tuân thủ quy định bảo vệ môi trường khi thi công",
            section="request",
        ),
    ])
    return fields


def get_fields_dd_020() -> List[FormField]:
    """DD-020: 38. Mẫu biên bản nghiệm thu công việc xây dựng"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend(get_land_plot_block(section="property"))
    fields.extend([
        FormField(
            name="acceptance_report_number",
            label="Số biên bản nghiệm thu công việc xây dựng",
            field_type=FieldType.text,
            required=True,
            placeholder="BBNT-01/2026",
            section="request",
        ),
        FormField(
            name="project_item_name",
            label="Tên công trình / hạng mục công trình xây dựng",
            field_type=FieldType.text,
            required=True,
            placeholder="Công trình nhà ở riêng lẻ / Hạng mục kết cấu móng và khung bê tông cốt thép",
            section="request",
        ),
        FormField(
            name="accepted_work_object",
            label="Đối tượng nghiệm thu (Tên công việc xây dựng được nghiệm thu)",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Nghiệm thu công tác lắp dựng cốt thép, cốp pha và đổ bê tông sàn tầng...",
            section="request",
        ),
        FormField(
            name="supervision_representative",
            label="Họ tên người giám sát thi công xây dựng (Đại diện Chủ đầu tư / Ban QLDA)",
            field_type=FieldType.text,
            required=True,
            placeholder="Kỹ sư giám sát: NGUYỄN VĂN B",
            section="request",
        ),
        FormField(
            name="contractor_representative",
            label="Họ tên người phụ trách kỹ thuật thi công trực tiếp (Đại diện Nhà thầu)",
            field_type=FieldType.text,
            required=True,
            placeholder="Chỉ huy trưởng: LÊ VĂN C",
            section="request",
        ),
        FormField(
            name="acceptance_date",
            label="Ngày tiến hành nghiệm thu",
            field_type=FieldType.date,
            required=True,
            section="request",
        ),
        FormField(
            name="quality_evaluation",
            label="Đánh giá chất lượng công việc xây dựng đã thực hiện",
            field_type=FieldType.textarea,
            required=True,
            default_value="Công việc xây dựng được thi công đúng hồ sơ thiết kế bản vẽ thi công được duyệt và đạt yêu cầu theo quy chuẩn, tiêu chuẩn kỹ thuật hiện hành.",
            section="request",
        ),
        FormField(
            name="acceptance_conclusion",
            label="Kết luận nghiệm thu",
            field_type=FieldType.select,
            required=True,
            default_value="approved",
            options=[
                FormFieldOption(value="approved", label="Đồng ý nghiệm thu, cho phép triển khai công việc xây dựng tiếp theo"),
                FormFieldOption(value="rework_required", label="Yêu cầu nhà thầu khắc phục tồn tại và tổ chức nghiệm thu lại"),
                FormFieldOption(value="rejected", label="Không đồng ý nghiệm thu"),
            ],
            section="request",
        ),
    ])
    return fields


def get_fields_dd_021() -> List[FormField]:
    """DD-021: 37.Mẫu đơn Giấy phép sửa chữa, cải tạo công trình (Phụ lục II - Mẫu số 01)"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend(get_land_plot_block(section="property"))
    fields.extend([
        FormField(
            name="licensing_authority",
            label="Kính gửi (Cơ quan có thẩm quyền cấp giấy phép xây dựng)",
            field_type=FieldType.text,
            required=True,
            default_value="Ủy ban nhân dân Quận/Huyện / Sở Xây dựng",
            section="request",
        ),
        FormField(
            name="investor_representative",
            label="Người đại diện chủ đầu tư và chức vụ (nếu chủ đầu tư là tổ chức)",
            field_type=FieldType.text,
            required=False,
            placeholder="Ghi họ tên, chức vụ người đại diện nếu là tổ chức/doanh nghiệp",
            section="request",
        ),
        FormField(
            name="design_unit_name",
            label="Tổ chức/cá nhân lập thiết kế xây dựng và mã số chứng chỉ năng lực/hành nghề",
            field_type=FieldType.text,
            required=True,
            placeholder="Công ty... - Mã số chứng chỉ: ... - Chủ trì thiết kế: ...",
            section="request",
        ),
        FormField(
            name="verification_unit_name",
            label="Tổ chức/cá nhân thẩm tra thiết kế xây dựng (nếu có)",
            field_type=FieldType.text,
            required=False,
            placeholder="Tên đơn vị thẩm tra, mã số chứng chỉ và tên chủ trì thẩm tra",
            section="request",
        ),
        FormField(
            name="project_category_grade",
            label="Loại công trình và cấp công trình",
            field_type=FieldType.text,
            required=True,
            default_value="Công trình dân dụng (Nhà ở riêng lẻ / Công trình cải tạo) - Cấp III",
            section="request",
        ),
        FormField(
            name="building_footprint_area",
            label="Diện tích xây dựng tầng 1 (tầng trệt) (m²)",
            field_type=FieldType.number,
            required=True,
            placeholder="90.0",
            section="request",
        ),
        FormField(
            name="total_floor_area",
            label="Tổng diện tích sàn xây dựng (bao gồm tầng hầm, lửng, kỹ thuật, tum) (m²)",
            field_type=FieldType.number,
            required=True,
            placeholder="280.0",
            section="request",
        ),
        FormField(
            name="ground_elevation_and_height",
            label="Cốt xây dựng và chiều cao toàn bộ công trình (m)",
            field_type=FieldType.text,
            required=True,
            default_value="Cốt nền +0.45m; Chiều cao công trình: 11.8m",
            section="request",
        ),
        FormField(
            name="number_of_floors",
            label="Số tầng công trình (tầng nổi, tầng hầm)",
            field_type=FieldType.number,
            required=True,
            default_value=3,
            section="request",
        ),
        FormField(
            name="renovation_scope_detail",
            label="Nội dung đề nghị cấp phép sửa chữa, cải tạo chi tiết",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Mô tả rõ hiện trạng công trình trước khi cải tạo và các hạng mục xin phép sửa chữa, cải tạo...",
            section="request",
        ),
        FormField(
            name="expected_completion_months",
            label="Thời gian dự kiến hoàn thành công trình (số tháng)",
            field_type=FieldType.number,
            required=True,
            default_value=6,
            section="request",
        ),
    ])
    return fields


def get_fields_dd_022() -> List[FormField]:
    """DD-022: Phương án sử dụng tầng đất mặt"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend(get_land_plot_block(section="property"))
    fields.extend([
        FormField(
            name="project_name",
            label="Tên công trình / dự án chuyển mục đích sử dụng từ đất chuyên trồng lúa",
            field_type=FieldType.text,
            required=True,
            placeholder="Dự án xây dựng hạ tầng / nhà ở tại...",
            section="conversion",
        ),
        FormField(
            name="rice_land_strip_area",
            label="Diện tích đất chuyên trồng lúa phải bóc tách tầng đất mặt (m²)",
            field_type=FieldType.number,
            required=True,
            placeholder="500.0",
            section="conversion",
        ),
        FormField(
            name="topsoil_depth_meters",
            label="Độ sâu tầng đất mặt bóc tách (m)",
            field_type=FieldType.number,
            required=True,
            default_value=0.3,
            section="conversion",
        ),
        FormField(
            name="topsoil_volume_m3",
            label="Khối lượng tầng đất mặt phải bóc tách (m³)",
            field_type=FieldType.number,
            required=True,
            placeholder="150.0",
            section="conversion",
        ),
        FormField(
            name="topsoil_reuse_location",
            label="Vị trí, địa điểm khu đất sử dụng tầng đất mặt sau khi bóc tách",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Khu đất nông nghiệp tại thôn..., xã..., huyện... (diện tích sử dụng ... m²)",
            section="conversion",
        ),
        FormField(
            name="topsoil_reuse_purpose",
            label="Mục đích và phương án sử dụng tầng đất mặt",
            field_type=FieldType.textarea,
            required=True,
            default_value="Bổ sung, cải tạo độ màu mỡ cho đất nông nghiệp / phục vụ trồng cây xanh cảnh quan theo quy định.",
            section="conversion",
        ),
        FormField(
            name="environmental_commitment",
            label="Cam kết thực hiện đúng phương án bóc tách và bảo vệ môi trường",
            field_type=FieldType.checkbox,
            required=True,
            default_value=True,
            description="Tôi cam kết bóc tách, vận chuyển và sử dụng tầng đất mặt đúng khối lượng, vị trí đã kê khai",
            section="conversion",
        ),
    ])
    return fields


# ==============================================================================
# 13 HÀM CHI TIẾT LĨNH VỰC KINH DOANH (BUSINESS PROCEDURES)
# ==============================================================================

def get_fields_hkd_001() -> List[FormField]:
    """HKD-001: Đăng ký thành lập hộ kinh doanh"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend([
        FormField(
            name="business_name_vn",
            label="Tên Hộ kinh doanh",
            field_type=FieldType.text,
            required=True,
            placeholder="HỘ KINH DOANH HOÀNG PHÁT",
            section="business",
            description="Phải bao gồm hai thành tố: 'Hộ kinh doanh' + Tên riêng",
        ),
        FormField(
            name="headquarters_address",
            label="Địa chỉ địa điểm kinh doanh",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Số nhà, đường/phố, phường/xã, quận/huyện, tỉnh/thành phố",
            section="business",
        ),
        FormField(
            name="headquarters_phone",
            label="Số điện thoại liên hệ",
            field_type=FieldType.text,
            required=True,
            placeholder="0912345678",
            section="business",
        ),
        FormField(
            name="charter_capital",
            label="Số vốn kinh doanh (VNĐ)",
            field_type=FieldType.number,
            required=True,
            placeholder="100000000",
            section="business",
        ),
        FormField(
            name="main_business_lines",
            label="Ngành nghề kinh doanh",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Kinh doanh ăn uống, bán buôn bán lẻ hàng tiêu dùng...",
            section="business",
        ),
        FormField(
            name="total_employees",
            label="Số lượng lao động",
            field_type=FieldType.number,
            required=False,
            default_value=3,
            section="business",
        ),
    ])
    return fields


def get_fields_hkd_002() -> List[FormField]:
    """HKD-002: Thay đổi nội dung đăng ký hộ kinh doanh"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend([
        FormField(
            name="business_name_vn",
            label="Tên Hộ kinh doanh hiện tại",
            field_type=FieldType.text,
            required=True,
            section="business",
        ),
        FormField(
            name="business_code",
            label="Mã số đăng ký Hộ kinh doanh / Mã số thuế",
            field_type=FieldType.text,
            required=True,
            placeholder="01E8012345",
            section="business",
        ),
        FormField(
            name="change_items",
            label="Nội dung đăng ký thay đổi",
            field_type=FieldType.select,
            required=True,
            default_value="address",
            options=[
                FormFieldOption(value="address", label="Thay đổi địa điểm kinh doanh"),
                FormFieldOption(value="business_lines", label="Thay đổi ngành, nghề kinh doanh"),
                FormFieldOption(value="capital", label="Thay đổi vốn kinh doanh"),
                FormFieldOption(value="name", label="Đổi tên hộ kinh doanh"),
            ],
            section="change",
        ),
        FormField(
            name="change_content_before",
            label="Nội dung trước khi thay đổi",
            field_type=FieldType.textarea,
            required=True,
            section="change",
        ),
        FormField(
            name="change_content_after",
            label="Nội dung sau khi thay đổi",
            field_type=FieldType.textarea,
            required=True,
            section="change",
        ),
    ])
    return fields


def get_fields_hkd_003() -> List[FormField]:
    """HKD-003: Cấp lại Giấy chứng nhận đăng ký hộ kinh doanh"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend([
        FormField(
            name="business_name_vn",
            label="Tên Hộ kinh doanh (viết bằng chữ in hoa)",
            field_type=FieldType.text,
            required=True,
            placeholder="HỘ KINH DOANH HOÀNG PHÁT",
            section="business",
        ),
        FormField(
            name="business_code",
            label="Mã số Hộ kinh doanh / Mã số thuế",
            field_type=FieldType.text,
            required=True,
            placeholder="01E8012345",
            section="business",
        ),
        FormField(
            name="headquarters_address",
            label="Địa chỉ địa điểm kinh doanh",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Số nhà, đường/phố, phường/xã, quận/huyện, tỉnh/thành phố",
            section="business",
        ),
        FormField(
            name="headquarters_phone",
            label="Số điện thoại liên hệ hộ kinh doanh",
            field_type=FieldType.text,
            required=True,
            placeholder="0912345678",
            section="business",
        ),
        FormField(
            name="receiving_authority",
            label="Cơ quan đăng ký kinh doanh tiếp nhận",
            field_type=FieldType.text,
            required=True,
            default_value="Phòng Tài chính - Kế hoạch / Cơ quan đăng ký kinh doanh",
            section="request",
        ),
        FormField(
            name="reissue_reason",
            label="Lý do đề nghị cấp lại Giấy chứng nhận đăng ký hộ kinh doanh",
            field_type=FieldType.select,
            required=True,
            default_value="lost",
            options=[
                FormFieldOption(value="lost", label="Giấy chứng nhận đăng ký hộ kinh doanh bị mất"),
                FormFieldOption(value="damaged", label="Giấy chứng nhận bị cháy, rách, nát"),
                FormFieldOption(value="destroyed", label="Giấy chứng nhận bị tiêu hủy dưới hình thức khác"),
            ],
            section="request",
        ),
        FormField(
            name="reissue_detail_note",
            label="Trình bày cụ thể hoàn cảnh mất hoặc hư hỏng Giấy chứng nhận (nếu có)",
            field_type=FieldType.textarea,
            required=False,
            placeholder="Trình bày thời điểm, lý do thất lạc hoặc rách nát Giấy chứng nhận...",
            section="request",
        ),
    ])
    return fields


def get_fields_dn_001() -> List[FormField]:
    """DN-001: Thủ tục ủy quyền thực hiện thủ tục đăng ký doanh nghiệp"""
    fields = get_applicant_block(prefix="authorizer", section="authorizer")
    fields.extend(get_applicant_block(prefix="authorized_person", section="authorized_person"))
    fields.extend([
        FormField(
            name="business_name_vn",
            label="Tên Doanh nghiệp thực hiện thủ tục",
            field_type=FieldType.text,
            required=True,
            section="scope",
        ),
        FormField(
            name="authorization_scope",
            label="Phạm vi ủy quyền",
            field_type=FieldType.textarea,
            required=True,
            default_value="Được toàn quyền lập, ký, nộp hồ sơ, sửa đổi bổ sung hồ sơ và nhận kết quả đăng ký doanh nghiệp tại Phòng Đăng ký kinh doanh",
            section="scope",
        ),
        FormField(
            name="authorization_term",
            label="Thời hạn ủy quyền",
            field_type=FieldType.text,
            required=True,
            default_value="Cho đến khi hoàn tất thủ tục và nhận kết quả Giấy chứng nhận",
            section="scope",
        ),
    ])
    return fields


def get_fields_dn_002() -> List[FormField]:
    """DN-002: Thủ tục đề nghị cấp lại Giấy chứng nhận đăng ký doanh nghiệp"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend([
        FormField(
            name="business_name_vn",
            label="Tên Doanh nghiệp",
            field_type=FieldType.text,
            required=True,
            section="business",
        ),
        FormField(
            name="business_code",
            label="Mã số doanh nghiệp / Mã số thuế",
            field_type=FieldType.text,
            required=True,
            section="business",
        ),
        FormField(
            name="reissue_reason",
            label="Lý do đề nghị cấp lại Giấy chứng nhận",
            field_type=FieldType.select,
            required=True,
            default_value="lost",
            options=[
                FormFieldOption(value="lost", label="Bị mất Giấy chứng nhận đăng ký doanh nghiệp"),
                FormFieldOption(value="damaged", label="Giấy chứng nhận bị rách, nát, cháy hoặc tiêu hủy"),
            ],
            section="request",
        ),
    ])
    return fields


def get_fields_dn_003() -> List[FormField]:
    """DN-003: Thủ tục đăng ký thành lập Công ty Cổ phần"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend(get_business_info_block(section="business"))
    fields.extend([
        FormField(
            name="founding_shareholders_count",
            label="Số lượng cổ đông sáng lập (tối thiểu 03)",
            field_type=FieldType.number,
            required=True,
            default_value=3,
            section="governance",
        ),
        FormField(
            name="total_shares",
            label="Tổng số cổ phần đăng ký chào bán",
            field_type=FieldType.number,
            required=True,
            placeholder="100000",
            section="governance",
        ),
        FormField(
            name="par_value_per_share",
            label="Mệnh giá mỗi cổ phần (VNĐ)",
            field_type=FieldType.number,
            required=True,
            default_value=10000,
            section="governance",
        ),
        FormField(
            name="legal_representative_title",
            label="Chức danh người đại diện theo pháp luật",
            field_type=FieldType.select,
            required=True,
            default_value="general_director",
            options=[
                FormFieldOption(value="general_director", label="Tổng Giám đốc"),
                FormFieldOption(value="director", label="Giám đốc"),
                FormFieldOption(value="chairman", label="Chủ tịch Hội đồng quản trị"),
            ],
            section="governance",
        ),
    ])
    return fields


def get_fields_dn_004() -> List[FormField]:
    """DN-004: Thủ tục đăng ký thành lập Công ty Trách nhiệm hữu hạn"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend(get_business_info_block(section="business"))
    fields.extend([
        FormField(
            name="llc_type",
            label="Loại hình Công ty TNHH",
            field_type=FieldType.select,
            required=True,
            default_value="single_member",
            options=[
                FormFieldOption(value="single_member", label="Công ty TNHH Một thành viên"),
                FormFieldOption(value="multi_member", label="Công ty TNHH Hai thành viên trở lên"),
            ],
            section="governance",
        ),
        FormField(
            name="legal_representative_title",
            label="Chức danh người đại diện theo pháp luật",
            field_type=FieldType.select,
            required=True,
            default_value="director",
            options=[
                FormFieldOption(value="director", label="Giám đốc"),
                FormFieldOption(value="general_director", label="Tổng Giám đốc"),
                FormFieldOption(value="chairman", label="Chủ tịch công ty / Chủ tịch HĐTV"),
            ],
            section="governance",
        ),
    ])
    return fields


def get_fields_dn_005() -> List[FormField]:
    """DN-005: Thủ tục đăng ký thành lập Doanh nghiệp tư nhân"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend(get_business_info_block(section="business"))
    fields.append(
        FormField(
            name="owner_commitment",
            label="Cam kết trách nhiệm tài sản của chủ DNTN",
            field_type=FieldType.checkbox,
            required=True,
            default_value=True,
            description="Tôi cam kết chịu trách nhiệm bằng toàn bộ tài sản của mình về mọi hoạt động của doanh nghiệp",
            section="business",
        )
    )
    return fields


def get_fields_dn_006() -> List[FormField]:
    """DN-006: Thủ tục thuê nhà/mặt bằng làm Trụ sở chính doanh nghiệp"""
    fields = get_applicant_block(prefix="lessor", section="lessor")
    fields.extend(get_applicant_block(prefix="lessee", section="lessee"))
    fields.extend([
        FormField(
            name="rental_address",
            label="Địa chỉ mặt bằng / văn phòng cho thuê",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Tầng 5, Tòa nhà ABC, 123 Đường XYZ, Quận 1, TP.HCM",
            section="property",
        ),
        FormField(
            name="rental_area",
            label="Diện tích thuê (m²)",
            field_type=FieldType.number,
            required=True,
            placeholder="120",
            section="property",
        ),
        FormField(
            name="rental_price",
            label="Giá thuê hàng tháng (VNĐ)",
            field_type=FieldType.number,
            required=True,
            placeholder="25000000",
            section="financial",
        ),
        FormField(
            name="rental_deposit",
            label="Tiền đặt cọc (VNĐ)",
            field_type=FieldType.number,
            required=True,
            placeholder="50000000",
            section="financial",
        ),
        FormField(
            name="rental_term_months",
            label="Thời hạn thuê (số tháng)",
            field_type=FieldType.number,
            required=True,
            default_value=24,
            section="financial",
        ),
    ])
    return fields


def get_fields_dn_007() -> List[FormField]:
    """DN-007: Thủ tục đăng ký thuế, khai thuế ban đầu"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend([
        FormField(
            name="business_name_vn",
            label="Tên người nộp thuế (Doanh nghiệp)",
            field_type=FieldType.text,
            required=True,
            section="business",
        ),
        FormField(
            name="business_code",
            label="Mã số thuế",
            field_type=FieldType.text,
            required=True,
            placeholder="0312345678",
            section="business",
        ),
        FormField(
            name="tax_accounting_method",
            label="Chế độ kế toán áp dụng",
            field_type=FieldType.select,
            required=True,
            default_value="tt133",
            options=[
                FormFieldOption(value="tt133", label="Thông tư 133/2016/TT-BTC (Doanh nghiệp nhỏ và vừa)"),
                FormFieldOption(value="tt200", label="Thông tư 200/2014/TT-BTC (Doanh nghiệp lớn)"),
            ],
            section="tax",
        ),
        FormField(
            name="vat_calculation_method",
            label="Phương pháp tính thuế GTGT",
            field_type=FieldType.select,
            required=True,
            default_value="deduction",
            options=[
                FormFieldOption(value="deduction", label="Phương pháp khấu trừ"),
                FormFieldOption(value="direct", label="Phương pháp trực tiếp trên doanh thu"),
            ],
            section="tax",
        ),
        FormField(
            name="fiscal_year_start",
            label="Ngày bắt đầu năm tài chính",
            field_type=FieldType.text,
            required=True,
            default_value="01/01",
            section="tax",
        ),
    ])
    return fields


def get_fields_dn_008() -> List[FormField]:
    """DN-008: Thủ tục đăng ký thành lập Công ty Hợp danh"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend(get_business_info_block(section="business"))
    fields.append(
        FormField(
            name="general_partners_count",
            label="Số lượng thành viên hợp danh (tối thiểu 02 cá nhân)",
            field_type=FieldType.number,
            required=True,
            default_value=2,
            section="governance",
        )
    )
    return fields


def get_fields_dn_009() -> List[FormField]:
    """DN-009: Thủ tục đăng ký giải thể doanh nghiệp"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend([
        FormField(
            name="business_name_vn",
            label="Tên Doanh nghiệp giải thể",
            field_type=FieldType.text,
            required=True,
            section="business",
        ),
        FormField(
            name="business_code",
            label="Mã số thuế / Mã số doanh nghiệp",
            field_type=FieldType.text,
            required=True,
            section="business",
        ),
        FormField(
            name="dissolution_reason",
            label="Lý do giải thể",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Theo quyết định của chủ sở hữu/Đại hội đồng cổ đông do hoạt động kinh doanh không hiệu quả...",
            section="dissolution",
        ),
        FormField(
            name="debt_settlement_commitment",
            label="Cam kết thanh toán toàn bộ nợ và nghĩa vụ tài sản",
            field_type=FieldType.checkbox,
            required=True,
            default_value=True,
            description="Doanh nghiệp cam đoan đã thanh toán hết các khoản nợ gồm nợ thuế, nợ lương người lao động và thanh lý hợp đồng",
            section="dissolution",
        ),
    ])
    return fields


def get_fields_dn_010() -> List[FormField]:
    """DN-010: Thủ tục thông báo tạm ngừng / Cấp đổi thông tin ĐKDN"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend([
        FormField(
            name="business_name_vn",
            label="Tên Doanh nghiệp",
            field_type=FieldType.text,
            required=True,
            section="business",
        ),
        FormField(
            name="business_code",
            label="Mã số doanh nghiệp",
            field_type=FieldType.text,
            required=True,
            section="business",
        ),
        FormField(
            name="suspension_from_date",
            label="Ngày bắt đầu tạm ngừng kinh doanh",
            field_type=FieldType.date,
            required=True,
            section="suspension",
        ),
        FormField(
            name="suspension_to_date",
            label="Ngày kết thúc tạm ngừng kinh doanh",
            field_type=FieldType.date,
            required=True,
            section="suspension",
            description="Thời hạn tạm ngừng tối đa là 01 năm cho mỗi lần thông báo",
        ),
        FormField(
            name="suspension_reason",
            label="Lý do tạm ngừng kinh doanh",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Cơ cấu lại tổ chức hoạt động và tìm kiếm thị trường mới...",
            section="suspension",
        ),
    ])
    return fields


def get_fields_dn_011() -> List[FormField]:
    """DN-011: Thủ tục đăng ký thay đổi nội dung Đăng ký doanh nghiệp"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend([
        FormField(
            name="business_name_vn",
            label="Tên Doanh nghiệp hiện tại",
            field_type=FieldType.text,
            required=True,
            section="business",
        ),
        FormField(
            name="business_code",
            label="Mã số doanh nghiệp",
            field_type=FieldType.text,
            required=True,
            section="business",
        ),
        FormField(
            name="change_items",
            label="Nội dung đăng ký thay đổi",
            field_type=FieldType.select,
            required=True,
            default_value="capital",
            options=[
                FormFieldOption(value="name", label="Thay đổi tên doanh nghiệp"),
                FormFieldOption(value="address", label="Thay đổi địa chỉ trụ sở chính"),
                FormFieldOption(value="business_lines", label="Thay đổi ngành, nghề kinh doanh"),
                FormFieldOption(value="capital", label="Thay đổi vốn điều lệ (Tăng / Giảm vốn)"),
                FormFieldOption(value="representative", label="Thay đổi người đại diện theo pháp luật"),
            ],
            section="change",
        ),
        FormField(
            name="change_content_before",
            label="Nội dung trước khi thay đổi",
            field_type=FieldType.textarea,
            required=True,
            section="change",
        ),
        FormField(
            name="change_content_after",
            label="Nội dung sau khi thay đổi",
            field_type=FieldType.textarea,
            required=True,
            section="change",
        ),
    ])
    return fields


def get_fields_dn_012() -> List[FormField]:
    """DN-012: Kê khai thuế giá trị gia tăng theo phương pháp khấu trừ (Mẫu 01/GTGT)"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend([
        FormField(
            name="business_name_vn",
            label="Tên người nộp thuế (Doanh nghiệp / Tổ chức)",
            field_type=FieldType.text,
            required=True,
            placeholder="CÔNG TY TNHH...",
            section="business",
        ),
        FormField(
            name="business_code",
            label="Mã số thuế doanh nghiệp",
            field_type=FieldType.text,
            required=True,
            placeholder="0312345678",
            section="business",
        ),
        FormField(
            name="headquarters_address",
            label="Địa chỉ trụ sở người nộp thuế",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Số nhà, đường/phố, phường/xã, quận/huyện, tỉnh/thành phố",
            section="business",
        ),
        FormField(
            name="headquarters_phone",
            label="Điện thoại liên hệ người nộp thuế",
            field_type=FieldType.text,
            required=True,
            placeholder="02838123456",
            section="business",
        ),
        FormField(
            name="tax_period_type",
            label="Hình thức kỳ tính thuế GTGT",
            field_type=FieldType.select,
            required=True,
            default_value="quarterly",
            options=[
                FormFieldOption(value="quarterly", label="Kê khai theo Quý"),
                FormFieldOption(value="monthly", label="Kê khai theo Tháng"),
            ],
            section="tax",
        ),
        FormField(
            name="tax_period_label",
            label="Kỳ tính thuế cụ thể",
            field_type=FieldType.text,
            required=True,
            placeholder="Quý IV năm 2026 (hoặc Tháng 11 năm 2026)",
            section="tax",
        ),
        FormField(
            name="declaration_type",
            label="Loại tờ khai",
            field_type=FieldType.select,
            required=True,
            default_value="first_time",
            options=[
                FormFieldOption(value="first_time", label="Tờ khai lần đầu"),
                FormFieldOption(value="supplementary", label="Tờ khai bổ sung"),
            ],
            section="tax",
        ),
        FormField(
            name="prev_period_deductible_vat",
            label="Chỉ tiêu [22] - Thuế GTGT còn được khấu trừ kỳ trước chuyển sang (VNĐ)",
            field_type=FieldType.number,
            required=True,
            default_value=0,
            section="tax",
        ),
        FormField(
            name="purchased_goods_value",
            label="Chỉ tiêu [23] - Giá trị hàng hóa, dịch vụ mua vào trong kỳ (chưa thuế GTGT, VNĐ)",
            field_type=FieldType.number,
            required=True,
            placeholder="150000000",
            section="tax",
        ),
        FormField(
            name="purchased_goods_vat",
            label="Chỉ tiêu [24] - Thuế GTGT của hàng hóa, dịch vụ mua vào được khấu trừ (VNĐ)",
            field_type=FieldType.number,
            required=True,
            placeholder="15000000",
            section="tax",
        ),
        FormField(
            name="sold_goods_total_revenue",
            label="Chỉ tiêu [34] - Tổng doanh thu hàng hóa, dịch vụ bán ra chịu thuế GTGT (VNĐ)",
            field_type=FieldType.number,
            required=True,
            placeholder="250000000",
            section="tax",
        ),
        FormField(
            name="sold_goods_total_vat",
            label="Chỉ tiêu [35] - Tổng thuế GTGT của hàng hóa, dịch vụ bán ra phát sinh trong kỳ (VNĐ)",
            field_type=FieldType.number,
            required=True,
            placeholder="25000000",
            section="tax",
        ),
        FormField(
            name="payable_or_deductible_vat",
            label="Chỉ tiêu [40]/[43] - Thuế GTGT còn phải nộp hoặc còn được khấu trừ chuyển kỳ sau (VNĐ)",
            field_type=FieldType.number,
            required=True,
            placeholder="10000000",
            section="tax",
        ),
    ])
    return fields


def get_fields_htx_001() -> List[FormField]:
    """HTX-001: Đăng ký chi nhánh, văn phòng đại diện hoặc địa điểm kinh doanh của hợp tác xã"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend([
        FormField(
            name="business_name_vn",
            label="Tên Hợp tác xã / Liên hiệp hợp tác xã (viết chữ in hoa)",
            field_type=FieldType.text,
            required=True,
            placeholder="HỢP TÁC XÃ NÔNG NGHIỆP...",
            section="business",
        ),
        FormField(
            name="business_code",
            label="Mã số hợp tác xã / Mã số thuế",
            field_type=FieldType.text,
            required=True,
            placeholder="0101234567",
            section="business",
        ),
        FormField(
            name="headquarters_address",
            label="Địa chỉ trụ sở chính của Hợp tác xã",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Số nhà, đường/thôn, xã/phường, quận/huyện, tỉnh/thành phố",
            section="business",
        ),
        FormField(
            name="headquarters_phone",
            label="Số điện thoại liên hệ của Hợp tác xã",
            field_type=FieldType.text,
            required=True,
            placeholder="0912345678",
            section="business",
        ),
        FormField(
            name="coop_unit_type",
            label="Loại đơn vị phụ thuộc đăng ký hoạt động",
            field_type=FieldType.select,
            required=True,
            default_value="branch",
            options=[
                FormFieldOption(value="branch", label="Đăng ký thành lập Chi nhánh của Hợp tác xã"),
                FormFieldOption(value="rep_office", label="Đăng ký thành lập Văn phòng đại diện của Hợp tác xã"),
                FormFieldOption(value="business_location", label="Đăng ký lập Địa điểm kinh doanh của Hợp tác xã"),
            ],
            section="request",
        ),
        FormField(
            name="coop_unit_name_vn",
            label="Tên Chi nhánh / Văn phòng đại diện / Địa điểm kinh doanh (viết chữ in hoa)",
            field_type=FieldType.text,
            required=True,
            placeholder="CHI NHÁNH HỢP TÁC XÃ...",
            section="request",
        ),
        FormField(
            name="coop_unit_address",
            label="Địa chỉ Chi nhánh / Văn phòng đại diện / Địa điểm kinh doanh",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Số nhà, đường/thôn, xã/phường, quận/huyện, tỉnh/thành phố",
            section="request",
        ),
        FormField(
            name="coop_unit_head_name",
            label="Họ và tên người đứng đầu Chi nhánh / VPĐD / Địa điểm kinh doanh",
            field_type=FieldType.text,
            required=True,
            placeholder="NGUYỄN VĂN B",
            section="request",
        ),
        FormField(
            name="coop_unit_head_id",
            label="Số CCCD / Định danh cá nhân của người đứng đầu",
            field_type=FieldType.text,
            required=True,
            placeholder="001095012345",
            validation_regex="^[0-9]{9,12}$",
            section="request",
        ),
        FormField(
            name="coop_unit_business_lines",
            label="Nội dung hoạt động / Ngành nghề kinh doanh của đơn vị phụ thuộc",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Tiêu thụ nông sản, cung ứng vật tư nông nghiệp, dịch vụ hỗ trợ thành viên...",
            section="request",
        ),
    ])
    return fields


def get_fields_bhxh_001() -> List[FormField]:
    """BHXH-001: Ủy quyền thực hiện thủ tục bảo hiểm xã hội (Mẫu 13-HSB)"""
    fields = get_applicant_block(prefix="authorizer", section="authorizer")
    fields.extend(get_applicant_block(prefix="authorized_person", section="authorized_person"))
    fields.extend([
        FormField(
            name="authorizer_social_insurance_code",
            label="Mã số Bảo hiểm xã hội của người ủy quyền",
            field_type=FieldType.text,
            required=True,
            placeholder="7912345678",
            section="scope",
        ),
        FormField(
            name="authorizer_health_card_number",
            label="Số thẻ Bảo hiểm y tế / Số sổ lương hưu (nếu có)",
            field_type=FieldType.text,
            required=False,
            placeholder="DN4797912345678",
            section="scope",
        ),
        FormField(
            name="authorization_scope",
            label="Nội dung ủy quyền thực hiện thủ tục BHXH / BHYT / BHTN",
            field_type=FieldType.textarea,
            required=True,
            default_value="Thay mặt tôi nộp hồ sơ, nhận kết quả giải quyết chế độ bảo hiểm xã hội và nhận tiền lương hưu / trợ cấp BHXH theo quy định.",
            section="scope",
        ),
        FormField(
            name="authorization_from_date",
            label="Thời hạn ủy quyền từ ngày",
            field_type=FieldType.date,
            required=True,
            section="scope",
        ),
        FormField(
            name="authorization_to_date",
            label="Thời hạn ủy quyền đến ngày",
            field_type=FieldType.date,
            required=True,
            section="scope",
        ),
        FormField(
            name="certifying_authority",
            label="Cơ quan chứng thực chữ ký người ủy quyền",
            field_type=FieldType.text,
            required=True,
            default_value="Ủy ban nhân dân cấp xã / Văn phòng Công chứng",
            section="scope",
        ),
    ])
    return fields


# ==============================================================================
# HÀM TRUNG GIAN (DISPATCHER FUNCTION)
# ==============================================================================

PROCEDURE_FIELD_DISPATCHER: Dict[str, Callable[[], List[FormField]]] = {
    # Đất đai & Xây dựng
    "DD-001": get_fields_dd_001,
    "DD-002": get_fields_dd_002,
    "DD-003": get_fields_dd_003,
    "DD-004": get_fields_dd_004,
    "DD-005": get_fields_dd_005,
    "DD-006": get_fields_dd_006,
    "DD-007": get_fields_dd_007,
    "DD-008": get_fields_dd_008,
    "DD-009": get_fields_dd_009,
    "DD-010": get_fields_dd_010,
    "DD-011": get_fields_dd_011,
    "DD-012": get_fields_dd_012,
    "DD-013": get_fields_dd_013,
    "DD-014": get_fields_dd_014,
    "DD-015": get_fields_dd_015,
    "DD-016": get_fields_dd_016,
    "DD-017": get_fields_dd_017,
    "DD-018": get_fields_dd_018,
    "DD-019": get_fields_dd_019,
    "DD-020": get_fields_dd_020,
    "DD-021": get_fields_dd_021,
    "DD-022": get_fields_dd_022,
    # Kinh doanh, Hợp tác xã & Bảo hiểm xã hội
    "HKD-001": get_fields_hkd_001,
    "HKD-002": get_fields_hkd_002,
    "HKD-003": get_fields_hkd_003,
    "DN-001": get_fields_dn_001,
    "DN-002": get_fields_dn_002,
    "DN-003": get_fields_dn_003,
    "DN-004": get_fields_dn_004,
    "DN-005": get_fields_dn_005,
    "DN-006": get_fields_dn_006,
    "DN-007": get_fields_dn_007,
    "DN-008": get_fields_dn_008,
    "DN-009": get_fields_dn_009,
    "DN-010": get_fields_dn_010,
    "DN-011": get_fields_dn_011,
    "DN-012": get_fields_dn_012,
    "HTX-001": get_fields_htx_001,
    "BHXH-001": get_fields_bhxh_001,
}

PROCEDURE_METADATA: Dict[str, Dict[str, str]] = {
    "DD-001": {"name": "Đăng ký biến động quyền sử dụng đất do chuyển nhượng", "domain": "land", "template": "Thủ tục đăng ký biến động quyền sử dụng đất do chuyển nhượng.pdf"},
    "DD-002": {"name": "Cấp đổi Giấy chứng nhận quyền sử dụng đất", "domain": "land", "template": "Thủ tục cấp đổi Giấy chứng nhận quyền sử dụng đất - Mẫu đơn xin đo đạc lại đất.doc"},
    "DD-003": {"name": "Tách thửa hoặc hợp thửa đất", "domain": "land", "template": "Thủ tục tách thửa hoặc hợp thửa đất.pdf"},
    "DD-004": {"name": "Thủ tục đăng ký đất đai, cấp Giấy chứng nhận lần đầu", "domain": "land", "template": "Thủ tục đăng ký đất đai, cấp Giấy chứng nhận lần đầu.pdf"},
    "DD-005": {"name": "Thủ tục cấp lại Giấy chứng nhận do bị mất", "domain": "land", "template": None},
    "DD-006": {"name": "Thủ tục đăng ký biến động do tặng cho, thừa kế quyền sử dụng đất, nhà ở", "domain": "land", "template": "Thủ tục đăng ký biến động do tặng cho, thừa kế - Hợp đồng tặng cho quyền sử dụng đất.doc"},
    "DD-007": {"name": "Thủ tục đính chính Giấy chứng nhận đã cấp do sai sót thông tin", "domain": "land", "template": None},
    "DD-008": {"name": "Thủ tục chuyển mục đích sử dụng đất", "domain": "land", "template": "Thủ tục chuyển mục đích sử dụng đất - Đơn đề nghị chuyển mục đích sử dụng đất.pdf"},
    "DD-009": {"name": "Thủ tục gia hạn thời hạn sử dụng đất", "domain": "land", "template": "Thủ tục gia hạn thời hạn sử dụng đất - Đơn xin gia hạn sử dụng đất.doc"},
    "DD-010": {"name": "Thủ tục đăng ký thế chấp quyền sử dụng đất, tài sản gắn liền với đất", "domain": "land", "template": "Thủ tục đăng ký thế chấp quyền sử dụng đất - Đơn đăng ký thế chấp.doc"},
    "DD-011": {"name": "Thủ tục xóa đăng ký thế chấp quyền sử dụng đất (Giải chấp Sổ đỏ)", "domain": "land", "template": "Thủ tục xóa đăng ký thế chấp quyền sử dụng đất (Giải chấp Sổ đỏ).doc"},
    "DD-012": {"name": "Thủ tục cung cấp thông tin, dữ liệu đất đai, trích lục bản đồ địa chính", "domain": "land", "template": None},
    "DD-013": {"name": "Giao đất, thuê đất, chuyển mục đích sử dụng đất", "domain": "land", "template": "Đơn đề nghị giao đất, thuê đất, chuyển mục đích sử dụng đất (Mẫu số 02d).docx"},
    "DD-014": {"name": "Hòa giải tranh chấp đất đai", "domain": "land", "template": "Đơn đề nghị hòa giải tranh chấp đất đai.docx"},
    "DD-015": {"name": "Yêu cầu đo đạc địa chính, tách thửa phục vụ đăng ký đất đai", "domain": "land", "template": "Đơn yêu cầu đo đạc đất.docx"},
    "DD-016": {"name": "Xác nhận lại thời hạn sử dụng đất nông nghiệp của hộ gia đình, cá nhân", "domain": "land", "template": "Đơn xin xác nhận lại thời hạn sử dụng đất nông nghiệp (Mẫu số 09).docx"},
    "DD-017": {"name": "Chuyển hình thức giao đất, cho thuê đất", "domain": "land", "template": "Đơn đề nghị chuyển hình thức giao đất, cho thuê đất (Mẫu số 02).docx"},
    "DD-018": {"name": "Thủ tục cấp Giấy phép xây dựng mới đối với nhà ở riêng lẻ", "domain": "land", "template": "Đơn đề nghị cấp Giấy phép xây dựng mới đối với nhà ở riêng lẻ (Mẫu số 01).pdf"},
    "DD-019": {"name": "Thủ tục cấp Giấy phép sửa chữa, cải tạo công trình nhà ở riêng lẻ", "domain": "land", "template": "Đơn đề nghị cấp Giấy phép sửa chữa, cải tạo nhà ở riêng lẻ (Mẫu số 01).pdf"},
    "DD-020": {"name": "38. Mẫu biên bản nghiệm thu công việc xây dựng", "domain": "land", "template": "38. Mẫu biên bản nghiệm thu công việc xây dựng.pdf"},
    "DD-021": {"name": "37.Mẫu đơn Giấy phép sửa chữa, cải tạo công trình", "domain": "land", "template": "37.Mẫu đơn Giấy phép sửa chữa, cải tạo công trình.pdf"},
    "DD-022": {"name": "Phương án sử dụng tầng đất mặt", "domain": "land", "template": "Phương án sử dụng tầng đất mặt.docx"},
    "HKD-001": {"name": "Đăng ký thành lập hộ kinh doanh", "domain": "business", "template": "Thủ tục đăng ký thành lập Hộ kinh doanh.doc"},
    "HKD-002": {"name": "Thay đổi nội dung đăng ký hộ kinh doanh", "domain": "business", "template": "Thủ tục đăng ký thay đổi nội dung Đăng ký Hộ kinh doanh.doc"},
    "HKD-003": {"name": "Cấp lại Giấy chứng nhận đăng ký hộ kinh doanh", "domain": "business", "template": "Giấy đề nghị cấp lại Giấy chứng nhận đăng ký hộ kinh doanh.docx"},
    "DN-001": {"name": "Thủ tục ủy quyền thực hiện thủ tục đăng ký doanh nghiệp", "domain": "business", "template": "Thủ tục ủy quyền thực hiện thủ tục đăng ký doanh nghiệp.docx"},
    "DN-002": {"name": "Thủ tục đề nghị cấp lại Giấy chứng nhận đăng ký doanh nghiệp", "domain": "business", "template": "Thủ tục đề nghị cấp lại Giấy chứng nhận đăng ký doanh nghiệp.docx"},
    "DN-003": {"name": "Thủ tục đăng ký thành lập Công ty Cổ phần", "domain": "business", "template": "Thủ tục đăng ký thành lập Công ty Cổ phần.pdf"},
    "DN-004": {"name": "Thủ tục đăng ký thành lập Công ty Trách nhiệm hữu hạn", "domain": "business", "template": "Thủ tục đăng ký thành lập Công ty Trách nhiệm hữu hạn.pdf"},
    "DN-005": {"name": "Thủ tục đăng ký thành lập Doanh nghiệp tư nhân", "domain": "business", "template": "Thủ tục đăng ký thành lập Doanh nghiệp tư nhân.pdf"},
    "DN-006": {"name": "Thủ tục thuê nhà/mặt bằng làm Trụ sở chính doanh nghiệp", "domain": "business", "template": "Thủ tục thuê nhà - mặt bằng làm Trụ sở chính doanh nghiệp.docx"},
    "DN-007": {"name": "Thủ tục đăng ký thuế, khai thuế ban đầu", "domain": "business", "template": "Thủ tục đăng ký thuế, khai thuế ban đầu.doc"},
    "DN-008": {"name": "Thủ tục đăng ký thành lập Công ty Hợp danh", "domain": "business", "template": "Thủ tục đăng ký thành lập Công ty Hợp danh.pdf"},
    "DN-009": {"name": "Thủ tục đăng ký giải thể doanh nghiệp", "domain": "business", "template": "Thủ tục đăng ký giải thể doanh nghiệp.doc"},
    "DN-010": {"name": "Thủ tục thông báo tạm ngừng / Cấp đổi thông tin ĐKDN", "domain": "business", "template": "Thủ tục thông báo tạm ngừng - Cấp đổi thông tin ĐKDN.docx"},
    "DN-011": {"name": "Thủ tục đăng ký thay đổi nội dung Đăng ký doanh nghiệp", "domain": "business", "template": "Thủ tục đăng ký thay đổi nội dung Đăng ký doanh nghiệp.docx"},
    "DN-012": {"name": "Kê khai thuế giá trị gia tăng theo phương pháp khấu trừ (Mẫu 01/GTGT)", "domain": "business", "template": "Tờ khai thuế giá trị gia tăng theo phương pháp khấu trừ (01/GTGT).docx"},
    "HTX-001": {"name": "Đăng ký chi nhánh, văn phòng đại diện hoặc địa điểm kinh doanh của hợp tác xã", "domain": "business", "template": "Giấy đề nghị đăng ký chi nhánh, văn phòng đại diện hoặc địa điểm kinh doanh của hợp tác xã.pdf"},
    "BHXH-001": {"name": "Ủy quyền thực hiện thủ tục bảo hiểm xã hội (Mẫu 13-HSB)", "domain": "business", "template": "Giấy ủy quyền thực hiện thủ tục bảo hiểm xã hội (Mẫu 13-HSB).pdf"},
}


def _convert_dynamic_template_to_schema(row_dict: dict, content: dict, code_upper: str) -> ProcedureFormSchema:
    """Convert a published DB template into the same schema used by static forms."""
    domain_value = row_dict.get("domain") or content.get("domain") or "dat_dai"
    domain = "land" if domain_value in ("land", "dat_dai") else "business"
    target_table = "land_procedure_records" if domain == "land" else "business_procedure_records"
    name = row_dict.get("name") or content.get("name") or f"Thủ tục {code_upper}"
    sections: List[FormSection] = []
    fields: List[FormField] = []

    for section_index, section in enumerate(content.get("sections") or []):
        section_key = str(section.get("id") or f"section_{section_index + 1}")
        sections.append(FormSection(key=section_key, title=section.get("title") or f"Mục {section_index + 1}", order=section_index + 1))
        for block_index, block in enumerate(section.get("blocks") or []):
            instance_id = block.get("instance_id") or f"block_{block_index + 1}"
            block_fields = block.get("fields") or []
            if block.get("block_key") == "free_field" and not block_fields:
                block_fields = [{
                    "key": "value",
                    "label": block.get("label") or "Trường tự do",
                    "type": block.get("type", "text"),
                    "required": block.get("required", False),
                }]

            for field_index, field in enumerate(block_fields):
                raw_key = field.get("key") or field.get("name") or f"field_{field_index + 1}"
                field_name = f"{instance_id}.{raw_key}" if instance_id and "." not in raw_key else raw_key
                field_type_name = str(field.get("type", "text")).lower()
                field_type = {
                    "textarea": FieldType.textarea,
                    "date": FieldType.date,
                    "dob": FieldType.date,
                    "issue_date": FieldType.date,
                    "past_date": FieldType.date,
                    "number": FieldType.number,
                    "select": FieldType.select,
                    "checkbox": FieldType.checkbox,
                    "radio": FieldType.radio,
                }.get(field_type_name, FieldType.text)
                options = None
                if isinstance(field.get("options"), list):
                    options = [
                        FormFieldOption(
                            value=str(option.get("value", option)) if isinstance(option, dict) else str(option),
                            label=str(option.get("label", option.get("value", option))) if isinstance(option, dict) else str(option),
                        )
                        for option in field["options"]
                    ]
                excluded_from_entry = is_submission_recipient(
                    {**field, "name": field_name}, block.get("block_key")
                )
                fields.append(FormField(
                    name=field_name,
                    label=field.get("label") or raw_key,
                    field_type=field_type,
                    required=bool(field.get("required", False)) and not excluded_from_entry,
                    excluded_from_entry=excluded_from_entry,
                    placeholder=field.get("placeholder"),
                    options=options,
                    section=section_key,
                ))

    return ProcedureFormSchema(
        procedure_code=code_upper,
        procedure_name=name,
        domain=domain,
        target_table=target_table,
        template_filename=None,
        sections=sections,
        fields=fields,
    )


def get_dynamic_form_schema(procedure_code: str) -> Optional[ProcedureFormSchema]:
    """Return the latest published DB form, or None so static forms remain the fallback."""
    try:
        from app.database import get_engine
        from sqlalchemy import text

        engine = get_engine()
        if not engine:
            return None
        code_upper = procedure_code.strip().upper()
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT * FROM public.dynamic_form_templates
                WHERE (UPPER(procedure_id) = :code OR UPPER(code) = :code
                       OR procedure_id = (SELECT CAST(id AS text) FROM public.procedures
                                          WHERE UPPER(code) = :code LIMIT 1))
                  AND status = 'published'
                  AND deleted_at IS NULL
                ORDER BY version DESC, updated_at DESC, id DESC
                LIMIT 1
            """), {"code": code_upper}).fetchone()
            if not row:
                return None
            record = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
            content = record.get("content")
            if isinstance(content, str):
                content = json.loads(content)
            if not isinstance(content, dict) or not content.get("sections"):
                return None
            return _convert_dynamic_template_to_schema(record, content, code_upper)
    except Exception as exc:
        # A missing/unmigrated dynamic table must not break the existing static forms.
        logger.info("Dynamic form unavailable for %s; using static schema: %s", procedure_code, exc)
        return None


def get_procedure_form_schema(procedure_code: str) -> ProcedureFormSchema:
    """
    Ưu tiên mẫu động đã publish; nếu không có thì dùng schema tĩnh local.
    """
    code_upper = procedure_code.strip().upper()
    dynamic_schema = get_dynamic_form_schema(code_upper)
    if dynamic_schema and dynamic_schema.fields:
        return dynamic_schema

    handler = PROCEDURE_FIELD_DISPATCHER.get(code_upper)
    if not handler:
        raise ValueError(f"Không tìm thấy cấu hình biểu mẫu cho mã thủ tục: '{procedure_code}'")

    meta = PROCEDURE_METADATA.get(code_upper, {
        "name": f"Thủ tục {code_upper}",
        "domain": "land" if code_upper.startswith("DD") else "business",
        "template": None,
    })

    domain = meta["domain"]
    target_table = "land_procedure_records" if domain == "land" else "business_procedure_records"

    fields = handler()
    for field in fields:
        if is_submission_recipient({"name": field.name, "label": field.label}):
            field.required = False
            field.default_value = None
            field.excluded_from_entry = True

    # Nhóm các section theo field (Loại bỏ số thứ tự cứng - L5)
    section_map = {
        "receiving": FormSection(key="receiving", title="Cơ quan tiếp nhận giải quyết", order=1),
        "applicant": FormSection(key="applicant", title="I. Người sử dụng đất, chủ sở hữu tài sản gắn liền với đất", order=2),
        "change": FormSection(key="change", title="II. Nội dung biến động", order=3),
        "tax_exemption": FormSection(key="tax_exemption", title="III. Thông tin đối tượng được miễn, giảm nghĩa vụ tài chính về đất đai", order=4),
        "attachment": FormSection(key="attachment", title="IV. Giấy tờ liên quan đến nội dung biến động nộp kèm theo đơn", order=5),
        "declaration": FormSection(key="declaration", title="Cam đoan và Xác nhận của người làm đơn", order=6),
        "authorizer": FormSection(key="authorizer", title="Thông tin người ủy quyền", order=7),
        "authorized_person": FormSection(key="authorized_person", title="Thông tin người được ủy quyền", order=8),
        "lessor": FormSection(key="lessor", title="Thông tin bên cho thuê", order=9),
        "lessee": FormSection(key="lessee", title="Thông tin bên thuê", order=10),
        "property": FormSection(key="property", title="Thông tin thửa đất / Bất động sản", order=11),
        "business": FormSection(key="business", title="Thông tin tổ chức / Doanh nghiệp", order=12),
        "governance": FormSection(key="governance", title="Cơ cấu quản lý & Người đại diện", order=13),
        "transfer": FormSection(key="transfer", title="Thông tin chuyển nhượng", order=14),
        "transaction": FormSection(key="transaction", title="Thông tin tặng cho / thừa kế", order=15),
        "conversion": FormSection(key="conversion", title="Thông tin chuyển mục đích sử dụng", order=16),
        "mortgage": FormSection(key="mortgage", title="Thông tin biện pháp bảo đảm / thế chấp", order=17),
        "release": FormSection(key="release", title="Thông tin giải chấp / xóa đăng ký", order=18),
        "suspension": FormSection(key="suspension", title="Thông tin tạm ngừng hoạt động", order=19),
        "dissolution": FormSection(key="dissolution", title="Thông tin giải thể / chấm dứt hoạt động", order=20),
        "tax": FormSection(key="tax", title="Thông tin đăng ký và kê khai thuế", order=21),
        "financial": FormSection(key="financial", title="Điều khoản thanh toán & Giá thuê", order=22),
        "scope": FormSection(key="scope", title="Phạm vi và thời hạn ủy quyền", order=23),
        "origin": FormSection(key="origin", title="Nguồn gốc đất đai & Cam kết", order=24),
        "request": FormSection(key="request", title="Yêu cầu giải quyết thủ tục", order=25),
        "general": FormSection(key="general", title="Thông tin chung", order=26),
    }

    used_sections: Dict[str, FormSection] = {}
    for f in fields:
        sec = section_map.get(f.section, FormSection(key=f.section, title=f.section.capitalize()))
        used_sections[sec.key] = sec

    raw_sorted = sorted(used_sections.values(), key=lambda s: s.order)
    final_sections = []
    for idx, s in enumerate(raw_sorted):
        if re.match(r"^(I|II|III|IV|V|VI|VII|VIII|IX|X|\d+)\.", s.title):
            title = s.title
        else:
            title = f"{idx + 1}. {s.title}"
        final_sections.append(
            FormSection(
                key=s.key,
                title=title,
                description=s.description,
                order=idx + 1,
            )
        )

    return ProcedureFormSchema(
        procedure_code=code_upper,
        procedure_name=meta["name"],
        domain=domain,
        target_table=target_table,
        template_filename=meta.get("template"),
        sections=final_sections,
        fields=fields,
    )


def get_entry_form_schema(procedure_code: str) -> ProcedureFormSchema:
    """Expose input fields while keeping legacy fields in the validation schema."""
    schema = get_procedure_form_schema(procedure_code)
    fields = [field for field in schema.fields if not field.excluded_from_entry]
    used_sections = {field.section for field in fields}
    sections = []
    for section in schema.sections:
        if section.key not in used_sections:
            continue
        order = len(sections) + 1
        title = section.title
        if title.startswith(f"{section.order}. "):
            title = f"{order}. {title.split('. ', 1)[1]}"
        sections.append(section.model_copy(update={"order": order, "title": title}))
    return schema.model_copy(update={"fields": fields, "sections": sections})


# ==============================================================================
# KIỂM TRA form_data THEO SCHEMA (khi lưu hồ sơ, cả nháp lẫn nộp thật)
# ==============================================================================

def _is_empty(field: FormField, value) -> bool:
    # Ô cam kết (checkbox) bắt buộc: chưa tích coi như chưa điền.
    if field.field_type == FieldType.checkbox:
        return value is not True
    return value is None or (isinstance(value, str) and not value.strip())


def _format_error(field: FormField, value) -> Optional[str]:
    kind = field.field_type
    if field.options and kind in (FieldType.select, FieldType.radio):
        if value not in {o.value for o in field.options}:
            return "Giá trị không nằm trong danh sách lựa chọn."
    elif kind == FieldType.number:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "Phải là số."
    elif kind == FieldType.date:
        try:
            date.fromisoformat(value)
        except (TypeError, ValueError):
            return "Ngày phải theo dạng YYYY-MM-DD."
    elif kind == FieldType.checkbox:
        if not isinstance(value, bool):
            return "Phải là true hoặc false."
    elif not isinstance(value, str):
        return "Phải là chuỗi ký tự."
    if field.validation_regex and not re.fullmatch(field.validation_regex, str(value)):
        return "Sai định dạng."
    return None


def validate_form_data(schema: ProcedureFormSchema, form_data: Dict[str, Any]) -> List[Dict[str, str]]:
    """Lỗi theo từng ô [{field, message}]; rỗng khi hợp lệ. Ô bắt buộc phải có giá trị, ô lạ bị từ chối."""
    fields = {f.name: f for f in schema.fields}
    errors = [{"field": name, "message": "Ô này không có trong biểu mẫu của thủ tục."}
              for name in form_data if name not in fields]
    for field in schema.fields:
        value = form_data.get(field.name)
        if _is_empty(field, value):
            if field.required:
                errors.append({"field": field.name, "message": "Đây là ô bắt buộc."})
            continue
        message = _format_error(field, value)
        if message:
            errors.append({"field": field.name, "message": message})
    return errors
