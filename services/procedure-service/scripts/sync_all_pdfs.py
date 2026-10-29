import os
import sys
import requests

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
    "Prefer": "return=representation"
}

# Mapping thủ tục theo tên file
FILE_MAPPING = {
    # Business
    "Thủ tục ủy quyền thực hiện thủ tục đăng ký doanh nghiệp": ("DN-001", "Thủ tục ủy quyền thực hiện thủ tục đăng ký doanh nghiệp", "Văn bản ủy quyền thực hiện thủ tục đăng ký doanh nghiệp"),
    "Thủ tục đề nghị cấp lại Giấy chứng nhận đăng ký doanh nghiệp": ("DN-002", "Thủ tục đề nghị cấp lại Giấy chứng nhận đăng ký doanh nghiệp", "Giấy đề nghị cấp lại Giấy chứng nhận đăng ký doanh nghiệp"),
    "Thủ tục đăng ký thành lập Công ty Cổ phần": ("DN-003", "Thủ tục đăng ký thành lập Công ty Cổ phần", "Giấy đề nghị đăng ký doanh nghiệp Công ty Cổ phần"),
    "Thủ tục đăng ký thành lập Công ty Trách nhiệm hữu hạn": ("DN-004", "Thủ tục đăng ký thành lập Công ty Trách nhiệm hữu hạn", "Giấy đề nghị đăng ký doanh nghiệp Công ty TNHH"),
    "Thủ tục đăng ký thành lập Doanh nghiệp tư nhân": ("DN-005", "Thủ tục đăng ký thành lập Doanh nghiệp tư nhân", "Giấy đề nghị đăng ký doanh nghiệp Doanh nghiệp tư nhân"),
    "Thủ tục thuê nhà - mặt bằng làm Trụ sở chính doanh nghiệp": ("DN-006", "Thủ tục thuê nhà/mặt bằng làm Trụ sở chính doanh nghiệp", "Hợp đồng thuê nhà làm Trụ sở chính doanh nghiệp"),
    "Thủ tục đăng ký thuế, khai thuế ban đầu": ("DN-007", "Thủ tục đăng ký thuế, khai thuế ban đầu", "Tờ khai đăng ký thuế và hồ sơ khai thuế ban đầu"),
    "Thủ tục đăng ký thành lập Công ty Hợp danh": ("DN-008", "Thủ tục đăng ký thành lập Công ty Hợp danh", "Giấy đề nghị đăng ký doanh nghiệp Công ty Hợp danh"),
    "Thủ tục đăng ký giải thể doanh nghiệp": ("DN-009", "Thủ tục đăng ký giải thể doanh nghiệp", "Thông báo và quyết định giải thể doanh nghiệp"),
    "Thủ tục thông báo tạm ngừng - Cấp đổi thông tin ĐKDN": ("DN-010", "Thủ tục thông báo tạm ngừng / Cấp đổi thông tin ĐKDN", "Thông báo tạm ngừng kinh doanh / cấp đổi thông tin ĐKDN"),
    "Thủ tục đăng ký thay đổi nội dung Đăng ký doanh nghiệp": ("DN-011", "Thủ tục đăng ký thay đổi nội dung Đăng ký doanh nghiệp", "Thông báo thay đổi nội dung đăng ký doanh nghiệp"),
    "Thủ tục đăng ký thành lập Hộ kinh doanh - Giấy đề nghị": ("HKD-001", "Đăng ký thành lập hộ kinh doanh", "Giấy đề nghị đăng ký hộ kinh doanh"),
    "Thủ tục thay đổi nội dung đăng ký Hộ kinh doanh - Thông báo": ("HKD-002", "Thay đổi nội dung đăng ký hộ kinh doanh", "Thông báo thay đổi nội dung đăng ký hộ kinh doanh"),
    "Thủ tục thông báo lập Địa điểm kinh doanh - Đăng ký Hộ kinh doanh": ("HKD-003", "Thông báo lập Địa điểm kinh doanh của Hộ kinh doanh", "Thông báo lập địa điểm kinh doanh của hộ kinh doanh"),

    # Land
    "Thủ tục đăng ký biến động quyền sử dụng đất do chuyển nhượng": ("DD-001", "Đăng ký biến động quyền sử dụng đất do chuyển nhượng", "Đơn đăng ký biến động quyền sử dụng đất (Mẫu 09/ĐK)"),
    "Thủ tục cấp đổi Giấy chứng nhận quyền sử dụng đất - Mẫu đơn xin đo đạc lại đất": ("DD-002", "Cấp đổi Giấy chứng nhận quyền sử dụng đất", "Đơn đề nghị cấp đổi Giấy chứng nhận và đo đạc lại đất"),
    "Thủ tục tách thửa hoặc hợp thửa đất": ("DD-003", "Tách thửa hoặc hợp thửa đất", "Đơn đề nghị tách thửa hoặc hợp thửa đất (Mẫu 11/ĐK)"),
    "Thủ tục đăng ký đất đai, cấp Giấy chứng nhận lần đầu": ("DD-004", "Thủ tục đăng ký đất đai, cấp Giấy chứng nhận lần đầu", "Đơn đăng ký, cấp Giấy chứng nhận quyền sử dụng đất lần đầu (Mẫu 04a/ĐK)"),
    "Thủ tục cấp lại Giấy chứng nhận do bị mất - Đơn đề nghị cấp lại": ("DD-005", "Thủ tục cấp lại Giấy chứng nhận do bị mất", "Đơn đề nghị cấp lại Giấy chứng nhận quyền sử dụng đất (Mẫu 10/ĐK)"),
    "Thủ tục đăng ký biến động do tặng cho, thừa kế - Hợp đồng tặng cho quyền sử dụng đất": ("DD-006", "Thủ tục đăng ký biến động do tặng cho, thừa kế", "Hợp đồng tặng cho quyền sử dụng đất, quyền sở hữu nhà ở"),
    "Thủ tục đăng ký biến động do tặng cho, thừa kế - Văn bản khai nhận di sản thừa kế": ("DD-006", "Thủ tục đăng ký biến động do tặng cho, thừa kế", "Văn bản khai nhận di sản thừa kế quyền sử dụng đất"),
    "Thủ tục đăng ký biến động do tặng cho, thừa kế - Văn bản thỏa thuận phân chia di sản thừa kế": ("DD-006", "Thủ tục đăng ký biến động do tặng cho, thừa kế", "Văn bản thỏa thuận phân chia di sản thừa kế"),
    "Thủ tục đính chính Giấy chứng nhận đã cấp - Đơn đề nghị đính chính": ("DD-007", "Thủ tục đính chính Giấy chứng nhận đã cấp do sai sót thông tin", "Đơn đề nghị đính chính Giấy chứng nhận quyền sử dụng đất đã cấp"),
    "Thủ tục chuyển mục đích sử dụng đất - Đơn đề nghị chuyển mục đích sử dụng đất": ("DD-008", "Thủ tục chuyển mục đích sử dụng đất", "Đơn xin chuyển mục đích sử dụng đất"),
    "Thủ tục chuyển mục đích sử dụng đất - Đơn đề nghị giao đất, thuê đất, chuyển mục đích sử dụng đất": ("DD-008", "Thủ tục chuyển mục đích sử dụng đất", "Đơn xin giao đất, cho thuê đất, chuyển mục đích sử dụng đất"),
    "Thủ tục chuyển mục đích sử dụng đất - Phương án sử dụng tầng đất mặt": ("DD-008", "Thủ tục chuyển mục đích sử dụng đất", "Phương án sử dụng tầng đất mặt khi chuyển mục đích sử dụng đất"),
    "Thủ tục gia hạn thời hạn sử dụng đất - Đơn xin gia hạn sử dụng đất": ("DD-009", "Thủ tục gia hạn thời hạn sử dụng đất", "Đơn xin gia hạn thời hạn sử dụng đất"),
    "Thủ tục gia hạn thời hạn sử dụng đất - Đơn xác nhận thời hạn sử dụng đất nông nghiệp": ("DD-009", "Thủ tục gia hạn thời hạn sử dụng đất", "Đơn xin xác nhận lại thời hạn sử dụng đất nông nghiệp"),
    "Thủ tục đăng ký thế chấp quyền sử dụng đất - Đơn đăng ký thế chấp": ("DD-010", "Thủ tục đăng ký thế chấp quyền sử dụng đất", "Phiếu yêu cầu đăng ký biện pháp bảo đảm bằng quyền sử dụng đất"),
    "Thủ tục đăng ký thế chấp quyền sử dụng đất - Hợp đồng thế chấp quyền sử dụng đất": ("DD-010", "Thủ tục đăng ký thế chấp quyền sử dụng đất", "Hợp đồng thế chấp quyền sử dụng đất và tài sản gắn liền"),
    "Thủ tục xóa đăng ký thế chấp quyền sử dụng đất (Giải chấp Sổ đỏ)": ("DD-011", "Thủ tục xóa đăng ký thế chấp quyền sử dụng đất", "Phiếu yêu cầu xóa đăng ký biện pháp bảo đảm (Giải chấp)"),
    "Thủ tục cung cấp thông tin, dữ liệu đất đai - Phiếu yêu cầu": ("DD-012", "Thủ tục cung cấp thông tin, dữ liệu đất đai", "Phiếu yêu cầu cung cấp dữ liệu đất đai"),
    "Thủ tục giao đất, cho thuê đất - Đơn xin giao đất": ("DD-013", "Thủ tục giao đất, cho thuê đất", "Đơn xin giao đất"),
    "Thủ tục giao đất, cho thuê đất - Đơn xin thuê đất": ("DD-013", "Thủ tục giao đất, cho thuê đất", "Đơn xin thuê đất"),
    "Thủ tục hòa giải tranh chấp đất đai": ("DD-014", "Thủ tục hòa giải tranh chấp đất đai", "Đơn yêu cầu hòa giải tranh chấp đất đai tại UBND cấp xã"),
}

