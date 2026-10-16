# Agent database

Database riêng của Agent Service (`AGENT_DATABASE_URL`), lưu hội thoại và tin nhắn giữa người dùng với AI. Service khác không truy cập trực tiếp các bảng này.

## Bảng

- `conversations`: mỗi phiên trò chuyện của 1 user (`user_id` lấy từ JWT, không có khóa ngoại sang user-service).
- `messages`: tin nhắn trong hội thoại, `role` là `user` hoặc `assistant`. Tin nhắn `assistant` có `metadata` (`rag_chunk_ids`, `model`, `tokens_used`). Xóa hội thoại thì tin nhắn bị xóa theo (CASCADE).

## Khởi tạo / cập nhật schema

Chạy trong Supabase → SQL Editor (project của `AGENT_DATABASE_URL`). Chạy lại nhiều lần không sao.

1. `001_agent_schema.sql`: tạo bảng `conversations`, `messages` và index.
2. `check_agent_db.sql`: kiểm tra, cột `ket_qua` phải toàn `OK`.
