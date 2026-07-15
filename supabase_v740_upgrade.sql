-- OASIS v7.4.0 직원현황 저장
create table if not exists public.oasis_employee_rosters (
    id bigserial primary key,
    version_id text not null unique,
    owner_user_id text not null,
    business_no text,
    company_name text,
    filename text,
    uploaded_at timestamptz not null default now(),
    summary_data jsonb not null default '{}'::jsonb,
    employee_data jsonb not null default '[]'::jsonb,
    parse_info jsonb not null default '{}'::jsonb
);

create index if not exists idx_oasis_employee_rosters_owner_business
on public.oasis_employee_rosters(
    owner_user_id,
    business_no,
    uploaded_at desc
);

alter table public.oasis_employee_rosters enable row level security;