def sync_all():
    rest_url = f"{SUPABASE_URL}/rest/v1/form_templates"
    print("Bắt đầu đồng bộ tất cả file PDF và Word lên cơ sở dữ liệu Supabase...")
    
    folders = [
        ("business", BIZ_DIR),
        ("land", LAND_DIR),
    ]
    
    synced = 0
    for domain, folder in folders:
        for fname in sorted(os.listdir(folder)):
            if fname.startswith("~$") or fname.startswith("."):
                continue
            ext = os.path.splitext(fname)[1].lower().replace(".", "").upper()
            if ext not in ("PDF", "DOCX", "DOC"):
                continue
                
            base_name = os.path.splitext(fname)[0]
            
            # Tra cứu thông tin thủ tục
            meta = FILE_MAPPING.get(base_name)
            if meta:
                p_code, p_name, form_name = meta
            else:
                p_code = "DD-001" if domain == "land" else "DN-001"
                p_name = base_name
                form_name = base_name
                
            full_path = os.path.join(folder, fname)
            preview_folder = os.path.join(PREVIEW_DIR, os.path.basename(folder))
            preview_path = os.path.join(preview_folder, fname)
            read_path = preview_path if (ext == "PDF" and os.path.exists(preview_path)) else full_path
            file_size = os.path.getsize(read_path)
            with open(read_path, "rb") as f:
                raw_bytes = f.read()
            hex_data = "\\x" + raw_bytes.hex()
            
            mtype = "application/pdf" if ext == "PDF" else (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document" if ext == "DOCX" else "application/msword"
            )
            
            payload = {
                "domain": domain,
                "procedure_code": p_code,
                "procedure_name": p_name,
                "name": form_name if ext != "PDF" else f"{form_name} (PDF)",
                "file_name": fname,
                "format": ext,
                "media_type": mtype,
                "file_size": file_size,
                "file_data": hex_data,
                "is_active": True,
            }
            
            # Check existing
            check_url = f"{rest_url}?domain=eq.{domain}&file_name=eq.{requests.utils.quote(fname)}&select=id"
            check_res = requests.get(check_url, headers=HEADERS)
            existing = check_res.json() if check_res.ok else []
            
            if existing:
                rec_id = existing[0]["id"]
                res = requests.patch(f"{rest_url}?id=eq.{rec_id}", headers=HEADERS, json=payload)
            else:
                res = requests.post(rest_url, headers=HEADERS, json=payload)
                
            if res.ok:
                synced += 1
                print(f"[{synced}] Đồng bộ thành công: [{p_code}] {fname} ({file_size:,} bytes)")
            else:
                print(f"Lỗi đồng bộ {fname}: {res.status_code} - {res.text[:100]}")
                
    print(f"\n HOÀN TẤT: Đã đồng bộ tổng cộng {synced} file biểu mẫu lên Supabase Database!")

if __name__ == "__main__":
    sync_all()
