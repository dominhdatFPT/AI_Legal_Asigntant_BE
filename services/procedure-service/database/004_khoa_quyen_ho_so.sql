-- 004_khoa_quyen_ho_so.sql
-- Chạy sau 003. An toàn khi chạy lại nhiều lần.
-- Bản 003 cũ cấp SELECT/INSERT/UPDATE cho anon (USING true): ai có anon key cũng đọc được CCCD, SĐT
-- của mọi hồ sơ qua Supabase REST. File này gỡ các quyền đó; procedure-service vẫn đọc/ghi bình thường
-- vì kết nối bằng role postgres (bỏ qua RLS). Quyền "chỉ xem hồ sơ của mình" kiểm tra ở API theo JWT.

BEGIN;

ALTER TABLE public.land_procedure_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.business_procedure_records ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS land_records_select_all ON public.land_procedure_records;
DROP POLICY IF EXISTS land_records_insert_all ON public.land_procedure_records;
DROP POLICY IF EXISTS land_records_update_all ON public.land_procedure_records;
DROP POLICY IF EXISTS biz_records_select_all ON public.business_procedure_records;
DROP POLICY IF EXISTS biz_records_insert_all ON public.business_procedure_records;
DROP POLICY IF EXISTS biz_records_update_all ON public.business_procedure_records;

REVOKE ALL ON public.land_procedure_records FROM anon, authenticated;
REVOKE ALL ON public.business_procedure_records FROM anon, authenticated;

-- "Hồ sơ của tôi" lọc theo user_id và sắp theo ngày nộp mới nhất.
CREATE INDEX IF NOT EXISTS idx_land_records_user_created ON public.land_procedure_records (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_biz_records_user_created ON public.business_procedure_records (user_id, created_at DESC);

COMMIT;

-- Kiểm tra: 2 dòng rls_enabled = true, 0 policy, anon không còn quyền.
SELECT relname, relrowsecurity AS rls_enabled
FROM pg_class WHERE relname IN ('land_procedure_records', 'business_procedure_records');
SELECT count(*) AS so_policy_con_lai FROM pg_policies
WHERE tablename IN ('land_procedure_records', 'business_procedure_records');
SELECT table_name, privilege_type FROM information_schema.role_table_grants
WHERE grantee = 'anon' AND table_name IN ('land_procedure_records', 'business_procedure_records');
