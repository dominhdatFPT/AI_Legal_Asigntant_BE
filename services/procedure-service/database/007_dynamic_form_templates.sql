-- 007_dynamic_form_templates.sql
-- Bảng mẫu đơn dùng chung (Dynamic Form Templates): admin tạo mẫu kéo-thả,
-- công dân điền và in PDF trên trình duyệt.
-- TÁCH BIỆT với bảng form_templates (006): bảng cũ lưu file PDF/DOCX tham khảo cố định.
-- An toàn khi chạy lại nhiều lần (CREATE IF NOT EXISTS, không dùng CREATE TYPE).

BEGIN;

CREATE TABLE IF NOT EXISTS public.dynamic_form_templates (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    template_group_id   UUID NOT NULL DEFAULT gen_random_uuid(),
    version             INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    status              VARCHAR(20) NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft', 'published', 'hidden', 'archived')),
    name                VARCHAR(255) NOT NULL,
    code                VARCHAR(50),
    domain              VARCHAR(20) NOT NULL
                        CHECK (domain IN ('dat_dai', 'ho_kinh_doanh', 'doanh_nghiep')),
    procedure_id        VARCHAR(100),
    content             JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by          UUID,
    updated_by          UUID,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at          TIMESTAMPTZ,
    CONSTRAINT uq_dft_group_version UNIQUE (template_group_id, version)
);

-- Mỗi nhóm chỉ có tối đa 1 bản published hoặc hidden chưa xóa.
CREATE UNIQUE INDEX IF NOT EXISTS uidx_dft_group_active
    ON public.dynamic_form_templates (template_group_id)
    WHERE status IN ('published', 'hidden') AND deleted_at IS NULL;

-- Mỗi nhóm chỉ có tối đa 1 bản draft chưa xóa.
CREATE UNIQUE INDEX IF NOT EXISTS uidx_dft_group_draft
    ON public.dynamic_form_templates (template_group_id)
    WHERE status = 'draft' AND deleted_at IS NULL;

-- Index lọc danh sách nhanh.
CREATE INDEX IF NOT EXISTS idx_dft_domain_status
    ON public.dynamic_form_templates (domain, status);

-- Bảo mật: chỉ procedure-service (role postgres) truy cập, không mở cho anon qua Supabase REST.
ALTER TABLE public.dynamic_form_templates ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.dynamic_form_templates FROM anon, authenticated;

COMMIT;
