create table if not exists public.oasis_policy_repository (
    id bigserial primary key,
    record_id text not null unique,
    source_type text not null,
    source_name text,
    title text not null,
    agency text,
    active boolean not null default true,
    raw_data jsonb not null default '{}'::jsonb,
    updated_at timestamptz not null default now(),
    created_at timestamptz not null default now()
);
create index if not exists idx_oasis_policy_repository_source
on public.oasis_policy_repository(source_type, active, updated_at desc);
alter table public.oasis_policy_repository enable row level security;
