# Procedure database

Database riêng của Procedure Service, gồm thủ tục, yêu cầu hồ sơ, lệ phí và legal reference metadata. Dữ liệu được quản lý bằng migration của chính service.

## Thứ tự chạy (Supabase SQL editor của database Procedure)

| File | Nội dung |
|---|---|
| `001_procedure_schema.sql` | Bảng thủ tục, giấy tờ, điều kiện, lệ phí, căn cứ pháp lý |
| `002_seed_procedures.sql` | 25 thủ tục (DD-001…DD-012, DN-001…DN-011, HKD-001, HKD-002) |
| `003_procedure_user_records.sql` | Bảng hồ sơ `land_procedure_records`, `business_procedure_records` |
| `004_khoa_quyen_ho_so.sql` | **Bắt buộc nếu đã chạy 003 bản cũ**: gỡ quyền đọc/ghi hồ sơ của `anon`, thêm index "hồ sơ của tôi" |
| `005_seed_demo_applications.sql` | (Tùy chọn) 8 hồ sơ ảo cho một tài khoản |
| `check_demo_applications.sql` | Đếm hồ sơ ảo theo tài khoản/trạng thái, kèm lệnh xóa |

## Thêm dữ liệu ảo cho một tài khoản

1. Trên database **User**: `select id, email, role from users where email = '<email>';` rồi copy `id`.
2. Mở `005_seed_demo_applications.sql`, dán `id` vào `v_user_id` (có thể đổi `v_full_name`, `v_cccd`, `v_phone`), chạy trên database **Procedure**.
3. Chạy `check_demo_applications.sql`: tài khoản đó có 8 dòng hồ sơ (land 5, business 3).

Chạy lại bước 2 không bị nhân đôi. Hồ sơ ảo đánh dấu `extra_data = {"seed": "demo"}`, hồ sơ thật không bị xóa.
Service phải kết nối bằng `PROCEDURE_DATABASE_URL` (role postgres); không dùng anon key để đọc bảng hồ sơ.
