-- OASIS v6.3.0 녹음 상담일지 영구 저장
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
