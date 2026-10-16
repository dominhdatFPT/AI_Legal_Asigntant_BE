-- Tài khoản quản trị mặc định: username admin2026.
-- password_hash là bcrypt hash của mật khẩu mặc định (nhóm đã thống nhất). Không lưu mật khẩu gốc.
-- Đổi mật khẩu: chạy `python scripts/create_admin.py --username admin2026` (nhập mật khẩu mới).
-- Chạy SAU 001_users_schema.sql. Chạy lại nhiều lần không sao: nếu đã có thì cập nhật lại.

insert into users (email, username, password_hash, full_name, auth_provider, role, status)
values (
    'admin2026@ai-legal.local',
    'admin2026',
    '$2b$12$tghuB17J7FUBbaIC8Odos.3lakj5/wmdDsT7SxgBbgJVdeAPOsCDe',
    'Quản trị viên hệ thống',
    'local',
    'admin',
    'active'
)
on conflict (email) do update
set username      = excluded.username,
    password_hash = excluded.password_hash,
    role          = 'admin',
    status        = 'active',
    deleted_at    = null,
    updated_at    = now();

-- Kiểm tra: phải thấy 1 dòng role = admin
select id, username, email, role, status from users where lower(username) = 'admin2026';
