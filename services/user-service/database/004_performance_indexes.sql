-- 004_performance_indexes.sql
-- Bổ sung Functional Index cho tra cứu email/username không phân biệt hoa thường

CREATE INDEX IF NOT EXISTS idx_users_email_lower
    ON public.users (lower(email))
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_users_username_lower
    ON public.users (lower(username))
    WHERE deleted_at IS NULL;
