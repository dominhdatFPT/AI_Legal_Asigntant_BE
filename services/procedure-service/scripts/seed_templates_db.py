import os
import sys
import requests

# Base directory
SERVICE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIZ_DIR = os.path.join(SERVICE_DIR, "Template các thủ tục kinh doanh")
LAND_DIR = os.path.join(SERVICE_DIR, "Template các thủ tục đất đai")
PREVIEW_DIR = os.path.join(SERVICE_DIR, "database", "form_templates_preview")

SUPABASE_URL = "https://qqwuhiymixomwrdunfkz.supabase.co"
SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFxd3VoaXltaXhvbXdyZHVuZmt6Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTE0MzgxNzAsImV4cCI6MjEwNzAxNDE3MH0."
    "SHNvLsI4fVZTbm1gS0pidZWjOLJnQBTgbQ-chLA5aTA"
)

HEADERS = {
    "apikey": SUPABASE_ANON_KEY,
    "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates"
}

# Template definitions mapping
TEMPLATE_ENTRIES = [
    # --- DOANH NGHIỆP & HỘ KINH DOANH (business) ---
    {
        "folder": BIZ_DIR,
        "file_name": "Thủ tục ủy quyền thực hiện thủ tục đăng ký doanh nghiệp.docx",
        "procedure_code": "DN-001",
        "procedure_name": "Thủ tục ủy quyền thực hiện thủ tục đăng ký doanh nghiệp",
        "name": "Văn bản ủy quyền thực hiện thủ tục đăng ký doanh nghiệp",
        "domain": "business",
        "format": "DOCX",
        "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "order_index": 1,
    },
    {
        "folder": BIZ_DIR,
        "file_name": "Thủ tục đề nghị cấp lại Giấy chứng nhận đăng ký doanh nghiệp.docx",
        "procedure_code": "DN-002",
        "procedure_name": "Thủ tục đề nghị cấp lại Giấy chứng nhận đăng ký doanh nghiệp",
        "name": "Giấy đề nghị cấp lại Giấy chứng nhận đăng ký doanh nghiệp",
        "domain": "business",
        "format": "DOCX",
        "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "order_index": 2,
    },
    {
        "folder": BIZ_DIR,
        "file_name": "Thủ tục đăng ký thành lập Công ty Cổ phần.docx",
        "procedure_code": "DN-003",
        "procedure_name": "Thủ tục đăng ký thành lập Công ty Cổ phần",
        "name": "Giấy đề nghị đăng ký doanh nghiệp Công ty Cổ phần (Bản Word)",
        "domain": "business",
        "format": "DOCX",
        "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "order_index": 3,
    },
    {
        "folder": BIZ_DIR,
        "file_name": "Thủ tục đăng ký thành lập Công ty Cổ phần.pdf",
        "procedure_code": "DN-003",
        "procedure_name": "Thủ tục đăng ký thành lập Công ty Cổ phần",
        "name": "Giấy đề nghị đăng ký doanh nghiệp Công ty Cổ phần (Bản PDF gốc)",
        "domain": "business",
        "format": "PDF",
        "media_type": "application/pdf",
        "order_index": 4,
    },
    {
        "folder": BIZ_DIR,
        "file_name": "Thủ tục đăng ký thành lập Công ty Trách nhiệm hữu hạn.docx",
        "procedure_code": "DN-004",
        "procedure_name": "Thủ tục đăng ký thành lập Công ty Trách nhiệm hữu hạn",
        "name": "Giấy đề nghị đăng ký Công ty TNHH (Bản Word)",
        "domain": "business",
        "format": "DOCX",
        "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "order_index": 5,
    },
    {
        "folder": BIZ_DIR,
        "file_name": "Thủ tục đăng ký thành lập Công ty Trách nhiệm hữu hạn.pdf",
        "procedure_code": "DN-004",
        "procedure_name": "Thủ tục đăng ký thành lập Công ty Trách nhiệm hữu hạn",
        "name": "Giấy đề nghị đăng ký Công ty TNHH (Bản PDF gốc)",
        "domain": "business",
        "format": "PDF",
        "media_type": "application/pdf",
        "order_index": 6,
    },
    {
        "folder": BIZ_DIR,
        "file_name": "Thủ tục đăng ký thành lập Doanh nghiệp tư nhân.docx",
        "procedure_code": "DN-005",
        "procedure_name": "Thủ tục đăng ký thành lập Doanh nghiệp tư nhân",
        "name": "Giấy đề nghị đăng ký Doanh nghiệp tư nhân (Bản Word)",
        "domain": "business",
        "format": "DOCX",
        "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "order_index": 7,
    },
    {
        "folder": BIZ_DIR,
        "file_name": "Thủ tục đăng ký thành lập Doanh nghiệp tư nhân.pdf",
        "procedure_code": "DN-005",
        "procedure_name": "Thủ tục đăng ký thành lập Doanh nghiệp tư nhân",
        "name": "Giấy đề nghị đăng ký Doanh nghiệp tư nhân (Bản PDF gốc)",
        "domain": "business",
        "format": "PDF",
        "media_type": "application/pdf",
        "order_index": 8,
    },
    {
        "folder": BIZ_DIR,
        "file_name": "Thủ tục thuê nhà - mặt bằng làm Trụ sở chính doanh nghiệp.docx",
        "procedure_code": "DN-006",
        "procedure_name": "Thủ tục thuê nhà/mặt bằng làm Trụ sở chính doanh nghiệp",
        "name": "Hợp đồng thuê nhà/mặt bằng làm Trụ sở chính doanh nghiệp",
        "domain": "business",
        "format": "DOCX",
        "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "order_index": 9,
    },
    {
        "folder": BIZ_DIR,
        "file_name": "Thủ tục đăng ký thuế, khai thuế ban đầu.doc",
        "procedure_code": "DN-007",
        "procedure_name": "Thủ tục đăng ký thuế, khai thuế ban đầu",
        "name": "Tờ khai đăng ký thuế và khai thuế ban đầu (Mẫu 01-ĐK-TCT)",
        "domain": "business",
        "format": "DOC",
        "media_type": "application/msword",
        "order_index": 10,
    },
    {
        "folder": BIZ_DIR,
        "file_name": "Thủ tục đăng ký thành lập Công ty Hợp danh.docx",
        "procedure_code": "DN-008",
        "procedure_name": "Thủ tục đăng ký thành lập Công ty Hợp danh",
        "name": "Giấy đề nghị đăng ký Công ty Hợp danh (Bản Word)",
        "domain": "business",
        "format": "DOCX",
        "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "order_index": 11,
    },
    {
        "folder": BIZ_DIR,
        "file_name": "Thủ tục đăng ký thành lập Công ty Hợp danh.pdf",
        "procedure_code": "DN-008",
        "procedure_name": "Thủ tục đăng ký thành lập Công ty Hợp danh",
        "name": "Giấy đề nghị đăng ký Công ty Hợp danh (Bản PDF gốc)",
        "domain": "business",
        "format": "PDF",
        "media_type": "application/pdf",
        "order_index": 12,
    },
    {
        "folder": BIZ_DIR,
        "file_name": "Thủ tục đăng ký giải thể doanh nghiệp.doc",
        "procedure_code": "DN-009",
        "procedure_name": "Thủ tục đăng ký giải thể doanh nghiệp",
        "name": "Thông báo về việc giải thể doanh nghiệp (Phụ lục II-18)",
        "domain": "business",
        "format": "DOC",
        "media_type": "application/msword",
        "order_index": 13,
    },
    {
        "folder": BIZ_DIR,
        "file_name": "Thủ tục thông báo tạm ngừng - Cấp đổi thông tin ĐKDN.docx",
        "procedure_code": "DN-010",
        "procedure_name": "Thủ tục thông báo tạm ngừng / Cấp đổi thông tin ĐKDN",
        "name": "Thông báo về việc tạm ngừng kinh doanh (Phụ lục II-15)",
        "domain": "business",
        "format": "DOCX",
        "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "order_index": 14,
    },
    {
        "folder": BIZ_DIR,
        "file_name": "Thủ tục đăng ký thay đổi nội dung Đăng ký doanh nghiệp.docx",
        "procedure_code": "DN-011",
        "procedure_name": "Thủ tục đăng ký thay đổi nội dung Đăng ký doanh nghiệp",
        "name": "Thông báo thay đổi nội dung Đăng ký doanh nghiệp (Phụ lục II-1)",
        "domain": "business",
        "format": "DOCX",
        "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "order_index": 15,
    },
    {
        "folder": BIZ_DIR,
        "file_name": "Thủ tục đăng ký thành lập Hộ kinh doanh - Giấy đề nghị.docx",
        "procedure_code": "HKD-001",
        "procedure_name": "Đăng ký thành lập hộ kinh doanh",
        "name": "Giấy đề nghị đăng ký Hộ kinh doanh (Phụ lục III-1)",
        "domain": "business",
        "format": "DOCX",
        "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "order_index": 16,
    },
    {
        "folder": BIZ_DIR,
        "file_name": "Thủ tục thông báo lập Địa điểm kinh doanh - Đăng ký Hộ kinh doanh.doc",
        "procedure_code": "HKD-001",
        "procedure_name": "Đăng ký thành lập hộ kinh doanh",
        "name": "Thông báo lập Địa điểm kinh doanh của Hộ kinh doanh",
        "domain": "business",
        "format": "DOC",
        "media_type": "application/msword",
        "order_index": 17,
    },
    {
        "folder": BIZ_DIR,
        "file_name": "Thủ tục thay đổi nội dung đăng ký Hộ kinh doanh - Thông báo.docx",
        "procedure_code": "HKD-002",
        "procedure_name": "Thay đổi nội dung đăng ký hộ kinh doanh",
        "name": "Thông báo thay đổi nội dung đăng ký Hộ kinh doanh (Phụ lục III-2)",
        "domain": "business",
        "format": "DOCX",
        "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "order_index": 18,
    },

    # --- ĐẤT ĐAI & BẤT ĐỘNG SẢN (land) ---
    {
        "folder": LAND_DIR,
        "file_name": "Thủ tục đăng ký biến động quyền sử dụng đất do chuyển nhượng.docx",
        "procedure_code": "DD-001",
        "procedure_name": "Đăng ký biến động quyền sử dụng đất do chuyển nhượng",
        "name": "Đơn đăng ký biến động đất đai do chuyển nhượng (Mẫu 11/ĐK - Bản Word)",
        "domain": "land",
        "format": "DOCX",
        "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "order_index": 19,
    },
    {
        "folder": LAND_DIR,
        "file_name": "Thủ tục đăng ký biến động quyền sử dụng đất do chuyển nhượng.pdf",
        "procedure_code": "DD-001",
        "procedure_name": "Đăng ký biến động quyền sử dụng đất do chuyển nhượng",
        "name": "Đơn đăng ký biến động đất đai do chuyển nhượng (Mẫu 11/ĐK - Bản PDF gốc)",
        "domain": "land",
        "format": "PDF",
        "media_type": "application/pdf",
        "order_index": 20,
    },
    {
        "folder": LAND_DIR,
        "file_name": "Thủ tục cấp đổi Giấy chứng nhận quyền sử dụng đất - Mẫu đơn xin đo đạc lại đất.doc",
        "procedure_code": "DD-002",
        "procedure_name": "Cấp đổi Giấy chứng nhận quyền sử dụng đất",
        "name": "Mẫu đơn xin đo đạc lại đất phục vụ cấp đổi Giấy chứng nhận",
        "domain": "land",
        "format": "DOC",
        "media_type": "application/msword",
        "order_index": 21,
    },
    {
        "folder": LAND_DIR,
        "file_name": "Thủ tục tách thửa hoặc hợp thửa đất.docx",
        "procedure_code": "DD-003",
        "procedure_name": "Tách thửa hoặc hợp thửa đất",
        "name": "Đơn đề nghị tách thửa hoặc hợp thửa đất (Mẫu 11/ĐK - Bản Word)",
        "domain": "land",
        "format": "DOCX",
        "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "order_index": 22,
    },
    {
        "folder": LAND_DIR,
        "file_name": "Thủ tục tách thửa hoặc hợp thửa đất.pdf",
        "procedure_code": "DD-003",
        "procedure_name": "Tách thửa hoặc hợp thửa đất",
        "name": "Đơn đề nghị tách thửa hoặc hợp thửa đất (Mẫu 11/ĐK - Bản PDF gốc)",
        "domain": "land",
        "format": "PDF",
        "media_type": "application/pdf",
        "order_index": 23,
    },
    {
        "folder": LAND_DIR,
        "file_name": "Thủ tục đăng ký đất đai, cấp Giấy chứng nhận lần đầu.docx",
        "procedure_code": "DD-004",
        "procedure_name": "Thủ tục đăng ký đất đai, cấp Giấy chứng nhận lần đầu",
        "name": "Đơn đăng ký đất đai, cấp Giấy chứng nhận lần đầu (Mẫu 04/ĐK - Bản Word)",
        "domain": "land",
        "format": "DOCX",
        "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "order_index": 24,
    },
    {
        "folder": LAND_DIR,
        "file_name": "Thủ tục đăng ký đất đai, cấp Giấy chứng nhận lần đầu.pdf",
        "procedure_code": "DD-004",
        "procedure_name": "Thủ tục đăng ký đất đai, cấp Giấy chứng nhận lần đầu",
        "name": "Đơn đăng ký đất đai, cấp Giấy chứng nhận lần đầu (Mẫu 04/ĐK - Bản PDF gốc)",
        "domain": "land",
        "format": "PDF",
        "media_type": "application/pdf",
        "order_index": 25,
    },
    {
        "folder": LAND_DIR,
        "file_name": "Thủ tục cấp lại Giấy chứng nhận do bị mất - Đơn đề nghị cấp lại.docx",
        "procedure_code": "DD-005",
        "procedure_name": "Thủ tục cấp lại Giấy chứng nhận do bị mất",
        "name": "Đơn đề nghị cấp lại Giấy chứng nhận quyền sử dụng đất bị mất (Mẫu 10/ĐK)",
        "domain": "land",
        "format": "DOCX",
        "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "order_index": 26,
    },
    {
        "folder": LAND_DIR,
        "file_name": "Thủ tục đăng ký biến động do tặng cho, thừa kế - Hợp đồng tặng cho quyền sử dụng đất.doc",
        "procedure_code": "DD-006",
        "procedure_name": "Thủ tục đăng ký biến động do tặng cho, thừa kế quyền sử dụng đất, nhà ở",
        "name": "Hợp đồng tặng cho quyền sử dụng đất và tài sản gắn liền với đất",
        "domain": "land",
        "format": "DOC",
        "media_type": "application/msword",
        "order_index": 27,
    },
    {
        "folder": LAND_DIR,
        "file_name": "Thủ tục đăng ký biến động do tặng cho, thừa kế - Văn bản khai nhận di sản thừa kế.doc",
        "procedure_code": "DD-006",
        "procedure_name": "Thủ tục đăng ký biến động do tặng cho, thừa kế quyền sử dụng đất, nhà ở",
        "name": "Văn bản khai nhận di sản thừa kế quyền sử dụng đất",
        "domain": "land",
        "format": "DOC",
        "media_type": "application/msword",
        "order_index": 28,
    },
    {
        "folder": LAND_DIR,
        "file_name": "Thủ tục đăng ký biến động do tặng cho, thừa kế - Văn bản thỏa thuận phân chia di sản thừa kế.doc",
        "procedure_code": "DD-006",
        "procedure_name": "Thủ tục đăng ký biến động do tặng cho, thừa kế quyền sử dụng đất, nhà ở",
        "name": "Văn bản thỏa thuận phân chia di sản thừa kế quyền sử dụng đất",
        "domain": "land",
        "format": "DOC",
        "media_type": "application/msword",
        "order_index": 29,
    },
    {
        "folder": LAND_DIR,
        "file_name": "Thủ tục đính chính Giấy chứng nhận đã cấp - Đơn đề nghị đính chính.docx",
        "procedure_code": "DD-007",
        "procedure_name": "Thủ tục đính chính Giấy chứng nhận đã cấp do sai sót thông tin",
        "name": "Đơn đề nghị đính chính thông tin Giấy chứng nhận đã cấp",
        "domain": "land",
        "format": "DOCX",
        "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "order_index": 30,
    },
    {
        "folder": LAND_DIR,
        "file_name": "Thủ tục chuyển mục đích sử dụng đất - Đơn đề nghị chuyển mục đích sử dụng đất.docx",
        "procedure_code": "DD-008",
        "procedure_name": "Thủ tục chuyển mục đích sử dụng đất",
        "name": "Đơn đề nghị chuyển mục đích sử dụng đất (Mẫu 01/ĐK - Bản Word)",
        "domain": "land",
        "format": "DOCX",
        "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "order_index": 31,
    },
    {
        "folder": LAND_DIR,
        "file_name": "Thủ tục chuyển mục đích sử dụng đất - Đơn đề nghị chuyển mục đích sử dụng đất.pdf",
        "procedure_code": "DD-008",
        "procedure_name": "Thủ tục chuyển mục đích sử dụng đất",
        "name": "Đơn đề nghị chuyển mục đích sử dụng đất (Mẫu 01/ĐK - Bản PDF gốc)",
        "domain": "land",
        "format": "PDF",
        "media_type": "application/pdf",
        "order_index": 32,
    },
    {
        "folder": LAND_DIR,
        "file_name": "Thủ tục chuyển mục đích sử dụng đất - Đơn đề nghị giao đất, thuê đất, chuyển mục đích sử dụng đất.docx",
        "procedure_code": "DD-008",
        "procedure_name": "Thủ tục chuyển mục đích sử dụng đất",
        "name": "Đơn đề nghị giao đất, thuê đất, cho phép chuyển mục đích sử dụng đất",
        "domain": "land",
        "format": "DOCX",
        "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "order_index": 33,
    },
    {
        "folder": LAND_DIR,
        "file_name": "Thủ tục chuyển mục đích sử dụng đất - Phương án sử dụng tầng đất mặt.doc",
        "procedure_code": "DD-008",
        "procedure_name": "Thủ tục chuyển mục đích sử dụng đất",
        "name": "Phương án bóc tách, sử dụng tầng đất mặt khi chuyển mục đích sử dụng",
        "domain": "land",
        "format": "DOC",
        "media_type": "application/msword",
        "order_index": 34,
    },
    {
        "folder": LAND_DIR,
        "file_name": "Thủ tục gia hạn thời hạn sử dụng đất - Đơn xin gia hạn sử dụng đất.doc",
        "procedure_code": "DD-009",
        "procedure_name": "Thủ tục gia hạn thời hạn sử dụng đất",
        "name": "Đơn đề nghị gia hạn thời hạn sử dụng đất",
        "domain": "land",
        "format": "DOC",
        "media_type": "application/msword",
        "order_index": 35,
    },
    {
        "folder": LAND_DIR,
        "file_name": "Thủ tục gia hạn thời hạn sử dụng đất - Đơn xác nhận thời hạn sử dụng đất nông nghiệp.doc",
        "procedure_code": "DD-009",
        "procedure_name": "Thủ tục gia hạn thời hạn sử dụng đất",
        "name": "Đơn xin xác nhận tiếp tục sử dụng đất nông nghiệp của hộ gia đình, cá nhân",
        "domain": "land",
        "format": "DOC",
        "media_type": "application/msword",
        "order_index": 36,
    },
    {
        "folder": LAND_DIR,
        "file_name": "Thủ tục đăng ký thế chấp quyền sử dụng đất - Đơn đăng ký thế chấp.doc",
        "procedure_code": "DD-010",
        "procedure_name": "Thủ tục đăng ký thế chấp quyền sử dụng đất, tài sản gắn liền với đất",
        "name": "Phiếu yêu cầu đăng ký biện pháp bảo đảm bằng quyền sử dụng đất",
        "domain": "land",
        "format": "DOC",
        "media_type": "application/msword",
        "order_index": 37,
    },
    {
        "folder": LAND_DIR,
        "file_name": "Thủ tục đăng ký thế chấp quyền sử dụng đất - Hợp đồng thế chấp quyền sử dụng đất.doc",
        "procedure_code": "DD-010",
        "procedure_name": "Thủ tục đăng ký thế chấp quyền sử dụng đất, tài sản gắn liền với đất",
        "name": "Hợp đồng thế chấp quyền sử dụng đất bảo đảm nghĩa vụ vay vốn",
        "domain": "land",
        "format": "DOC",
        "media_type": "application/msword",
        "order_index": 38,
    },
    {
        "folder": LAND_DIR,
        "file_name": "Thủ tục xóa đăng ký thế chấp quyền sử dụng đất (Giải chấp Sổ đỏ).doc",
        "procedure_code": "DD-011",
        "procedure_name": "Thủ tục xóa đăng ký thế chấp quyền sử dụng đất (Giải chấp Sổ đỏ)",
        "name": "Phiếu yêu cầu xóa đăng ký biện pháp bảo đảm bằng quyền sử dụng đất (Giải chấp Sổ đỏ)",
        "domain": "land",
        "format": "DOC",
        "media_type": "application/msword",
        "order_index": 39,
    },
    {
        "folder": LAND_DIR,
        "file_name": "Thủ tục cung cấp thông tin, dữ liệu đất đai - Phiếu yêu cầu.docx",
        "procedure_code": "DD-012",
        "procedure_name": "Thủ tục cung cấp thông tin, dữ liệu đất đai, trích lục bản đồ địa chính",
        "name": "Phiếu yêu cầu cung cấp thông tin, dữ liệu đất đai và trích lục bản đồ địa chính",
        "domain": "land",
        "format": "DOCX",
        "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "order_index": 40,
    },
    {
        "folder": LAND_DIR,
        "file_name": "Thủ tục giao đất, cho thuê đất - Đơn xin giao đất.docx",
        "procedure_code": "DD-012",
        "procedure_name": "Thủ tục cung cấp thông tin, dữ liệu đất đai, trích lục bản đồ địa chính",
        "name": "Đơn xin giao đất đối với cá nhân, tổ chức (Bản Word)",
        "domain": "land",
        "format": "DOCX",
        "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "order_index": 41,
    },
    {
        "folder": LAND_DIR,
        "file_name": "Thủ tục giao đất, cho thuê đất - Đơn xin thuê đất.doc",
        "procedure_code": "DD-012",
        "procedure_name": "Thủ tục cung cấp thông tin, dữ liệu đất đai, trích lục bản đồ địa chính",
        "name": "Đơn xin thuê đất và phương án sản xuất kinh doanh",
        "domain": "land",
        "format": "DOC",
        "media_type": "application/msword",
        "order_index": 42,
    },
    {
        "folder": LAND_DIR,
        "file_name": "Thủ tục hòa giải tranh chấp đất đai.doc",
        "procedure_code": "DD-012",
        "procedure_name": "Thủ tục cung cấp thông tin, dữ liệu đất đai, trích lục bản đồ địa chính",
        "name": "Đơn đề nghị hòa giải tranh chấp đất đai tại UBND cấp xã",
        "domain": "land",
        "format": "DOC",
        "media_type": "application/msword",
        "order_index": 43,
    },
]

