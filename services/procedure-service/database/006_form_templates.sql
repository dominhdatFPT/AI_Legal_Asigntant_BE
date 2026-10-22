-- 006_form_templates.sql
-- Chạy sau 001 (cần bảng procedure_domains). An toàn khi chạy lại nhiều lần.
-- Bảng "Kho mẫu đơn tham khảo": lưu luôn nội dung file mẫu (và bản PDF xem trước của mẫu Word)
-- để mọi máy chạy procedure-service đọc chung một nguồn trên Supabase.
-- Nạp file vào bảng: chạy scripts/seed_form_templates.py (xem hướng dẫn đầu file đó).

BEGIN;

CREATE TABLE IF NOT EXISTS public.form_templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain VARCHAR(20) NOT NULL CHECK (domain IN ('land', 'business')),
    name VARCHAR(255) NOT NULL,
    procedure_name VARCHAR(255) NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    format VARCHAR(10) NOT NULL,
    media_type VARCHAR(120) NOT NULL,
    file_size INTEGER NOT NULL CHECK (file_size >= 0),
    file_data BYTEA NOT NULL,
    -- Bản PDF để trình duyệt xem được mẫu Word; NULL nếu chưa có (khi đó FE chỉ cho tải về).
    preview_pdf BYTEA,
    order_index INTEGER NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (domain, file_name)
);

CREATE INDEX IF NOT EXISTS idx_form_templates_domain ON public.form_templates (domain, order_index);

-- Giống 004: chỉ procedure-service (role postgres) đọc bảng, không mở cho anon qua Supabase REST.
ALTER TABLE public.form_templates ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.form_templates FROM anon, authenticated;

COMMIT;

-- Kiểm tra sau khi chạy seed: land 18, business 12; có bản xem trước: land 13, business 8.
SELECT domain, count(*) AS so_mau, count(preview_pdf) AS co_ban_xem_truoc
FROM public.form_templates GROUP BY domain ORDER BY domain;
