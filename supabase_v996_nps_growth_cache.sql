-- 두 월 모두 확인된 사업장 중 고용인원 증가 업체의 사전계산 캐시
create table if not exists public.oasis_nps_growth_leads (
    snapshot_identity text not null,
    current_ym text not null check (current_ym ~ '^[0-9]{6}$'),
    previous_ym text not null check (previous_ym ~ '^[0-9]{6}$'),
    business_no text not null default '',
    company_name text not null default '',
    address text not null default '',
    industry_code text not null default '',
    industry_name text not null default '',
    province_code text not null default '',
    district_code text not null default '',
    join_status_code text not null default '',
    workplace_type_code text not null default '',
    current_employee_count integer not null check (current_employee_count >= 0),
    previous_employee_count integer not null check (previous_employee_count >= 0),
    employee_growth integer not null check (employee_growth > 0),
    employee_growth_rate numeric(12, 2),
    new_employee_count integer not null default 0,
    lost_employee_count integer not null default 0,
    source_snapshot_updated_at timestamptz,
    computed_at timestamptz not null default now(),
    primary key (current_ym, snapshot_identity)
);

alter table public.oasis_nps_growth_leads enable row level security;

create index if not exists idx_oasis_nps_growth_leads_growth
    on public.oasis_nps_growth_leads
    (current_ym, employee_growth desc);

create index if not exists idx_oasis_nps_growth_leads_region
    on public.oasis_nps_growth_leads
    (current_ym, province_code, district_code);

create index if not exists idx_oasis_nps_growth_leads_industry
    on public.oasis_nps_growth_leads
    (current_ym, industry_code);

comment on table public.oasis_nps_growth_leads is
    '국민연금 연속 월 스냅샷을 비교해 고용인원이 증가한 사업장만 사전 계산한 CRM 조회 캐시';