def seed_templates():
    print(f"Bắt đầu nạp {len(TEMPLATE_ENTRIES)} biểu mẫu vào database Supabase...")
    rest_url = f"{SUPABASE_URL}/rest/v1/form_templates"
    
    success_count = 0
    for idx, entry in enumerate(TEMPLATE_ENTRIES, 1):
        file_path = os.path.join(entry["folder"], entry["file_name"])
        preview_path = os.path.join(PREVIEW_DIR, os.path.basename(entry["folder"]), entry["file_name"])
        read_path = preview_path if (entry.get("format") == "PDF" and os.path.exists(preview_path)) else file_path
        if not os.path.exists(read_path):
            print(f"[{idx}/{len(TEMPLATE_ENTRIES)}] BỎ QUA - File không tồn tại: {entry['file_name']}")
            continue
            
        with open(read_path, "rb") as f:
            raw_bytes = f.read()
            
        file_size = len(raw_bytes)
        hex_data = "\\x" + raw_bytes.hex()
        
        payload = {
            "domain": entry["domain"],
            "procedure_code": entry["procedure_code"],
            "procedure_name": entry["procedure_name"],
            "name": entry["name"],
            "file_name": entry["file_name"],
            "format": entry["format"],
            "media_type": entry["media_type"],
            "file_size": file_size,
            "file_data": hex_data,
            "order_index": entry["order_index"],
            "is_active": True,
        }
        
        # Check if exists by (domain, file_name)
        check_url = f"{rest_url}?domain=eq.{entry['domain']}&file_name=eq.{requests.utils.quote(entry['file_name'])}&select=id"
        check_res = requests.get(check_url, headers=HEADERS)
        existing = check_res.json() if check_res.ok else []
        
        if existing:
            # Update existing
            rec_id = existing[0]["id"]
            update_url = f"{rest_url}?id=eq.{rec_id}"
            res = requests.patch(update_url, headers=HEADERS, json=payload)
            if res.ok:
                print(f"[{idx}/{len(TEMPLATE_ENTRIES)}] ĐÃ CẬP NHẬT: [{entry['procedure_code']}] {entry['name']} ({file_size:,} bytes)")
                success_count += 1
            else:
                print(f"[{idx}/{len(TEMPLATE_ENTRIES)}] LỖI UPDATE: {res.status_code} - {res.text[:150]}")
        else:
            # Insert new
            res = requests.post(rest_url, headers=HEADERS, json=payload)
            if res.status_code in (200, 201):
                print(f"[{idx}/{len(TEMPLATE_ENTRIES)}] ĐÃ NẠP MỚI: [{entry['procedure_code']}] {entry['name']} ({file_size:,} bytes)")
                success_count += 1
            else:
                print(f"[{idx}/{len(TEMPLATE_ENTRIES)}] LỖI INSERT: {res.status_code} - {res.text[:150]}")
                
    print(f"\n HOÀN TẤT: Đã lưu thành công {success_count}/{len(TEMPLATE_ENTRIES)} biểu mẫu vào Database!")

if __name__ == "__main__":
    seed_templates()
