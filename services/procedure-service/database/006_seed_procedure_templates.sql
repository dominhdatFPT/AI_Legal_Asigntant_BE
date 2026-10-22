-- 006_seed_procedure_templates.sql
-- Cập nhật bảng form_templates và cấu hình RLS cho Kho mẫu đơn tham khảo

ALTER TABLE IF EXISTS public.form_templates ADD COLUMN IF NOT EXISTS procedure_code VARCHAR(50);
CREATE INDEX IF NOT EXISTS idx_form_templates_procedure_code ON public.form_templates(procedure_code);
CREATE INDEX IF NOT EXISTS idx_form_templates_domain_active ON public.form_templates(domain, is_active);

-- Thiết lập RLS cho phép truy cập công khai tra cứu biểu mẫu
ALTER TABLE IF EXISTS public.form_templates ENABLE ROW LEVEL SECURITY;

DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'form_templates' AND policyname = 'Allow public read form_templates'
    ) THEN
        CREATE POLICY "Allow public read form_templates" ON public.form_templates FOR SELECT USING (true);
    END IF;
END $$;

-- Ghi chú: Dữ liệu nhị phân binary (BYTEA) của 43 biểu mẫu đã được nạp tự động qua script seed_templates_db.py
