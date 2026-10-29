# Thêm mẫu PDF từ trang quản trị

Trang `/landforms` và `/business-form-templates` gửi file tới
`POST /api/contracts/reference-catalog` qua Gateway. Request là multipart:
`file` (PDF tối đa 10 MiB, không mật khẩu, 1–100 trang), `name` (5–255 ký tự),
`domain` (`land` hoặc `business`), `procedure` (key trong `classifications` của
`GET /api/contracts/reference-catalog`). Chỉ admin có quyền hiện tại mới được thêm;
cookie phiên cần Origin được phép. File trùng nội dung trong cùng lĩnh vực/thủ tục trả 409.

PDF gốc và PNG xem trước từng trang được lưu nguyên tử trong bảng
`contract_reference_uploads` của database Contract. Bản gốc không bị thay đổi.
Mẫu xuất hiện trong danh sách quản trị và kho công khai `/document-storages`;
`/xem-mau-don?template={id}` hiển thị các trang PNG. Đây là mẫu nguồn để xem,
chưa có mapping ô điền hoặc generation. Không liên kết vào catalog hợp đồng
có phiên bản điền/xuất chỉ bằng thao tác tải file.

## Cập nhật máy chạy

Từ thư mục `services/contract-service` (môi trường có requirements và common_auth):

```powershell
python scripts/migrate_reference_uploads.py --apply
```

Script chỉ dùng `CONTRACT_DATABASE_URL`, đọc bổ sung `backend/.env`, mặc định chỉ
kiểm tra khi không có `--apply`. Migration 003 tạo bảng mới và bật RLS; không sửa
hai kho mẫu hiện có. Không tự chạy migration trong API.

Từ `backend`, build lại và bật Contract/Gateway; User phải đang chạy để xác thực admin:

```powershell
docker compose build contract-service
docker compose up -d --no-deps contract-service gateway
```

Khởi động lại Express frontend để nạp route proxy asset mới. File PDF có URL API
`/api/contracts/reference-assets/{id}.pdf`, ảnh trang là `{id}-page-{n}.png`.
Frontend chuyển qua `/api/reference-assets/...` cùng origin, không forward cookie
hoặc Authorization đến API đọc file công khai. Gateway dành 11 MiB cho multipart
để chấp nhận file PDF 10 MiB cùng metadata.

## Kiểm thử

`tests/test_reference_uploads.py` dùng unittest trong image Contract. Hai test
kiểm tra parser/render chạy offline; bốn test HTTP/SQL chỉ chạy khi
`TEST_REFERENCE_DATABASE_URL=postgresql://postgres@127.0.0.1/reference_upload_tests`
trỏ tới PostgreSQL Docker tạm riêng, dùng chung network namespace container DB.
Không dùng database thật cho các test tạo/xóa fixture.
