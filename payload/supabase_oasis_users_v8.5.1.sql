-- OASIS v8.5.1 회원계정·승인정보 영구저장 테이블
-- Supabase SQL Editor에서 1회 실행하세요.

create table if not exists public.oasis_users (
    user_id text primary key,
    name text not null default '',
    salt text not null default '',
    password_hash text not null default '',
    role text not null default 'member',
    status text not null default 'pending',
    created_at text not null default '',
    approved_at text not null default '',
    approved_by text not null default '',
    password_changed_at text not null default ''
);

create index if not exists idx_oasis_users_status
    on public.oasis_users (status);

create index if not exists idx_oasis_users_role
    on public.oasis_users (role);

alter table public.oasis_users enable row level security;

-- 앱은 Railway의 SUPABASE_SECRET_KEY 또는 SUPABASE_SERVICE_ROLE_KEY로 접근합니다.
-- service_role은 RLS를 우회하므로 일반 anon 공개 정책을 만들지 않습니다.

comment on table public.oasis_users is
    'OASIS 로그인 계정, 회원가입 승인상태 및 비밀번호 해시 저장';
