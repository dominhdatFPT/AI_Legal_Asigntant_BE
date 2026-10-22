"""
Dịch vụ xuất tờ đơn hồ sơ chính thức chuẩn PDF (Server-side PDF Generation).
Sử dụng ReportLab với bộ font Liberation Serif hỗ trợ đầy đủ tiếng Việt có dấu.
Định dạng chuẩn theo Mẫu số 18 ban hành kèm theo Nghị định số 151/2025/NĐ-CP:
"ĐƠN ĐĂNG KÝ BIẾN ĐỘNG ĐẤT ĐAI, TÀI SẢN GẮN LIỀN VỚI ĐẤT".

Văn bản được dàn trang A4 chính quy, có đánh số trang tự động (NumberedCanvas),
tuyệt đối không chứa URL trình duyệt, không chứa nút bấm giao diện hay footer rác.
"""

from datetime import datetime
import html
from io import BytesIO
import os
import re
from typing import Any, Dict, List, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# Đường dẫn thư mục font
FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fonts")
_FONTS_REGISTERED = False


def _ensure_fonts_registered():
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return

    regular_path = os.path.join(FONT_DIR, "LiberationSerif-Regular.ttf")
    bold_path = os.path.join(FONT_DIR, "LiberationSerif-Bold.ttf")
    italic_path = os.path.join(FONT_DIR, "LiberationSerif-Italic.ttf")
    bold_italic_path = os.path.join(FONT_DIR, "LiberationSerif-BoldItalic.ttf")

    if not os.path.isfile(regular_path):
        sys_font_dir = "/usr/share/fonts/truetype/liberation"
        if os.path.isfile(os.path.join(sys_font_dir, "LiberationSerif-Regular.ttf")):
            regular_path = os.path.join(sys_font_dir, "LiberationSerif-Regular.ttf")
            bold_path = os.path.join(sys_font_dir, "LiberationSerif-Bold.ttf")
            italic_path = os.path.join(sys_font_dir, "LiberationSerif-Italic.ttf")
            bold_italic_path = os.path.join(sys_font_dir, "LiberationSerif-BoldItalic.ttf")

    pdfmetrics.registerFont(TTFont("LiberationSerif", regular_path))
    pdfmetrics.registerFont(TTFont("LiberationSerif-Bold", bold_path))
    pdfmetrics.registerFont(TTFont("LiberationSerif-Italic", italic_path))
    pdfmetrics.registerFont(TTFont("LiberationSerif-BoldItalic", bold_italic_path))
    _FONTS_REGISTERED = True


