-- OASIS v6.3.2 combined upgrade
-- Safe to run more than once.

create table if not exists public.oasis_consultation_journals (
    id bigserial primary key,
    journal_id text not null unique,
    owner_user_id text not null,
    company_name text,
    business_no text,
    consultant_name text,
    consultation_title text,
    summary text,
    saved_at timestamptz not null default now(),
    journal_data jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_oasis_consultation_journals_owner_business
on public.oasis_consultation_journals(owner_user_id, business_no, saved_at desc);

create index if not exists idx_oasis_consultation_journals_owner_saved
on public.oasis_consultation_journals(owner_user_id, saved_at desc);

alter table public.oasis_consultation_journals enable row level security;

create table if not exists public.oasis_consultation_ai_cache (
    id bigserial primary key,
    owner_user_id text not null,
    cache_key text not null,
    cache_type text not null,
    cache_data jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique(owner_user_id, cache_key)
);

create index if not exists idx_oasis_consultation_ai_cache_owner_type
on public.oasis_consultation_ai_cache(owner_user_id, cache_type, updated_at desc);

alter table public.oasis_consultation_ai_cache enable row level security;
