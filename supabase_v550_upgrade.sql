create table if not exists public.oasis_ai_usage (
    id bigserial primary key,
    event_id text not null unique,
    owner_user_id text not null,
    user_name text,
    feature text,
    operation text,
    model text,
    company_name text,
    business_no text,
    cached boolean not null default false,
    audio_minutes numeric not null default 0,
    input_tokens bigint not null default 0,
    output_tokens bigint not null default 0,
    estimated_cost_usd numeric not null default 0,
    saved_cost_usd numeric not null default 0,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists idx_oasis_ai_usage_created_at
on public.oasis_ai_usage(created_at desc);

create index if not exists idx_oasis_ai_usage_owner
on public.oasis_ai_usage(owner_user_id, created_at desc);

alter table public.oasis_ai_usage enable row level security;