class NumberedCanvas(canvas.Canvas):
    """
    Canvas hai lượt (two-pass) để tự động tính tổng số trang và in chân trang chuẩn:
    'Mã hồ sơ: ... | Trang X / Y | Mẫu số 18 - Nghị định số 151/2025/NĐ-CP'
    Tuyệt đối không in URL trình duyệt hay timestamp rác.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []
        self.app_code = "HỒ SƠ ĐIỆN TỬ"
        self.template_ref = "Mẫu số 18 - Nghị định số 151/2025/NĐ-CP"

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_footer(num_pages)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)

    def draw_footer(self, total_pages: int):
        self.saveState()
        self.setFont("LiberationSerif", 8.5)
        self.setFillColor(colors.HexColor("#475569"))

        # Đường kẻ ngang trên footer
        self.setStrokeColor(colors.HexColor("#cbd5e1"))
        self.setLineWidth(0.5)
        self.line(36, 32, 595.27 - 36, 32)

        # Chân trang bên trái: Mã hồ sơ
        self.drawString(36, 20, f"Mã hồ sơ: {self.app_code}")

        # Ở giữa: Số trang X / Y
        page_text = f"Trang {self._pageNumber} / {total_pages}"
        self.drawCentredString(595.27 / 2.0, 20, page_text)

        # Bên phải: Trích dẫn quy chuẩn pháp lý
        self.drawRightString(595.27 - 36, 20, self.template_ref)
        self.restoreState()


def _format_date_vn(dt_val: Any) -> str:
    """Định dạng ngày theo văn phong hành chính: ngày ... tháng ... năm ..."""
    dt = None
    if isinstance(dt_val, str):
        try:
            cleaned = dt_val.replace("Z", "+00:00")
            dt = datetime.fromisoformat(cleaned)
        except Exception:
            dt = None
    elif isinstance(dt_val, datetime):
        dt = dt_val

    if dt:
        return f"ngày {dt.day:02d} tháng {dt.month:02d} năm {dt.year}"
    now = datetime.now()
    return f"ngày {now.day:02d} tháng {now.month:02d} năm {now.year}"


def _format_multiline_html(text: Optional[str]) -> str:
    """Chuyển đổi xuống dòng thành <br/> an toàn"""
    if not text:
        return "—"
    escaped = html.escape(str(text).strip())
    return escaped.replace("\n", "<br/>")


def generate_application_dossier_pdf(application_data: Dict[str, Any]) -> bytes:
    """
    Sinh file PDF tờ đơn chính thức từ dữ liệu tra cứu hồ sơ:
    - application_data['application_code']
    - application_data['record'] (chứa form_data dạng jsonb)
    - application_data['procedure']
    - application_data['schema']
    """
    _ensure_fonts_registered()

    app_code = application_data.get("application_code") or "HỒ SƠ ĐIỆN TỬ"
    record = application_data.get("record") or {}
    form_data = record.get("form_data") or {}
    procedure = application_data.get("procedure") or {}

    # Trích xuất dữ liệu biểu mẫu chuẩn Mẫu số 18
    receiving_agency = (
        form_data.get("receiving_agency")
        or "Chi nhánh Văn phòng Đăng ký Đất đai"
    )

    applicant_name = (
        form_data.get("applicant_name")
        or record.get("applicant_name")
        or "NGƯỜI LÀM ĐƠN"
    ).strip().upper()

    # Giấy tờ nhân thân/pháp nhân
    applicant_id_doc = form_data.get("applicant_id_document")
    if not applicant_id_doc:
        id_num = form_data.get("applicant_id_number") or record.get("applicant_id_number") or ""
        id_date = form_data.get("applicant_id_issue_date") or ""
        id_place = form_data.get("applicant_id_issue_place") or ""
        id_type = form_data.get("applicant_id_type") or "CCCD"
        parts = []
        if id_num:
            parts.append(f"{id_type} số {id_num}")
        if id_date:
            parts.append(f"cấp ngày {id_date}")
        if id_place:
            parts.append(f"tại {id_place}")
        applicant_id_doc = ", ".join(parts) if parts else "—"

    # Địa chỉ thường trú / trụ sở chính
    applicant_address = (
        form_data.get("applicant_address")
        or form_data.get("applicant_permanent_address")
        or form_data.get("applicant_current_address")
        or record.get("land_address")
        or "—"
    )

    applicant_tax_code = form_data.get("applicant_tax_code") or "—"
    applicant_phone = form_data.get("applicant_phone") or record.get("applicant_phone") or "—"
    applicant_email = form_data.get("applicant_email") or "—"

    # Mục II: Nội dung biến động
    change_content = form_data.get("change_content")
    if not change_content:
        # Tự động tổng hợp từ dữ liệu cũ nếu chưa có trường change_content trực tiếp
        plot = form_data.get("plot_number") or record.get("plot_number")
        sheet = form_data.get("map_sheet_number") or record.get("map_sheet_number")
        addr = form_data.get("land_address") or record.get("land_address")
        area = form_data.get("land_area") or record.get("land_area")
        transferee = form_data.get("transferee_name")
        tf_id = form_data.get("transferee_id_number")
        c_date = form_data.get("transfer_contract_date")
        notary = form_data.get("transfer_notary_office")

        lines = ["Đăng ký biến động quyền sử dụng đất, tài sản gắn liền với đất:"]
        if transferee:
            lines.append(f"- Bên nhận chuyển quyền: {transferee} (CCCD số: {tf_id or '—'})")
        if plot or sheet or addr:
            lines.append(f"- Thửa đất số: {plot or '—'}, Tờ bản đồ số: {sheet or '—'}, Địa chỉ: {addr or '—'}")
        if area:
            lines.append(f"- Diện tích: {area} m²")
        if c_date:
            lines.append(f"- Hợp đồng giao dịch công chứng ngày: {c_date}" + (f" tại {notary}" if notary else ""))
        lines.append("- Nhu cầu cấp Giấy chứng nhận: Có nhu cầu cấp mới Giấy chứng nhận quyền sử dụng đất.")
        change_content = "\n".join(lines)

    # Mục III: Miễn giảm tài chính
    tax_exemption_info = (
        form_data.get("tax_exemption_info")
        or "Không thuộc đối tượng miễn, giảm nghĩa vụ tài chính."
    )

    # Mục IV: Giấy tờ nộp kèm
    attached_documents = (
        form_data.get("attached_documents")
        or "1. Bản gốc Giấy chứng nhận quyền sử dụng đất đã cấp;\n"
           "2. Hợp đồng chuyển nhượng/giao dịch quyền sử dụng đất (bản chính công chứng);\n"
           "3. Bản sao Căn cước công dân của các bên tham gia giao dịch;\n"
           "4. Tờ khai thuế thu nhập cá nhân và lệ phí trước bạ."
    )

    declarer_signature_name = (
        form_data.get("declarer_signature_name")
        or applicant_name
    ).strip().upper()

    date_str = _format_date_vn(record.get("created_at"))

    # Thiết lập Document A4
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=42,
        rightMargin=42,
        topMargin=36,
        bottomMargin=45,
    )

    content_width = 595.27 - 84.0  # 511.27 pt

    # Bộ style văn bản hành chính
    body_style = ParagraphStyle(
        "ViBody",
        fontName="LiberationSerif",
        fontSize=10.5,
        leading=16,
        textColor=colors.HexColor("#0f172a"),
        alignment=4,  # Justified
    )
    body_italic = ParagraphStyle(
        "ViBodyItalic",
        parent=body_style,
        fontName="LiberationSerif-Italic",
    )
    title_style = ParagraphStyle(
        "ViTitle",
        fontName="LiberationSerif-Bold",
        fontSize=13.5,
        leading=18,
        alignment=1,  # Center
        textColor=colors.HexColor("#0f172a"),
    )
    section_head_style = ParagraphStyle(
        "ViSecHead",
        fontName="LiberationSerif-Bold",
        fontSize=11,
        leading=16,
        textColor=colors.HexColor("#0f172a"),
    )

    story = []

    # 1. QUỐC HIỆU & MẪU SỐ 18 (2 cột chuẩn thể thức văn bản hành chính)
    header_left = (
        "<font size='9'><b>Mẫu số 18</b></font><br/>"
        "<font size='8' color='#334155'><i>(Ban hành kèm theo Nghị định số 151/2025/NĐ-CP)</i></font>"
    )
    header_right = (
        "<font size='10.5'><b>CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM</b></font><br/>"
        "<font size='10'><b><i>Độc lập - Tự do - Hạnh phúc</i></b></font><br/>"
    )
    header_table = Table(
        [
            [
                Paragraph(header_left, ParagraphStyle("HL", parent=body_style, alignment=0, leading=12)),
                Paragraph(header_right, ParagraphStyle("HR", parent=body_style, alignment=1, leading=14)),
            ],
            [
                "",
                HRFlowable(width="60%", thickness=0.8, color=colors.HexColor("#0f172a"), spaceAfter=0, spaceBefore=2, hAlign="CENTER"),
            ]
        ],
        colWidths=[200, content_width - 200],
    )
    header_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story.append(header_table)
    story.append(Spacer(1, 14))

    # 2. TIÊU ĐỀ ĐƠN
    story.append(
        Paragraph("ĐƠN ĐĂNG KÝ BIẾN ĐỘNG ĐẤT ĐAI,<br/>TÀI SẢN GẮN LIỀN VỚI ĐẤT", title_style)
    )
    story.append(Spacer(1, 4))
    story.append(
        Paragraph(
            f"<font size='8.5' color='#475569'>Mã hồ sơ điện tử: <b>{html.escape(app_code)}</b></font>",
            ParagraphStyle("SubAppCode", parent=body_style, alignment=1, leading=11),
        )
    )
    story.append(Spacer(1, 12))

    # 3. KÍNH GỬI
    kinh_gui_text = f"<b>Kính gửi:</b> {html.escape(receiving_agency)}"
    story.append(Paragraph(kinh_gui_text, ParagraphStyle("KinhGui", parent=body_style, fontSize=11, leading=16, leftIndent=16)))
    story.append(Spacer(1, 12))

    # 4. MỤC I: NGƯỜI SỬ DỤNG ĐẤT, CHỦ SỞ HỮU TÀI SẢN GẮN LIỀN VỚI ĐẤT
    story.append(Paragraph("<b>I. Người sử dụng đất, chủ sở hữu tài sản gắn liền với đất:</b>", section_head_style))
    story.append(Spacer(1, 4))

    # Các dòng thông tin mục I
    sec_i_rows = [
        [
            Paragraph("<b>- Tên:</b>", ParagraphStyle("Lbl", parent=body_style, leading=15)),
            Paragraph(f"<b>{html.escape(applicant_name)}</b>", ParagraphStyle("Val", parent=body_style, leading=15)),
        ],
        [
            Paragraph("<b>- Giấy tờ nhân thân/pháp nhân:</b>", ParagraphStyle("Lbl", parent=body_style, leading=15)),
            Paragraph(html.escape(applicant_id_doc), ParagraphStyle("Val", parent=body_style, leading=15)),
        ],
        [
            Paragraph("<b>- Địa chỉ:</b>", ParagraphStyle("Lbl", parent=body_style, leading=15)),
            Paragraph(html.escape(applicant_address), ParagraphStyle("Val", parent=body_style, leading=15)),
        ],
        [
            Paragraph("<b>- Mã số thuế (nếu có):</b>", ParagraphStyle("Lbl", parent=body_style, leading=15)),
            Paragraph(html.escape(applicant_tax_code), ParagraphStyle("Val", parent=body_style, leading=15)),
        ],
        [
            Paragraph("<b>- Điện thoại liên hệ:</b>", ParagraphStyle("Lbl", parent=body_style, leading=15)),
            Paragraph(
                f"{html.escape(applicant_phone)} &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; "
                f"<b>Hộp thư điện tử (nếu có):</b> {html.escape(applicant_email)}",
                ParagraphStyle("Val", parent=body_style, leading=15),
            ),
        ],
    ]

    sec_i_table = Table(sec_i_rows, colWidths=[175, content_width - 175])
    sec_i_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story.append(sec_i_table)
    story.append(Spacer(1, 4))
    story.append(
        Paragraph(
            "<i>(Trường hợp có nhiều đồng sử dụng, sở hữu thì kê khai thông tin một người đại diện; "
            "đồng thời lập danh sách theo Bảng 18a đính kèm)</i>",
            ParagraphStyle("NoteSecI", parent=body_italic, fontSize=8.5, textColor=colors.HexColor("#475569"), leftIndent=8, leading=12),
        )
    )
    story.append(Spacer(1, 10))

    # 5. MỤC II: NỘI DUNG BIẾN ĐỘNG
    story.append(Paragraph("<b>II. Nội dung biến động:</b>", section_head_style))
    story.append(Spacer(1, 4))
    story.append(
        Paragraph(_format_multiline_html(change_content), ParagraphStyle("ChangeVal", parent=body_style, leftIndent=8, leading=16))
    )
    story.append(Spacer(1, 10))

    # 6. MỤC III: THÔNG TIN VỀ ĐỐI TƯỢNG ĐƯỢC MIỄN, GIẢM NGHĨA VỤ TÀI CHÍNH
    story.append(
        Paragraph("<b>III. Thông tin về đối tượng được miễn, giảm nghĩa vụ tài chính về đất đai (nếu có):</b>", section_head_style)
    )
    story.append(Spacer(1, 4))
    story.append(
        Paragraph(_format_multiline_html(tax_exemption_info), ParagraphStyle("TaxVal", parent=body_style, leftIndent=8, leading=16))
    )
    story.append(Spacer(1, 10))

    # 7. MỤC IV: GIẤY TỜ LIÊN QUAN ĐẾN NỘI DUNG BIẾN ĐỘNG NỘP KÈM THEO ĐƠN
    story.append(
        Paragraph("<b>IV. Giấy tờ liên quan đến nội dung biến động nộp kèm theo đơn này gồm có:</b>", section_head_style)
    )
    story.append(Spacer(1, 4))
    story.append(
        Paragraph(_format_multiline_html(attached_documents), ParagraphStyle("AttVal", parent=body_style, leftIndent=8, leading=16))
    )
    story.append(Spacer(1, 12))

    # 8. CAM ĐOAN VÀ KHUNG CHỮ KÝ (KeepTogether để không bị tràn ngắt trang đơn lẻ)
    closing_elements = []

    cam_doan_p = Paragraph(
        "<i>Cam đoan nội dung kê khai trên đơn là đúng sự thật và chịu trách nhiệm trước pháp luật.</i>",
        ParagraphStyle("CamDoan", parent=body_italic, fontSize=10, leading=15, leftIndent=0),
    )
    closing_elements.append(cam_doan_p)
    closing_elements.append(Spacer(1, 14))

    # Bảng chữ ký 2 cột
    sign_left = (
        "<font size='10'><b>CƠ QUAN TIẾP NHẬN HỒ SƠ</b></font><br/>"
        "<font size='8' color='#475569'><i>(Ký, ghi rõ họ tên và đóng dấu)</i></font><br/><br/>"
        "<font size='8.5' color='#059669'><b>[ ĐÃ TIẾP NHẬN TRỰC TUYẾN ]</b></font><br/>"
        "<font size='8' color='#64748b'><i>Hệ thống Thông tin giải quyết TTHC</i></font>"
    )

    sign_right = (
        f"<font size='9.5'><i>..., {date_str}</i></font><br/>"
        "<font size='10'><b>NGƯỜI VIẾT ĐƠN</b></font><br/>"
        "<font size='8' color='#475569'><i>(Ký, ghi rõ họ tên và đóng dấu nếu có)</i></font><br/><br/>"
        "<font size='8.5' color='#059669'><b>[ ĐÃ KÝ SỐ XÁC THỰC ]</b></font><br/>"
        f"<font size='10.5'><b>{html.escape(declarer_signature_name)}</b></font>"
    )

    sign_table = Table(
        [
            [
                Paragraph(sign_left, ParagraphStyle("SL", parent=body_style, alignment=1, leading=13)),
                Paragraph(sign_right, ParagraphStyle("SR", parent=body_style, alignment=1, leading=13)),
            ]
        ],
        colWidths=[content_width / 2.0, content_width / 2.0],
    )
    sign_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    closing_elements.append(sign_table)
    story.append(KeepTogether(closing_elements))

    # 9. Sinh PDF qua NumberedCanvas
    def canvasmaker(*args, **kwargs):
        c = NumberedCanvas(*args, **kwargs)
        c.app_code = app_code
        c.template_ref = "Mẫu số 18 - Nghị định số 151/2025/NĐ-CP"
        return c

    doc.build(story, canvasmaker=canvasmaker)
    return buffer.getvalue()
