-- OASIS v4.4.0 upgrade
create table if not exists public.oasis_customer_history (
    id uuid primary key default gen_random_uuid(),
    owner_user_id text not null,
    business_no text not null,
    company_name text,
    source text,
    snapshot_data jsonb not null default '{}'::jsonb,
    captured_at timestamptz not null default now()
);

create index if not exists idx_oasis_customer_history_owner_business
on public.oasis_customer_history(owner_user_id, business_no, captured_at desc);

alter table public.oasis_customer_history enable row level security;
