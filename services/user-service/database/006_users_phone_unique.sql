-- User Service: số điện thoại là duy nhất (tự đăng ký bằng email + SĐT + mật khẩu).
-- Chạy được nhiều lần (idempotent). Chạy SAU 001 → 005.
-- Cách chạy: Supabase Dashboard → SQL Editor → dán toàn bộ file → Run.
--
-- Tính cả tài khoản đã xóa mềm (giống email). NULL không tính là trùng.
-- Nếu lệnh tạo index báo lỗi trùng: chạy câu kiểm tra ở cuối file, sửa / xóa SĐT trùng rồi chạy lại.

create unique index if not exists users_phone_key on users (phone) where phone is not null;

-- Kiểm tra: phải KHÔNG có dòng nào
select phone, count(*) as so_tai_khoan
from users
where phone is not null
group by phone
having count(*) > 1;
