-- 003_procedure_user_records.sql
-- Migration: Create tables for user procedure application submissions (Land & Business)
-- Aligned with optimized Hybrid Schema (Searchable core columns + full form_data JSONB)

CREATE TABLE IF NOT EXISTS public.land_procedure_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID,
    procedure_code VARCHAR(50) NOT NULL,
    procedure_id UUID REFERENCES public.procedures(id) ON DELETE RESTRICT,
    status VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'submitted', 'processing', 'completed', 'cancelled')),
    
    -- Searchable core land columns
    applicant_name VARCHAR(255),
    applicant_id_number VARCHAR(20),
    applicant_phone VARCHAR(20),
    plot_number VARCHAR(50),
    map_sheet_number VARCHAR(50),
    land_address TEXT,
    land_area NUMERIC(12, 2),
    
    -- Detailed template fields stored as JSONB
    form_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    extra_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.business_procedure_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID,
    procedure_code VARCHAR(50) NOT NULL,
    procedure_id UUID REFERENCES public.procedures(id) ON DELETE RESTRICT,
    status VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'submitted', 'processing', 'completed', 'cancelled')),
    
    -- Searchable core business columns
    applicant_name VARCHAR(255),
    applicant_id_number VARCHAR(20),
    applicant_phone VARCHAR(20),
    business_type VARCHAR(50),
    business_name_vn VARCHAR(255),
    business_code VARCHAR(50),
    headquarters_address TEXT,
    charter_capital BIGINT,
    
    -- Detailed template fields stored as JSONB
    form_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    extra_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes for performance & foreign keys
CREATE INDEX IF NOT EXISTS idx_land_records_user_status ON public.land_procedure_records (user_id, status);
CREATE INDEX IF NOT EXISTS idx_land_records_procedure_code ON public.land_procedure_records (procedure_code, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_land_records_procedure_id ON public.land_procedure_records (procedure_id);
CREATE INDEX IF NOT EXISTS idx_land_records_form_data_gin ON public.land_procedure_records USING GIN (form_data);

CREATE INDEX IF NOT EXISTS idx_biz_records_user_status ON public.business_procedure_records (user_id, status);
CREATE INDEX IF NOT EXISTS idx_biz_records_procedure_code ON public.business_procedure_records (procedure_code, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_biz_records_procedure_id ON public.business_procedure_records (procedure_id);
CREATE INDEX IF NOT EXISTS idx_biz_records_form_data_gin ON public.business_procedure_records USING GIN (form_data);

-- Row Level Security (RLS)
ALTER TABLE public.land_procedure_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.business_procedure_records ENABLE ROW LEVEL SECURITY;

-- 1. Land Procedure Records Policies
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'land_procedure_records' AND policyname = 'land_records_select_owner'
    ) THEN
        CREATE POLICY land_records_select_owner ON public.land_procedure_records
            FOR SELECT USING (auth.uid() = user_id OR user_id IS NULL);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'land_procedure_records' AND policyname = 'land_records_insert'
    ) THEN
        CREATE POLICY land_records_insert ON public.land_procedure_records
            FOR INSERT WITH CHECK (auth.uid() = user_id OR user_id IS NULL);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'land_procedure_records' AND policyname = 'land_records_update_owner'
    ) THEN
        CREATE POLICY land_records_update_owner ON public.land_procedure_records
            FOR UPDATE USING (auth.uid() = user_id AND status = 'draft');
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'land_procedure_records' AND policyname = 'land_records_delete_owner'
    ) THEN
        CREATE POLICY land_records_delete_owner ON public.land_procedure_records
            FOR DELETE USING (auth.uid() = user_id AND status = 'draft');
    END IF;
END $$;

-- 2. Business Procedure Records Policies
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'business_procedure_records' AND policyname = 'biz_records_select_owner'
    ) THEN
        CREATE POLICY biz_records_select_owner ON public.business_procedure_records
            FOR SELECT USING (auth.uid() = user_id OR user_id IS NULL);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'business_procedure_records' AND policyname = 'biz_records_insert'
    ) THEN
        CREATE POLICY biz_records_insert ON public.business_procedure_records
            FOR INSERT WITH CHECK (auth.uid() = user_id OR user_id IS NULL);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'business_procedure_records' AND policyname = 'biz_records_update_owner'
    ) THEN
        CREATE POLICY biz_records_update_owner ON public.business_procedure_records
            FOR UPDATE USING (auth.uid() = user_id AND status = 'draft');
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'business_procedure_records' AND policyname = 'biz_records_delete_owner'
    ) THEN
        CREATE POLICY biz_records_delete_owner ON public.business_procedure_records
            FOR DELETE USING (auth.uid() = user_id AND status = 'draft');
    END IF;
END $$;

-- Cấp quyền có kiểm soát qua RLS
GRANT SELECT, INSERT, UPDATE, DELETE ON public.land_procedure_records TO authenticated;
GRANT SELECT, INSERT ON public.land_procedure_records TO anon;
GRANT ALL ON public.land_procedure_records TO service_role;

GRANT SELECT, INSERT, UPDATE, DELETE ON public.business_procedure_records TO authenticated;
GRANT SELECT, INSERT ON public.business_procedure_records TO anon;
GRANT ALL ON public.business_procedure_records TO service_role;
