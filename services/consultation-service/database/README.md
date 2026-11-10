# Consultation database

Database riêng của Consultation Service (`CONSULTATION_DATABASE_URL`). Các bảng chỉ lưu ID logic (`citizen_user_id`, `lawyer_user_id`, `closed_by`, `agent_session_id`); tài khoản thuộc User Service, hội thoại AI thuộc Agent Service. Thiết kế: `docs/api/CONSULTATION_DESIGN.md`.

- `consultations`: một dòng đi từ yêu cầu đến cuộc chat `waiting → in_progress → closed`, `source` `lobby | direct`, `close_reason` khi đóng. Yêu cầu `waiting` bị xóa hẳn khi hết hạn; cuộc đã đóng quá 10 ngày thì xóa `question`, `ai_summary` và đặt `purged_at`, giữ dòng.
- `consultation_messages`: tin nhắn text, `is_read` cho chấm đỏ. Bị xóa khi cuộc tư vấn bị xóa nội dung.
- `consultation_summaries`: biên bản JSON từng đoạn chat do Agent trích (design mục 9b), giữ lâu dài; `consultations.summarized_until` là mốc đã tóm tắt đến tin nào.
- `consultation_reports`: ai report ai, lý do, trạng thái `pending | resolved | dismissed`.
- `consultation_terms_acceptances`: user đã đồng ý điều khoản phiên bản nào.

## Khởi tạo / cập nhật schema

Chạy trong Supabase → SQL Editor của database Consultation. Chạy lại nhiều lần không sao.

1. `001_consultation_schema.sql`: tạo bảng, index, bật RLS và thu hồi quyền `anon` / `authenticated`.
