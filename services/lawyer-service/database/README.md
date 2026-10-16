# Lawyer database

Database riêng của Lawyer Service (`LAWYER_DATABASE_URL`). Các bảng chỉ lưu ID logic (`user_id`, `card_document_id`, `reviewed_by`); tài khoản, vai trò, CCCD thuộc User Service, ảnh thẻ và OCR thuộc Document Service. Thiết kế: `docs/api/LAWYER_DESIGN.md`.

- `specializations`: lĩnh vực tư vấn (`land` Đất đai, `business` Hộ kinh doanh).
- `lawyer_profiles`: hồ sơ đang hiệu lực, 1 dòng / người, trạng thái `incomplete | active | hidden`, nguồn `admin_created | self_applied`, cột `is_online` (chưa có API bật/tắt).
- `lawyer_profile_specializations`: lĩnh vực của hồ sơ (nhiều lĩnh vực).
- `lawyer_applications`: đơn `new | card_change`, trạng thái `draft → pending → approved | rejected`, `draft/pending → withdrawn`; lưu ảnh chụp các trường, `card_document_id` + JSON OCR.

## Khởi tạo / cập nhật schema

Chạy lần lượt trong Supabase → SQL Editor của database Lawyer. Chạy lại nhiều lần không sao.

1. `001_lawyer_schema.sql`: tạo bảng, index, seed lĩnh vực, bật RLS và thu hồi quyền `anon` / `authenticated`.
2. Tạo hồ sơ cho luật sư đã có: chạy `user-service/database/007_lawyer_role.sql` rồi `008_export_lawyer_profiles.sql` trên database **User**, copy câu `INSERT` in ra sang đây rồi Run.
3. `check_lawyer_db.sql`: kiểm tra, cột `ket_qua` phải toàn `OK`.
