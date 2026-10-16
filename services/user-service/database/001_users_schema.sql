-- User Service: bảng users + refresh_tokens.
-- Chạy được nhiều lần (idempotent): bảng chưa có thì tạo, đã có thì chỉ bổ sung cột còn thiếu.
-- Cách chạy: Supabase Dashboard → SQL Editor → dán toàn bộ file → Run.

create extension if not exists pgcrypto;

do $$ begin
    create type user_role as enum ('citizen', 'lawyer', 'admin');
exception when duplicate_object then null;
end $$;

do $$ begin
    create type user_status as enum ('active', 'locked');
exception when duplicate_object then null;
end $$;

create table if not exists users (
    id            uuid primary key default gen_random_uuid(),
    firebase_uid  varchar unique,              -- trống với tài khoản đăng nhập bằng mật khẩu (admin...)
    auth_provider varchar,                     -- google | password | local
    email         varchar not null unique,
    username      varchar,                     -- tên đăng nhập cho tài khoản mật khẩu
    password_hash varchar,                     -- bcrypt hash, KHÔNG lưu mật khẩu gốc
    full_name     varchar,
    avatar_url    varchar,
    phone         varchar,
    role          user_role   not null default 'citizen',
    status        user_status not null default 'active',
    locked_reason text,
    locked_by     uuid references users(id),
    locked_at     timestamptz,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),
    deleted_at    timestamptz
);

-- Bảng users đã tồn tại từ trước: bổ sung cột đăng nhập bằng mật khẩu.
alter table users add column if not exists username varchar;
alter table users add column if not exists password_hash varchar;
alter table users alter column firebase_uid drop not null;

-- Username không trùng, không phân biệt hoa thường.
create unique index if not exists users_username_lower_key on users (lower(username));

create table if not exists refresh_tokens (
    id         uuid primary key default gen_random_uuid(),
    user_id    uuid not null references users(id) on delete cascade,
    token_hash varchar not null unique,
    expires_at timestamptz not null,
    revoked_at timestamptz,
    user_agent varchar,
    created_at timestamptz not null default now()
);

create index if not exists ix_refresh_tokens_user_id on refresh_tokens (user_id);
