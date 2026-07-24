create table if not exists public.oasis_license_collection_runs (
    run_key text primary key,
    status text not null default 'pending'
        check (status in ('pending', 'running', 'partial', 'completed')),
    total_services integer not null default 0,
    completed_services integer not null default 0,
    failed_services integer not null default 0,
    received_count bigint not null default 0,
    saved_count bigint not null default 0,
    last_error text not null default '',
    started_at timestamptz,
    heartbeat_at timestamptz,
    completed_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.oasis_license_collection_progress (
    run_key text not null
        references public.oasis_license_collection_runs(run_key)
        on delete cascade,
    service_key text not null,
    status text not null default 'pending'
        check (status in ('pending', 'running', 'failed', 'completed')),
    next_page integer not null default 1,
    pages_processed integer not null default 0,
    received_count bigint not null default 0,
    saved_count bigint not null default 0,
    last_error text not null default '',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (run_key, service_key)
);

create index if not exists oasis_license_collection_runs_status_idx
    on public.oasis_license_collection_runs (status, updated_at desc);
create index if not exists oasis_license_collection_progress_status_idx
    on public.oasis_license_collection_progress
    (run_key, status, service_key);

alter table public.oasis_license_collection_runs enable row level security;
alter table public.oasis_license_collection_progress enable row level security;
revoke all on public.oasis_license_collection_runs from anon, authenticated;
revoke all on public.oasis_license_collection_progress from anon, authenticated;
grant all on public.oasis_license_collection_runs to service_role;
grant all on public.oasis_license_collection_progress to service_role;

select 'OASIS license collection scheduler tables ready' as result;
