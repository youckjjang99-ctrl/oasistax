-- OASIS CRM v9.9.3
-- 행정안전부 인허가 업체, 국민연금 고용 스냅샷, 공개 사업장 연락처를
-- 하나의 업체 기준으로 연결하기 위한 통합 데이터 모델입니다.

create extension if not exists pgcrypto;

create table if not exists public.oasis_company_master (
    id uuid primary key default gen_random_uuid(),
    company_key text not null unique,
    normalized_name text not null,
    normalized_address text not null,
    company_name text not null default '',
    address text not null default '',
    province text not null default '',
    district text not null default '',
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check (company_key <> ''),
    check (normalized_name <> ''),
    check (normalized_address <> '')
);

create index if not exists oasis_company_master_name_address_idx
on public.oasis_company_master (normalized_name, normalized_address);

create index if not exists oasis_company_master_region_idx
on public.oasis_company_master (province, district, is_active);

create table if not exists public.oasis_company_source_links (
    company_id uuid not null
        references public.oasis_company_master(id) on delete cascade,
    source_type text not null,
    source_key text not null,
    source_record_id uuid,
    match_method text not null default 'normalized_name_address',
    match_confidence integer not null default 100,
    linked_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (source_type, source_key),
    check (source_type in ('localdata', 'nps')),
    check (match_confidence between 0 and 100)
);

create index if not exists oasis_company_source_links_company_idx
on public.oasis_company_source_links (company_id, source_type);

create index if not exists oasis_company_source_links_record_idx
on public.oasis_company_source_links (source_record_id)
where source_record_id is not null;

create table if not exists public.oasis_company_contacts (
    id bigint generated always as identity primary key,
    company_id uuid not null
        references public.oasis_company_master(id) on delete cascade,
    contact_type text not null default 'phone',
    contact_value text not null,
    source text not null,
    source_url text not null default '',
    confidence integer not null default 0,
    is_primary boolean not null default false,
    checked_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (company_id, contact_type, contact_value),
    check (contact_type in ('phone', 'email', 'website')),
    check (contact_value <> ''),
    check (confidence between 0 and 100)
);

create index if not exists oasis_company_contacts_best_phone_idx
on public.oasis_company_contacts (
    company_id,
    contact_type,
    is_primary desc,
    confidence desc,
    updated_at desc
);

alter table public.oasis_nps_employee_snapshots
    add column if not exists company_id uuid,
    add column if not exists normalized_name text not null default '',
    add column if not exists normalized_address text not null default '',
    add column if not exists business_no text not null default '',
    add column if not exists new_employee_count integer not null default 0,
    add column if not exists lost_employee_count integer not null default 0,
    add column if not exists match_status text not null default 'pending',
    add column if not exists match_method text not null default '',
    add column if not exists match_confidence integer not null default 0,
    add column if not exists matched_at timestamptz;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'oasis_nps_snapshots_company_fkey'
          and conrelid = 'public.oasis_nps_employee_snapshots'::regclass
    ) then
        alter table public.oasis_nps_employee_snapshots
        add constraint oasis_nps_snapshots_company_fkey
        foreign key (company_id)
        references public.oasis_company_master(id)
        on delete set null;
    end if;
end $$;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'oasis_nps_snapshots_match_confidence_check'
          and conrelid = 'public.oasis_nps_employee_snapshots'::regclass
    ) then
        alter table public.oasis_nps_employee_snapshots
        add constraint oasis_nps_snapshots_match_confidence_check
        check (match_confidence between 0 and 100);
    end if;
end $$;

create index if not exists oasis_nps_snapshots_company_month_idx
on public.oasis_nps_employee_snapshots (
    company_id,
    data_created_ym desc
)
include (employee_count, new_employee_count, lost_employee_count)
where company_id is not null;

create index if not exists oasis_nps_snapshots_match_queue_idx
on public.oasis_nps_employee_snapshots (
    match_status,
    data_created_ym desc,
    snapshot_identity
)
where company_id is null;

create index if not exists oasis_nps_snapshots_normalized_idx
on public.oasis_nps_employee_snapshots (
    normalized_name,
    normalized_address,
    data_created_ym desc
);

