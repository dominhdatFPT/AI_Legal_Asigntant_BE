-- =============================================================================
-- Migration 001: Hệ thống Report (Ticket & Quick Error) và Thông báo (Personal & Group/Broadcast)
-- Database: Notification Service DB (PostgreSQL / Supabase)
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- 1. Base notifications table (thông báo cá nhân)
CREATE TABLE IF NOT EXISTS public.notifications (
    id            varchar(64) PRIMARY KEY,
    user_id       varchar(64) NOT NULL,
    event_type    varchar(64) NOT NULL,
    category      varchar(24) NOT NULL DEFAULT 'system',
    priority      varchar(10) NOT NULL DEFAULT 'normal',
    title         varchar(255) NOT NULL,
    message       text NOT NULL,
    icon          varchar(64) NOT NULL DEFAULT 'bell',
    action_url    varchar(512) NOT NULL DEFAULT '/',
    source_type   varchar(24),
    source_id     varchar(64),
    metadata_json text NOT NULL DEFAULT '{}',
    is_read       boolean NOT NULL DEFAULT false,
    read_at       timestamptz,
    deleted_at    timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_notifications_user_created 
    ON public.notifications (user_id, created_at DESC) 
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_notifications_user_unread 
    ON public.notifications (user_id) 
    WHERE is_read = false AND deleted_at IS NULL;

-- 2. Danh mục nhóm vấn đề (Ticket Categories)
CREATE TABLE IF NOT EXISTS public.report_categories (
    code             varchar(40) PRIMARY KEY,
    name_vi          varchar(120) NOT NULL,
    description      text,
    allowed_roles    text NOT NULL DEFAULT 'citizen,lawyer',
    sort_order       int NOT NULL DEFAULT 0,
    is_active        boolean NOT NULL DEFAULT true
);

-- 3. Danh mục nguyên nhân cụ thể (Ticket Reasons & Quick Error Chips)
CREATE TABLE IF NOT EXISTS public.report_reasons (
    code             varchar(40) PRIMARY KEY,
    category_code    varchar(40) REFERENCES public.report_categories(code) ON DELETE SET NULL,
    kind             varchar(16) NOT NULL CHECK (kind IN ('ticket','quick_error')),
    name_vi          varchar(160) NOT NULL,
    allowed_roles    text NOT NULL DEFAULT 'citizen,lawyer',
    default_priority varchar(10) NOT NULL DEFAULT 'normal' CHECK (default_priority IN ('low','normal','high','critical')),
    requires_target  boolean NOT NULL DEFAULT false,
    requires_note    boolean NOT NULL DEFAULT false,
    sort_order       int NOT NULL DEFAULT 0,
    is_active        boolean NOT NULL DEFAULT true
);

CREATE INDEX IF NOT EXISTS ix_report_reasons_category 
    ON public.report_reasons (category_code);

-- 4. Bảng chính lưu trữ Report (Ticket & Quick Error)
CREATE TABLE IF NOT EXISTS public.reports (
    id                 varchar(64) PRIMARY KEY,
    code               varchar(24) NOT NULL UNIQUE,
    kind               varchar(16) NOT NULL CHECK (kind IN ('ticket','quick_error')),
    reporter_id        varchar(64) NOT NULL,
    reporter_role      varchar(16) NOT NULL CHECK (reporter_role IN ('citizen','lawyer')),
    category_code      varchar(40) REFERENCES public.report_categories(code) ON DELETE SET NULL,
    reason_code        varchar(40) NOT NULL REFERENCES public.report_reasons(code),
    title              varchar(200) NOT NULL,
    description        text,
    priority           varchar(10) NOT NULL DEFAULT 'normal' CHECK (priority IN ('low','normal','high','critical')),
    status             varchar(16) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','in_review','accepted','rejected','resolved','cancelled')),
    feature_code       varchar(40),
    target_type        varchar(40),
    target_id          varchar(64),
    context_json       text NOT NULL DEFAULT '{}',
    fingerprint        varchar(64),
    occurrence_count   int NOT NULL DEFAULT 1,
    duplicate_of       varchar(64) REFERENCES public.reports(id) ON DELETE SET NULL,
    assignee_id        varchar(64),
    reject_reason_code varchar(40),
    decision_note      text,
    decided_by         varchar(64),
    decided_at         timestamptz,
    resolved_at        timestamptz,
    version            int NOT NULL DEFAULT 1,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT chk_reject_reason CHECK (status <> 'rejected' OR reject_reason_code IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS ix_reports_reporter_created 
    ON public.reports (reporter_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_reports_status_priority 
    ON public.reports (status, priority, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_reports_category 
    ON public.reports (category_code, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_reports_reason 
    ON public.reports (reason_code);
CREATE INDEX IF NOT EXISTS ix_reports_duplicate_of 
    ON public.reports (duplicate_of);
CREATE INDEX IF NOT EXISTS ix_reports_fingerprint 
    ON public.reports (fingerprint, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_reports_target 
    ON public.reports (target_type, target_id);

-- 5. Tệp ảnh đính kèm của Report
CREATE TABLE IF NOT EXISTS public.report_attachments (
    id           varchar(64) PRIMARY KEY,
    report_id    varchar(64) REFERENCES public.reports(id) ON DELETE CASCADE,
    uploader_id  varchar(64) NOT NULL,
    storage_key  varchar(512) NOT NULL,
    thumb_key    varchar(512),
    file_name    varchar(255) NOT NULL,
    mime_type    varchar(64) NOT NULL CHECK (mime_type IN ('image/png','image/jpeg','image/webp')),
    size_bytes   int NOT NULL CHECK (size_bytes <= 5242880),
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_report_attachments_report 
    ON public.report_attachments (report_id);

-- 6. Lịch sử chuyển trạng thái Report (Audit Trail)
CREATE TABLE IF NOT EXISTS public.report_status_history (
    id           bigserial PRIMARY KEY,
    report_id    varchar(64) NOT NULL REFERENCES public.reports(id) ON DELETE CASCADE,
    from_status  varchar(16),
    to_status    varchar(16) NOT NULL,
    actor_id     varchar(64) NOT NULL,
    actor_role   varchar(16) NOT NULL,
    note         text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_report_history_report 
    ON public.report_status_history (report_id, created_at);

-- 7. Thông báo nhóm & toàn hệ thống (Fan-out on Read)
CREATE TABLE IF NOT EXISTS public.group_notifications (
    id           varchar(64) PRIMARY KEY,
    audience     varchar(16) NOT NULL CHECK (audience IN ('all','admin','lawyer','citizen')),
    event_type   varchar(64) NOT NULL,
    category     varchar(24) NOT NULL DEFAULT 'system',
    priority     varchar(10) NOT NULL DEFAULT 'normal',
    title        varchar(255) NOT NULL,
    message      text NOT NULL,
    icon         varchar(64) NOT NULL DEFAULT 'bell',
    action_url   varchar(512) NOT NULL DEFAULT '/',
    source_type  varchar(24),
    source_id    varchar(64),
    created_by   varchar(64),
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz,
    is_deleted   boolean NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS ix_group_notif_audience_created 
    ON public.group_notifications (audience, created_at DESC) 
    WHERE is_deleted = false;

-- 8. Trạng thái đọc / ẩn thông báo nhóm của từng user
CREATE TABLE IF NOT EXISTS public.group_notification_reads (
    group_notification_id varchar(64) NOT NULL REFERENCES public.group_notifications(id) ON DELETE CASCADE,
    user_id               varchar(64) NOT NULL,
    is_read               boolean NOT NULL DEFAULT true,
    read_at               timestamptz NOT NULL DEFAULT now(),
    hidden                boolean NOT NULL DEFAULT false,
    PRIMARY KEY (user_id, group_notification_id)
);

CREATE INDEX IF NOT EXISTS ix_group_notification_reads_group_id 
    ON public.group_notification_reads (group_notification_id);

-- 9. Bảng quản lý chiến dịch Broadcast của Admin
CREATE TABLE IF NOT EXISTS public.broadcasts (
    id                    varchar(64) PRIMARY KEY,
    title                 varchar(150) NOT NULL,
    content               text NOT NULL,
    audience              varchar(16) NOT NULL DEFAULT 'all' CHECK (audience IN ('all','admin','lawyer','citizen')),
    priority              varchar(10) NOT NULL DEFAULT 'normal' CHECK (priority IN ('normal','high','critical')),
    channels              varchar(120) NOT NULL DEFAULT 'in_app',
    action_url            varchar(512) NOT NULL DEFAULT '/',
    status                varchar(16) NOT NULL DEFAULT 'sent' CHECK (status IN ('scheduled','queued','sending','sent','cancelled','failed')),
    scheduled_at          timestamptz,
    sent_at               timestamptz,
    expires_at            timestamptz,
    group_notification_id varchar(64) REFERENCES public.group_notifications(id) ON DELETE SET NULL,
    stats_json            text NOT NULL DEFAULT '{}',
    created_by            varchar(64) NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_broadcasts_status_scheduled 
    ON public.broadcasts (status, scheduled_at);
CREATE INDEX IF NOT EXISTS ix_broadcasts_group_id 
    ON public.broadcasts (group_notification_id);

-- Enable Row Level Security (RLS)
ALTER TABLE public.notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.report_categories ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.report_reasons ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.report_attachments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.report_status_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.group_notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.group_notification_reads ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.broadcasts ENABLE ROW LEVEL SECURITY;

-- Seed default categories and reasons
INSERT INTO public.report_categories(code, name_vi, allowed_roles, sort_order) VALUES
 ('ACCOUNT', 'Tài khoản & đăng nhập', 'citizen,lawyer', 10),
 ('AI_ASSISTANT', 'Trợ lý AI (Chatbox)', 'citizen,lawyer', 20),
 ('PROCEDURE', 'Thủ tục hành chính & nộp hồ sơ', 'citizen,lawyer', 30),
 ('CONTRACT', 'Hợp đồng & biểu mẫu', 'citizen,lawyer', 40),
 ('DOCUMENT', 'Tài liệu tải lên', 'citizen,lawyer', 50),
 ('CONSULTATION', 'Phiên tư vấn với luật sư', 'citizen,lawyer', 60),
 ('LAWYER_PROGRAM', 'Hồ sơ luật sư', 'lawyer', 70),
 ('LOCATION', 'Tra cứu nơi nộp hồ sơ', 'citizen,lawyer', 80),
 ('SECURITY', 'Bảo mật & quyền riêng tư', 'citizen,lawyer', 90),
 ('SUGGESTION', 'Góp ý & đề xuất', 'citizen,lawyer', 100),
 ('OTHER', 'Khác', 'citizen,lawyer', 999)
ON CONFLICT (code) DO NOTHING;

INSERT INTO public.report_reasons(code, category_code, kind, name_vi, allowed_roles, default_priority, requires_target, requires_note, sort_order) VALUES
 ('ACC_LOGIN_FAILED', 'ACCOUNT', 'ticket', 'Không đăng nhập được / không nhận OTP', 'citizen,lawyer', 'high', false, false, 1),
 ('ACC_CCCD_VERIFY', 'ACCOUNT', 'ticket', 'Xác thực CCCD sai / bị từ chối', 'citizen,lawyer', 'normal', false, false, 2),
 ('ACC_LOCKED_UNFAIR', 'ACCOUNT', 'ticket', 'Tài khoản bị khóa không rõ lý do', 'citizen,lawyer', 'high', false, false, 3),
 ('ACC_PROFILE_WRONG', 'ACCOUNT', 'ticket', 'Thông tin hồ sơ cá nhân hiển thị sai', 'citizen,lawyer', 'low', false, false, 4),
 ('AI_WRONG_ANSWER', 'AI_ASSISTANT', 'ticket', 'Trả lời sai nội dung pháp lý', 'citizen,lawyer', 'high', false, false, 1),
 ('AI_OUTDATED_LAW', 'AI_ASSISTANT', 'ticket', 'Trích dẫn luật đã hết hiệu lực', 'citizen,lawyer', 'high', false, false, 2),
 ('AI_HARMFUL', 'AI_ASSISTANT', 'ticket', 'Nội dung không phù hợp / độc hại', 'citizen,lawyer', 'high', false, false, 3),
 ('AI_NO_RESPONSE', 'AI_ASSISTANT', 'ticket', 'Không phản hồi / trả lời bị cắt', 'citizen,lawyer', 'normal', false, false, 4),
 ('PRO_FORM_TEMPLATE_WRONG', 'PROCEDURE', 'ticket', 'Mẫu đơn sai quy định / sai phiên bản', 'citizen,lawyer', 'high', false, false, 1),
 ('PRO_MISSING_FIELD', 'PROCEDURE', 'ticket', 'Thiếu trường thông tin cần thiết', 'citizen,lawyer', 'normal', false, false, 2),
 ('PRO_SUBMIT_FAILED', 'PROCEDURE', 'ticket', 'Không nộp được hồ sơ', 'citizen,lawyer', 'high', false, false, 3),
 ('PRO_DOSSIER_PDF_WRONG', 'PROCEDURE', 'ticket', 'File PDF / mã QR hồ sơ bị sai', 'citizen,lawyer', 'normal', false, false, 4),
 ('CON_TEMPLATE_WRONG', 'CONTRACT', 'ticket', 'Nội dung mẫu hợp đồng sai', 'citizen,lawyer', 'normal', false, false, 1),
 ('CON_EXPORT_FAILED', 'CONTRACT', 'ticket', 'Xuất file DOCX/PDF lỗi', 'citizen,lawyer', 'normal', false, false, 2),
 ('CON_FORMAT_BROKEN', 'CONTRACT', 'ticket', 'Định dạng / font chữ bị vỡ', 'citizen,lawyer', 'low', false, false, 3),
 ('DOC_UPLOAD_FAILED', 'DOCUMENT', 'ticket', 'Không tải lên được', 'citizen,lawyer', 'normal', false, false, 1),
 ('DOC_OCR_WRONG', 'DOCUMENT', 'ticket', 'Trích xuất (OCR) sai dữ liệu', 'citizen,lawyer', 'normal', false, false, 2),
 ('CSL_LAWYER_CONDUCT', 'CONSULTATION', 'ticket', 'Luật sư có thái độ / hành vi không đúng', 'citizen', 'high', true, false, 1),
 ('CSL_CLIENT_CONDUCT', 'CONSULTATION', 'ticket', 'Người dùng có hành vi xúc phạm / quấy rối', 'lawyer', 'high', true, false, 2),
 ('CSL_OFF_PLATFORM_PAYMENT', 'CONSULTATION', 'ticket', 'Yêu cầu giao dịch/chuyển khoản ngoài nền tảng', 'citizen,lawyer', 'high', true, false, 3),
 ('CSL_SPAM', 'CONSULTATION', 'ticket', 'Spam / quảng cáo', 'citizen,lawyer', 'normal', true, false, 4),
 ('CSL_CONNECTION', 'CONSULTATION', 'ticket', 'Lỗi kết nối, mất tin nhắn', 'citizen,lawyer', 'normal', false, false, 5),
 ('LAW_REVIEW_DELAY', 'LAWYER_PROGRAM', 'ticket', 'Hồ sơ đăng ký chậm được duyệt', 'lawyer', 'normal', false, false, 1),
 ('LAW_PROFILE_WRONG', 'LAWYER_PROGRAM', 'ticket', 'Thông tin chứng chỉ / hồ sơ hiển thị sai', 'lawyer', 'normal', false, false, 2),
 ('LAW_CASE_ASSIGNMENT', 'LAWYER_PROGRAM', 'ticket', 'Lỗi nhận / phân công yêu cầu tư vấn', 'lawyer', 'normal', false, false, 3),
 ('LOC_WRONG_ADDRESS', 'LOCATION', 'ticket', 'Sai địa chỉ / giờ làm việc cơ quan', 'citizen,lawyer', 'low', false, false, 1),
 ('SEC_DATA_LEAK', 'SECURITY', 'ticket', 'Thấy dữ liệu của người khác', 'citizen,lawyer', 'critical', false, false, 1),
 ('SEC_VULNERABILITY', 'SECURITY', 'ticket', 'Phát hiện lỗ hổng bảo mật', 'citizen,lawyer', 'critical', false, false, 2),
 ('SUG_FEATURE', 'SUGGESTION', 'ticket', 'Đề xuất tính năng mới', 'citizen,lawyer', 'low', false, false, 1),
 ('SUG_UX', 'SUGGESTION', 'ticket', 'Góp ý giao diện / trải nghiệm', 'citizen,lawyer', 'low', false, false, 2),
 ('OTH_OTHER', 'OTHER', 'ticket', 'Vấn đề khác', 'citizen,lawyer', 'normal', false, true, 1),
 ('QE_CANNOT_LOAD', NULL, 'quick_error', 'Không tải được trang / dữ liệu', 'citizen,lawyer', 'normal', false, false, 1),
 ('QE_NO_RESPONSE', NULL, 'quick_error', 'Bấm nút không phản hồi', 'citizen,lawyer', 'normal', false, false, 2),
 ('QE_WRONG_RESULT', NULL, 'quick_error', 'Kết quả hiển thị sai', 'citizen,lawyer', 'normal', false, false, 3),
 ('QE_SAVE_FAILED', NULL, 'quick_error', 'Lưu / gửi không thành công', 'citizen,lawyer', 'high', false, false, 4),
 ('QE_FILE_ERROR', NULL, 'quick_error', 'Tải lên / tải xuống file lỗi', 'citizen,lawyer', 'normal', false, false, 5),
 ('QE_AI_ERROR', NULL, 'quick_error', 'AI trả lời sai / không trả lời', 'citizen,lawyer', 'normal', false, false, 6),
 ('QE_UI_BROKEN', NULL, 'quick_error', 'Giao diện bị vỡ / hiển thị lỗi', 'citizen,lawyer', 'low', false, false, 7),
 ('QE_TOO_SLOW', NULL, 'quick_error', 'Quá chậm', 'citizen,lawyer', 'low', false, false, 8),
 ('QE_OTHER', NULL, 'quick_error', 'Lỗi khác', 'citizen,lawyer', 'normal', false, true, 9)
ON CONFLICT (code) DO NOTHING;
