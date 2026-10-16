# Procedure Service

Dịch vụ quản lý và tra cứu dữ liệu thủ tục hành chính (đất đai, hộ kinh doanh cá thể) và hồ sơ giấy tờ cần chuẩn bị.

## 1. Tài liệu API bàn giao cho Frontend
- **Tài liệu chi tiết cho FE:** Xem [docs/api/PROCEDURE_API.md](../../docs/api/PROCEDURE_API.md)
- **OpenAPI Contract (YAML):** Xem [openapi.yaml](openapi.yaml)

## 2. Danh sách Endpoint
- `GET /api/v1/procedures/health`: Kiểm tra trạng thái service
- `GET /api/v1/procedures/domains`: Danh sách lĩnh vực (`land`, `business`) kèm số lượng
- `GET /api/v1/procedures`: Danh sách thủ tục (lọc theo `domain`, tìm kiếm nhanh `q`, phân trang `page`, `page_size`)
- `POST /api/v1/procedures/search`: Tìm kiếm thủ tục theo câu hỏi hoặc từ khóa
- `GET /api/v1/procedures/{procedure_id}`: Chi tiết thủ tục (hỗ trợ cả UUID hoặc mã `HKD-001`, `DD-001`...)
- `GET /api/v1/procedures/{procedure_id}/requirements`: Danh mục hồ sơ, giấy tờ cần chuẩn bị
- `GET /api/v1/procedures/{procedure_id}/conditions`: Danh sách điều kiện thực hiện
- `GET /api/v1/procedures/{procedure_id}/fees`: Chi tiết bảng phí và lệ phí

## 3. Chạy service độc lập (Local Dev)
```bash
python -m uvicorn app.main:app --reload --port 8000
```
*Lưu ý:* Service đã tích hợp sẵn **Fallback Mock Data**. Khi chưa có cấu hình Supabase, hệ thống vẫn hoạt động và trả về dữ liệu mẫu thực tế để kiểm thử.
