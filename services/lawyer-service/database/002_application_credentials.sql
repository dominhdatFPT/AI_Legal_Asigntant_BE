-- Giấy tờ FE tải lên Document Service khi gửi hồ sơ luật sư: [{"document_id": uuid, "type": "the_luat_su" | ...}].
-- Chạy sau 001_lawyer_schema.sql. Chạy lại nhiều lần không sao.
alter table lawyer_applications add column if not exists credentials jsonb;

do $$ begin
    alter table lawyer_applications
        add constraint lawyer_applications_credentials_check
        check (credentials is null or jsonb_typeof(credentials) = 'array');
exception when duplicate_object then null;
end $$;
