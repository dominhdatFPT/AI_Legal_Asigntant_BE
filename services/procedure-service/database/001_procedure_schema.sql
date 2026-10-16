CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE TABLE IF NOT EXISTS procedure_domains (
    code VARCHAR(50) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    icon VARCHAR(100),
    order_index INTEGER NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS procedures (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(50) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    domain VARCHAR(20) NOT NULL CHECK (domain IN ('land', 'business')),
    description TEXT,
    processing_days INTEGER CHECK (processing_days >= 0),
    submission_channels TEXT[] NOT NULL DEFAULT ARRAY['direct']::TEXT[],
    receiving_agency_level VARCHAR(20) CHECK (receiving_agency_level IN ('commune', 'province', 'district')),
    fee BIGINT CHECK (fee >= 0),
    fee_note TEXT,
    effective_from DATE,
    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'draft', 'deprecated')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS procedure_conditions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    procedure_id UUID NOT NULL REFERENCES procedures(id) ON DELETE CASCADE,
    condition_code VARCHAR(50),
    title VARCHAR(255) NOT NULL,
    description TEXT,
    order_index INTEGER NOT NULL DEFAULT 0,
    is_mandatory BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS procedure_fees (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    procedure_id UUID NOT NULL REFERENCES procedures(id) ON DELETE CASCADE,
    fee_type VARCHAR(50) NOT NULL CHECK (fee_type IN ('administrative_fee', 'appraisal_fee', 'other')),
    name VARCHAR(255) NOT NULL,
    amount BIGINT CHECK (amount >= 0),
    unit VARCHAR(50) NOT NULL DEFAULT 'ho_so',
    is_exemptible BOOLEAN NOT NULL DEFAULT false,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS procedure_legal_bases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    procedure_id UUID NOT NULL REFERENCES procedures(id) ON DELETE CASCADE,
    doc_number VARCHAR(100) NOT NULL,
    title VARCHAR(500) NOT NULL,
    article_ref VARCHAR(255),
    source_url TEXT,
    order_index INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS procedure_requirements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    procedure_id UUID NOT NULL REFERENCES procedures(id) ON DELETE CASCADE,
    name VARCHAR(500) NOT NULL,
    description TEXT,
    is_required BOOLEAN NOT NULL DEFAULT true,
    copies INTEGER NOT NULL DEFAULT 1 CHECK (copies >= 1),
    document_form VARCHAR(50) NOT NULL DEFAULT 'original' CHECK (document_form IN ('original', 'copy', 'certified_copy')),
    form_code VARCHAR(50),
    note TEXT,
    order_index INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes
-- Không tạo index GIN(unaccent(...)): unaccent() không IMMUTABLE nên Postgres báo lỗi và dừng script.
-- Bảng thủ tục chỉ vài chục dòng; service tìm kiếm không dấu trong Python.
CREATE INDEX IF NOT EXISTS ix_procedures_domain_status ON procedures (domain, status);

CREATE INDEX IF NOT EXISTS ix_procedure_conditions_procedure_id ON procedure_conditions(procedure_id);
CREATE INDEX IF NOT EXISTS ix_procedure_fees_procedure_id ON procedure_fees(procedure_id);
CREATE INDEX IF NOT EXISTS ix_procedure_legal_bases_procedure_id ON procedure_legal_bases(procedure_id);
CREATE INDEX IF NOT EXISTS ix_procedure_requirements_procedure_id ON procedure_requirements(procedure_id);

-- Trigger updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
   NEW.updated_at = now();
   RETURN NEW;
END;
$$ language 'plpgsql';

DO $$ BEGIN
    CREATE TRIGGER update_procedures_updated_at
        BEFORE UPDATE ON procedures
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at_column();
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Enable RLS
ALTER TABLE procedures ENABLE ROW LEVEL SECURITY;
ALTER TABLE procedures FORCE ROW LEVEL SECURITY;
ALTER TABLE procedure_conditions ENABLE ROW LEVEL SECURITY;
ALTER TABLE procedure_conditions FORCE ROW LEVEL SECURITY;
ALTER TABLE procedure_fees ENABLE ROW LEVEL SECURITY;
ALTER TABLE procedure_fees FORCE ROW LEVEL SECURITY;
ALTER TABLE procedure_legal_bases ENABLE ROW LEVEL SECURITY;
ALTER TABLE procedure_legal_bases FORCE ROW LEVEL SECURITY;
ALTER TABLE procedure_requirements ENABLE ROW LEVEL SECURITY;
ALTER TABLE procedure_requirements FORCE ROW LEVEL SECURITY;

-- Grant permissions
GRANT USAGE ON SCHEMA public TO anon, authenticated;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO anon, authenticated;
GRANT INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO authenticated, service_role;

-- Remove old policies to be idempotent
DROP POLICY IF EXISTS "Public can view active procedures" ON procedures;
DROP POLICY IF EXISTS "Admin can view all procedures" ON procedures;
DROP POLICY IF EXISTS "Admins can insert procedures" ON procedures;
DROP POLICY IF EXISTS "Admins can update procedures" ON procedures;
DROP POLICY IF EXISTS "Admins can delete procedures" ON procedures;

DROP POLICY IF EXISTS "Public can view related procedure_conditions" ON procedure_conditions;
DROP POLICY IF EXISTS "Admins can insert procedure_conditions" ON procedure_conditions;
DROP POLICY IF EXISTS "Admins can update procedure_conditions" ON procedure_conditions;
DROP POLICY IF EXISTS "Admins can delete procedure_conditions" ON procedure_conditions;

DROP POLICY IF EXISTS "Public can view related procedure_fees" ON procedure_fees;
DROP POLICY IF EXISTS "Admins can insert procedure_fees" ON procedure_fees;
DROP POLICY IF EXISTS "Admins can update procedure_fees" ON procedure_fees;
DROP POLICY IF EXISTS "Admins can delete procedure_fees" ON procedure_fees;

DROP POLICY IF EXISTS "Public can view related procedure_legal_bases" ON procedure_legal_bases;
DROP POLICY IF EXISTS "Admins can insert procedure_legal_bases" ON procedure_legal_bases;
DROP POLICY IF EXISTS "Admins can update procedure_legal_bases" ON procedure_legal_bases;
DROP POLICY IF EXISTS "Admins can delete procedure_legal_bases" ON procedure_legal_bases;

DROP POLICY IF EXISTS "Public can view related procedure_requirements" ON procedure_requirements;
DROP POLICY IF EXISTS "Admins can insert procedure_requirements" ON procedure_requirements;
DROP POLICY IF EXISTS "Admins can update procedure_requirements" ON procedure_requirements;
DROP POLICY IF EXISTS "Admins can delete procedure_requirements" ON procedure_requirements;

-- Add Policies (using auth caching for performance as best practice)
-- SELECT
CREATE POLICY "Public can view active procedures" ON procedures FOR SELECT TO anon, authenticated USING (status = 'active');
CREATE POLICY "Admin can view all procedures" ON procedures FOR SELECT TO authenticated USING (
    (SELECT coalesce(auth.jwt() -> 'app_metadata' ->> 'role', '')) = 'admin' OR (SELECT auth.jwt() ->> 'role') = 'service_role'
);

CREATE POLICY "Public can view related procedure_conditions" ON procedure_conditions FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY "Public can view related procedure_fees" ON procedure_fees FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY "Public can view related procedure_legal_bases" ON procedure_legal_bases FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY "Public can view related procedure_requirements" ON procedure_requirements FOR SELECT TO anon, authenticated USING (true);

-- Admin CUD policies generator block
DO $$
DECLARE
    tbl text;
BEGIN
    FOR tbl IN SELECT unnest(ARRAY['procedures', 'procedure_conditions', 'procedure_fees', 'procedure_legal_bases', 'procedure_requirements'])
    LOOP
        EXECUTE format('CREATE POLICY "Admins can insert %I" ON %I FOR INSERT TO authenticated WITH CHECK (
            (SELECT coalesce(auth.jwt() -> ''app_metadata'' ->> ''role'', '''')) = ''admin'' OR (SELECT auth.jwt() ->> ''role'') = ''service_role''
        )', tbl, tbl);
        EXECUTE format('CREATE POLICY "Admins can update %I" ON %I FOR UPDATE TO authenticated USING (
            (SELECT coalesce(auth.jwt() -> ''app_metadata'' ->> ''role'', '''')) = ''admin'' OR (SELECT auth.jwt() ->> ''role'') = ''service_role''
        ) WITH CHECK (
            (SELECT coalesce(auth.jwt() -> ''app_metadata'' ->> ''role'', '''')) = ''admin'' OR (SELECT auth.jwt() ->> ''role'') = ''service_role''
        )', tbl, tbl);
        EXECUTE format('CREATE POLICY "Admins can delete %I" ON %I FOR DELETE TO authenticated USING (
            (SELECT coalesce(auth.jwt() -> ''app_metadata'' ->> ''role'', '''')) = ''admin'' OR (SELECT auth.jwt() ->> ''role'') = ''service_role''
        )', tbl, tbl);
    END LOOP;
END $$;
