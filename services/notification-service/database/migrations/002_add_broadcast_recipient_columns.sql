-- Migration 002: Bổ sung các cột người nhận cá nhân cho bảng broadcasts
ALTER TABLE public.broadcasts ADD COLUMN IF NOT EXISTS recipient_id varchar(255);
ALTER TABLE public.broadcasts ADD COLUMN IF NOT EXISTS personal_notification_id varchar(64) REFERENCES public.notifications(id) ON DELETE SET NULL;
