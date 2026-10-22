# User database

Database riêng của User Service, gồm user profile, role, permission và authentication metadata. Service khác không truy cập trực tiếp các bảng này.

## Khởi tạo / cập nhật schema

Chạy lần lượt trong Supabase → SQL Editor (database của `USER_DATABASE_URL`). Các file chạy lại nhiều lần không sao.

1. `001_users_schema.sql`: tạo bảng `users`, `refresh_tokens` (hoặc bổ sung cột `username`, `password_hash` nếu bảng đã có).
2. `002_seed_admin.sql`: tạo tài khoản quản trị `admin2026`.
3. `003_citizen_profiles.sql`: tạo bảng `citizen_profiles` (hồ sơ cá nhân theo CCCD, 1-1 với `users`, số CCCD không trùng).
4. `004_account_management.sql`: bổ sung cột cho quản lý tài khoản: `locked_until` (khóa có thời hạn), `deleted_by` (xóa mềm), `created_by`, `created_as_lawyer` (tài khoản admin tạo).
5. `005_seed_demo_users.sql`: tài khoản demo.
6. `006_users_phone_unique.sql`: số điện thoại không trùng (unique index `users_phone_key`).

## Hai cách đăng nhập

- Firebase (Google hoặc email/mật khẩu Firebase): `POST /api/v1/users/auth/firebase`, user mới có role `citizen`, có `firebase_uid`.
- Tự đăng ký bằng email + SĐT + mật khẩu: `POST /api/v1/users/auth/register` tạo tài khoản `local` (`username` = email). Sau đó đăng nhập Firebase cùng email (email đã xác minh) thì tự liên kết vào tài khoản này, mật khẩu cũ bị xóa.
- Tên đăng nhập + mật khẩu lưu trong bảng `users` (`username`, `password_hash` dạng bcrypt): `POST /api/v1/users/auth/login`. Tạo hoặc đổi mật khẩu bằng `scripts/create_admin.py`.
