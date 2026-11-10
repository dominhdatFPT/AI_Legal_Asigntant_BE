# Word trong màn dựng mẫu

Màn dựng mẫu dùng ONLYOFFICE Docs Community 9.3; tab Tổng hợp vẫn dùng trình
hiển thị mẫu hiện tại. Trường trong thư viện được chọn từng ô. Bản chụp các thẻ
nhóm cũ vẫn được đọc nguyên trạng, không sửa các mẫu đã lưu.

## Cấu hình local

- `docker compose up -d --build procedure-service onlyoffice`
- ONLYOFFICE chỉ bind `127.0.0.1:8082`; frontend gọi script từ địa chỉ này.
- Các biến cấu hình ONLYOFFICE được đặt trong file `BE/.env` (và được tài liệu hóa ở `BE/.env.example`):
  chứa `ONLYOFFICE_JWT_SECRET` với giá trị ngẫu nhiên ít nhất 32 ký tự (docker-compose tự đồng bộ sang biến `JWT_SECRET` của container OnlyOffice).
  Không commit hoặc in giá trị này vào log.
- Khi khởi động Docker Compose, `procedure-service` và `onlyoffice` tự động nạp secret từ `BE/.env`.
  Không cần tạo riêng `.keys/onlyoffice.env` (tuy nhiên hệ thống vẫn giữ fallback tương thích nếu file tồn tại).
- Backend tải file và nhận callback qua mạng Docker. Không mở source/callback
  không xác thực: link có ticket HS256 giới hạn mẫu/phiên/mục đích/8 giờ, callback
  còn phải có chữ ký JWT do ONLYOFFICE gửi.
- Download callback chỉ nhận URL `/cache/` thuộc ONLYOFFICE đã cấu hình;
  không theo redirect và giới hạn DOCX 10 MB.

## Sử dụng

1. Lưu nháp mẫu, tải lên `.docx`.
2. Ngay trong **Dựng mẫu**, bên trái là Word, bên phải là thư viện và các trường.
   Chọn `.docx` ở khung trái sẽ tải lên và mở tự động; mẫu chưa có tên lấy tên file
   làm tên nháp ban đầu. Không sinh trường từ nội dung tài liệu.
3. Tạo trường, bấm sao chép mã và Ctrl+V vào vị trí trong Word.
4. **Lưu nháp** yêu cầu forcesave và chờ backend xác nhận trước khi báo thành công.
5. Tổng hợp vẫn mở độc lập; chuyển tab chỉ ẩn khung dựng mẫu, không tháo iframe Word.
6. Xuất bản chỉ chạy sau khi lưu Word và kiểm tra mã trường thành công.

Mỗi phiên mở giữ bản DOCX gốc theo SHA-256 trong volume `office_backups`.
Callback chỉ thay bản Word của draft và từ chối nếu một phiên khác đã thay file.
Phiên đang mở cần mở lại sau khi backend restart. Chưa có tác vụ tự xóa backup.
Không cam kết mọi định dạng Word phức tạp có bố cục giống từng pixel trong
ONLYOFFICE; bản gốc lưu riêng không bị sửa.

## Dữ liệu tài khoản

Các trường có `autofill: profile.*` được điền từ hồ sơ tài khoản, readonly tại
màn công dân. Backend xác nhận lại qua API nội bộ user-service khi xuất file và
tạo hồ sơ. Không lấy CCCD/họ tên do client tự gửi làm dữ liệu chuẩn. Dòng người
khác trong nhóm lặp vẫn nhập độc lập. Muốn đổi dữ liệu gốc: cập nhật Tài khoản.

## Kiểm tra

Các test standalone trong `tests/test_office_editor.py`,
`tests/test_profile_binding.py` chạy bên trong image procedure-service bằng
`Get-Content -Raw tests/<file> | docker compose exec -T procedure-service python -`.

`tests/onlyoffice_smoke_app.py` chỉ dùng trong container thử độc lập, tài liệu
giả và DB trong bộ nhớ. Không mount vào production hoặc public: có override
admin dành riêng kiểm thử. Container thử phải xóa sau kiểm tra.

Tài liệu tham khảo: [ONLYOFFICE Docs API](https://api.onlyoffice.com/docs/docs-api/),
[Force save](https://api.onlyoffice.com/docs/docs-api/additional-api/command-service/forcesave),
[Docker Community](https://helpcenter.onlyoffice.com/docs/installation/docs-community-install-docker.aspx).
Khi triển khai ngoài localhost, cần cấu hình lại public URL/HTTPS và rà soát
điều kiện giấy phép Community (AGPL) theo cách phân phối dự án.
