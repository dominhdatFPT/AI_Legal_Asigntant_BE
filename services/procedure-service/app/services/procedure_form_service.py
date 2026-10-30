import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from app.schemas.form_schema import (
    FieldType,
    FormField,
    FormFieldOption,
    FormSection,
    ProcedureFormSchema,
)

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
            validation_regex="^[0-9]{12}$",
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
            validation_regex="^0[35789][0-9]{8}$",
        ),
        FormField(
            name=f"{prefix}_email",
            label="Địa chỉ email",
            field_type=FieldType.text,
            required=False,
            placeholder="example@gmail.com",
            section=section,
            validation_regex=r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$",
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
            validation_regex="^0[0-9]{9,10}$",
        ),
        FormField(
            name="headquarters_email",
            label="Email trụ sở",
            field_type=FieldType.text,
            required=False,
            placeholder="contact@company.vn",
            section=section,
            validation_regex=r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$",
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
            validation_regex="^0[35789][0-9]{8}$",
        ),
        FormField(
            name="applicant_email",
            label="Hộp thư điện tử (nếu có)",
            field_type=FieldType.text,
            required=False,
            placeholder="example@gmail.com",
            section="applicant",
            validation_regex=r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$",
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
            validation_regex="^0[0-9]{9,10}$",
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
    """HKD-003: Chấm dứt hoạt động hộ kinh doanh"""
    fields = get_applicant_block(prefix="applicant", section="applicant")
    fields.extend([
        FormField(
            name="business_name_vn",
            label="Tên Hộ kinh doanh chấm dứt",
            field_type=FieldType.text,
            required=True,
            placeholder="HỘ KINH DOANH HOÀNG PHÁT",
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
            name="headquarters_address",
            label="Địa chỉ địa điểm kinh doanh",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Số nhà, đường/phố, phường/xã, quận/huyện, tỉnh/thành phố",
            section="business",
        ),
        FormField(
            name="termination_reason",
            label="Lý do chấm dứt hoạt động",
            field_type=FieldType.textarea,
            required=True,
            placeholder="Kinh doanh không hiệu quả / Chuyển hướng kinh doanh...",
            section="dissolution",
        ),
        FormField(
            name="tax_clearance_status",
            label="Tình trạng nghĩa vụ thuế",
            field_type=FieldType.select,
            required=True,
            default_value="completed",
            options=[
                FormFieldOption(value="completed", label="Đã hoàn thành toàn bộ nghĩa vụ nộp thuế"),
                FormFieldOption(value="committed", label="Cam kết thanh toán toàn bộ nợ thuế phát sinh"),
            ],
            section="dissolution",
        ),
        FormField(
            name="termination_date",
            label="Thời điểm dự kiến chấm dứt hoạt động",
            field_type=FieldType.date,
            required=True,
            section="dissolution",
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


# ==============================================================================
# HÀM TRUNG GIAN (DISPATCHER FUNCTION)
# ==============================================================================

PROCEDURE_FIELD_DISPATCHER: Dict[str, Callable[[], List[FormField]]] = {
    # 12 Đất đai
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
    # 13 Kinh doanh
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
    "HKD-001": {"name": "Đăng ký thành lập hộ kinh doanh", "domain": "business", "template": "Thủ tục đăng ký thành lập Hộ kinh doanh.doc"},
    "HKD-002": {"name": "Thay đổi nội dung đăng ký hộ kinh doanh", "domain": "business", "template": "Thủ tục đăng ký thay đổi nội dung Đăng ký Hộ kinh doanh.doc"},
    "HKD-003": {"name": "Chấm dứt hoạt động hộ kinh doanh", "domain": "business", "template": "Thủ tục chấm dứt hoạt động Hộ kinh doanh.doc"},
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
}


def convert_dynamic_template_to_schema(row_dict: dict, content: dict, code_upper: str) -> ProcedureFormSchema:
    domain_val = row_dict.get("domain") or content.get("domain") or "dat_dai"
    domain = "land" if domain_val in ("land", "dat_dai") else "business"
    target_table = "land_procedure_records" if domain == "land" else "business_procedure_records"
    name = row_dict.get("name") or content.get("name") or f"Thủ tục {code_upper}"

    sections_data = content.get("sections") or []
    final_sections: List[FormSection] = []
    final_fields: List[FormField] = []

    for s_idx, sec in enumerate(sections_data):
        sec_id = str(sec.get("id") or f"sec_{s_idx + 1}")
        sec_title = sec.get("title") or f"Mục {s_idx + 1}"
        final_sections.append(
            FormSection(
                key=sec_id,
                title=sec_title,
                order=s_idx + 1,
            )
        )

        blocks = sec.get("blocks") or []
        for b_idx, blk in enumerate(blocks):
            inst_id = blk.get("instance_id") or f"blk_{b_idx + 1}"
            b_key = blk.get("block_key", "")
            f_list = blk.get("fields") or []

            if b_key == "free_field" and not f_list:
                f_list = [{
                    "key": "value",
                    "label": blk.get("label") or "Trường tự do",
                    "type": blk.get("type", "text"),
                    "required": blk.get("required", False)
                }]

            for f_idx, fld in enumerate(f_list):
                raw_key = fld.get("key") or fld.get("name") or f"field_{f_idx + 1}"
                field_name = f"{inst_id}.{raw_key}" if (inst_id and "." not in raw_key) else raw_key
                label = fld.get("label") or raw_key
                f_type_str = str(fld.get("type", "text")).lower()

                if f_type_str == "textarea":
                    field_type = FieldType.textarea
                elif f_type_str in ("date", "dob", "issue_date", "past_date"):
                    field_type = FieldType.date
                elif f_type_str == "number":
                    field_type = FieldType.number
                elif f_type_str == "select":
                    field_type = FieldType.select
                elif f_type_str == "checkbox":
                    field_type = FieldType.checkbox
                elif f_type_str == "radio":
                    field_type = FieldType.radio
                else:
                    field_type = FieldType.text

                options = None
                if fld.get("options") and isinstance(fld["options"], list):
                    options = [FormFieldOption(value=str(o), label=str(o)) for o in fld["options"]]

                required = bool(fld.get("required", False))
                placeholder = fld.get("placeholder")

                final_fields.append(
                    FormField(
                        name=field_name,
                        label=label,
                        field_type=field_type,
                        required=required,
                        placeholder=placeholder,
                        options=options,
                        section=sec_id,
                    )
                )

    return ProcedureFormSchema(
        procedure_code=code_upper,
        procedure_name=name,
        domain=domain,
        target_table=target_table,
        template_filename=None,
        sections=final_sections,
        fields=final_fields,
    )


def get_dynamic_form_schema(procedure_code: str) -> Optional[ProcedureFormSchema]:
    """
    Kiểm tra xem thủ tục có mẫu đơn dùng chung (Dynamic Form Template)
    đã được Admin xuất bản (published) hay không.
    Nếu có, chuyển đổi cấu trúc sections & blocks của mẫu đơn thành ProcedureFormSchema
    để ưu tiên trả về cho người dân điền đơn.
    """
    try:
        from app.database import get_engine
        from sqlalchemy import text
        import json
        engine = get_engine()
        if not engine:
            return None

        code_upper = procedure_code.strip().upper()
        with engine.connect() as conn:
            proc_id_str = None
            try:
                proc_row = conn.execute(
                    text("SELECT id FROM public.procedures WHERE UPPER(code) = :code LIMIT 1"),
                    {"code": code_upper}
                ).fetchone()
                if proc_row:
                    proc_id_str = str(proc_row[0])
            except Exception:
                pass

            params = {"code": code_upper}
            where_proc = "(UPPER(procedure_id) = :code OR UPPER(code) = :code"
            if proc_id_str:
                params["proc_id"] = proc_id_str
                where_proc += " OR procedure_id = :proc_id"
            where_proc += ")"

            sql = f"""
                SELECT * FROM public.dynamic_form_templates
                WHERE {where_proc}
                  AND status = 'published'
                  AND deleted_at IS NULL
                ORDER BY version DESC, updated_at DESC
                LIMIT 1
            """
            row = conn.execute(text(sql), params).fetchone()
            if not row:
                return None

            m = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
            content = m.get("content")
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except Exception:
                    content = {}
            if not isinstance(content, dict) or not content.get("sections"):
                return None

            return convert_dynamic_template_to_schema(m, content, code_upper)
    except Exception as e:
        logger.warning(f"Không thể tải dynamic form schema cho thủ tục {procedure_code}: {e}")
        return None


def get_procedure_form_schema(procedure_code: str) -> ProcedureFormSchema:
    """
    Hàm trung gian (Dispatcher): Nhận vào mã thủ tục (ví dụ: DD-001 hoặc HKD-001).
    Ưu tiên 1: Lấy schema từ Mẫu đơn dùng chung (Dynamic Form Template) do Admin tùy chỉnh & xuất bản.
    Ưu tiên 2: Fallback về schema mặc định ban đầu từ 26 hàm thủ tục cố định.
    """
    code_upper = procedure_code.strip().upper()

    # 1. Thử lấy schema từ mẫu đơn động đã xuất bản của Admin
    dynamic_schema = get_dynamic_form_schema(code_upper)
    if dynamic_schema and len(dynamic_schema.fields) > 0:
        return dynamic_schema

    # 2. Fallback về cấu hình tĩnh mặc định
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

    # Gọi hàm chi tiết
    fields = handler()

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
            d = date.fromisoformat(value)
            if any(kw in field.name for kw in ("_dob", "date_of_birth", "_issue_date")):
                vn_today = datetime.now(timezone(timedelta(hours=7))).date()
                if d > vn_today:
                    return "Ngày không được ở tương lai."
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
