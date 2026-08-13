-- Customer-level public procurement summaries.
--
-- Only aggregated figures are retained. Raw API responses and personal data
-- from the procurement supplier registry are intentionally not stored.

begin;

create table if not exists public.oasis_customer_procurement_summaries (
    id uuid primary key default gen_random_uuid(),
    owner_user_id text not null,
    business_no text not null,
    supplier_name text,
    supplier_unity_no text,
    query_start_ym text not null,
    query_end_ym text not null,
    total_count bigint not null default 0,
    total_amount numeric(20, 0) not null default 0,
    product_count bigint not null default 0,
    product_amount numeric(20, 0) not null default 0,
    construction_count bigint not null default 0,
    construction_amount numeric(20, 0) not null default 0,
    general_service_count bigint not null default 0,
    general_service_amount numeric(20, 0) not null default 0,
    technical_service_count bigint not null default 0,
    technical_service_amount numeric(20, 0) not null default 0,
    unclassified_count bigint not null default 0,
    unclassified_amount numeric(20, 0) not null default 0,
    source_systems jsonb not null default '[]'::jsonb,
    match_status text not null default 'not_checked',
    collected_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint oasis_customer_procurement_business_no_check
        check (business_no ~ '^[0-9]{3}-[0-9]{2}-[0-9]{5}$'),
    constraint oasis_customer_procurement_period_check
        check (
            query_start_ym ~ '^[0-9]{6}$'
            and query_end_ym ~ '^[0-9]{6}$'
            and query_start_ym <= query_end_ym
        ),
    constraint oasis_customer_procurement_status_check
        check (
            match_status in (
                'matched',
                'not_registered',
                'not_found',
                'ambiguous'
            )
        ),
    constraint oasis_customer_procurement_owner_business_unique
        unique (owner_user_id, business_no)
);

create index if not exists idx_oasis_customer_procurement_owner_updated
    on public.oasis_customer_procurement_summaries
    (owner_user_id, updated_at desc);

alter table public.oasis_customer_procurement_summaries enable row level security;

comment on table public.oasis_customer_procurement_summaries is
    'Owner-scoped aggregated procurement performance; service-role only.';
comment on column public.oasis_customer_procurement_summaries.source_systems is
    'Distinct public e-procurement system names included in the aggregate.';

revoke all on table public.oasis_customer_procurement_summaries
    from anon, authenticated, public;
grant select, insert, update, delete
    on table public.oasis_customer_procurement_summaries
    to service_role;

commit;
