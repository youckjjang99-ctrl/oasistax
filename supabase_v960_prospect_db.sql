-- OASIS CRM v9.6.0 영업후보DB
-- Supabase SQL Editor에서 한 번 실행합니다.

create extension if not exists pgcrypto;

create table if not exists public.oasis_prospect_companies (
    id uuid primary key default gen_random_uuid(),
    source text not null default 'nps_workplace_v2',
    source_key text not null,
    business_no text,
    company_name text not null default '',
    address text not null default '',
    region text not null default '',
    industry_code text not null default '',
    industry_name text not null default '',
    employee_count integer not null default 0,
    new_employee_count integer not null default 0,
    lost_employee_count integer not null default 0,
    monthly_notice_amount bigint not null default 0,
    data_created_ym text not null default '',
    priority_score integer not null default 0,
    priority_reasons jsonb not null default '[]'::jsonb,
    status text not null default 'candidate',
    owner_user_id text not null default '',
    source_data jsonb not null default '{}'::jsonb,
    collected_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (source, source_key)
);

create index if not exists idx_oasis_prospects_priority
on public.oasis_prospect_companies (priority_score desc, updated_at desc);

create index if not exists idx_oasis_prospects_business_no
on public.oasis_prospect_companies (business_no);

create index if not exists idx_oasis_prospects_region
on public.oasis_prospect_companies (region);

create index if not exists idx_oasis_prospects_status
on public.oasis_prospect_companies (status);

alter table public.oasis_prospect_companies enable row level security;

comment on table public.oasis_prospect_companies is
'OASIS CRM 공공데이터 기반 영업후보 전용 테이블. 기존 고객DB와 분리 저장.';

