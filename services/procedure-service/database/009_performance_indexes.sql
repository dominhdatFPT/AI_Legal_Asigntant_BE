-- 009_performance_indexes.sql
-- Bổ sung các index tối ưu truy vấn cho procedure-service (báo cáo sự cố 429 & tối ưu hệ thống)

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 1. Functional index cho tra cứu mã thủ tục không phân biệt hoa thường
CREATE INDEX IF NOT EXISTS idx_procedures_code_upper
    ON public.procedures (upper(code));

-- 2. Index đơn trên cột status phục vụ count_records() và lọc theo trạng thái của Admin
CREATE INDEX IF NOT EXISTS idx_land_records_status_only
    ON public.land_procedure_records (status);

CREATE INDEX IF NOT EXISTS idx_biz_records_status_only
    ON public.business_procedure_records (status);

-- 3. Partial index cho các hồ sơ đang xử lý (draft, submitted, processing)
CREATE INDEX IF NOT EXISTS idx_land_records_active
    ON public.land_procedure_records (created_at DESC)
    WHERE status IN ('draft', 'submitted', 'processing');

CREATE INDEX IF NOT EXISTS idx_biz_records_active
    ON public.business_procedure_records (created_at DESC)
    WHERE status IN ('draft', 'submitted', 'processing');

-- 4. GIN Trigram index tăng tốc tìm kiếm LIKE '%...%' theo họ tên người nộp hồ sơ
CREATE INDEX IF NOT EXISTS idx_land_records_name_trgm
    ON public.land_procedure_records USING gin (lower(applicant_name) gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_biz_records_name_trgm
    ON public.business_procedure_records USING gin (lower(applicant_name) gin_trgm_ops);

-- 5. Index cho custom_form_templates khi truy vấn danh sách mẫu đã xuất bản
CREATE INDEX IF NOT EXISTS idx_custom_form_templates_published
    ON public.custom_form_templates (domain, status, name)
    WHERE deleted_at IS NULL;
