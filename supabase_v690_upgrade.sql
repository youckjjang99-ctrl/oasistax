-- OASIS v6.9.0 정관 개정안 버전 저장소
create table if not exists public.oasis_articles_versions (
    id bigserial primary key,
    version_id text not null unique,
    owner_user_id text not null,
    business_no text,
    company_name text,
    profile_name text,
    version_name text,
    status text,
    original_text text,
    final_text text,
    comparison_data jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists idx_oasis_articles_versions_owner_business
on public.oasis_articles_versions(owner_user_id, business_no, created_at desc);

alter table public.oasis_articles_versions enable row level security;