create table if not exists public.oasis_company_pipeline_runs (
    run_key text primary key,
    stage text not null,
    status text not null default 'running',
    cursor_value text not null default '',
    processed_count bigint not null default 0,
    matched_count bigint not null default 0,
    contact_count bigint not null default 0,
    stats jsonb not null default '{}'::jsonb,
    last_error text not null default '',
    started_at timestamptz not null default now(),
    heartbeat_at timestamptz not null default now(),
    completed_at timestamptz,
    updated_at timestamptz not null default now(),
    check (status in ('running', 'completed', 'partial', 'failed'))
);

drop view if exists public.oasis_growth_crm_leads;

create view public.oasis_growth_crm_leads
with (security_invoker = true)
as
with employee_history as (
    select
        s.*,
        lag(s.employee_count) over (
            partition by coalesce(s.company_id::text, s.snapshot_identity)
            order by s.data_created_ym
        ) as previous_employee_count,
        row_number() over (
            partition by coalesce(s.company_id::text, s.snapshot_identity)
            order by s.data_created_ym desc
        ) as latest_rank
    from public.oasis_nps_employee_snapshots s
),
latest as (
    select
        h.*,
        case
            when h.previous_employee_count is not null
                then h.employee_count - h.previous_employee_count
            else h.new_employee_count - h.lost_employee_count
        end as employee_growth,
        case
            when h.previous_employee_count is not null
                then 'monthly_snapshot'
            else 'acquisition_loss'
        end as growth_basis
    from employee_history h
    where h.latest_rank = 1
)
select
    l.company_id,
    coalesce(m.company_name, l.company_name) as company_name,
    coalesce(m.address, l.address) as address,
    coalesce(m.province, '') as province,
    coalesce(m.district, '') as district,
    coalesce(m.is_active, true) as is_active,
    l.data_created_ym,
    l.employee_count,
    l.previous_employee_count,
    l.new_employee_count,
    l.lost_employee_count,
    l.employee_growth,
    case
        when coalesce(l.previous_employee_count, 0) > 0
            then round(
                (l.employee_growth::numeric
                    / l.previous_employee_count::numeric) * 100,
                1
            )
        else null
    end as employee_growth_rate,
    l.growth_basis,
    l.match_status,
    l.match_method,
    l.match_confidence,
    contact.contact_value as phone,
    contact.source as phone_source,
    contact.source_url as phone_source_url,
    contact.confidence as phone_confidence,
    license.industry_names,
    license.category_names,
    license.license_count,
    license.latest_license_date
from latest l
left join public.oasis_company_master m
    on m.id = l.company_id
left join lateral (
    select
        c.contact_value,
        c.source,
        c.source_url,
        c.confidence
    from public.oasis_company_contacts c
    where c.company_id = l.company_id
      and c.contact_type = 'phone'
    order by
        c.is_primary desc,
        c.confidence desc,
        c.updated_at desc
    limit 1
) contact on true
left join lateral (
    select
        array_agg(distinct b.industry_name)
            filter (where b.industry_name <> '') as industry_names,
        array_agg(distinct b.category)
            filter (where b.category <> '') as category_names,
        count(*) as license_count,
        max(b.license_date) as latest_license_date
    from public.oasis_company_source_links sl
    join public.oasis_licensed_businesses b
      on b.id = sl.source_record_id
    where sl.company_id = l.company_id
      and sl.source_type = 'localdata'
) license on true
where l.employee_growth > 0;

alter table public.oasis_company_master enable row level security;
alter table public.oasis_company_source_links enable row level security;
alter table public.oasis_company_contacts enable row level security;
alter table public.oasis_company_pipeline_runs enable row level security;

comment on table public.oasis_company_master is
    '행안부·국민연금·연락처 원천을 연결하는 OASIS 통합 업체 마스터';
comment on table public.oasis_company_source_links is
    '통합 업체와 행안부/국민연금 원천 레코드 연결 이력';
comment on table public.oasis_company_contacts is
    '상호·주소 일치 검증을 통과한 공개 사업장 연락처';
comment on view public.oasis_growth_crm_leads is
    '최근 고용인원이 증가한 통합 업체의 CRM 영업 후보 목록';
