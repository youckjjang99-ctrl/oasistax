-- 근로복지공단 2023~2025 연간 고용 인원을 현재 1명 이상 업체별 1행으로 압축한다.
create table if not exists public.oasis_comwel_annual_growth (
    business_no text primary key check (business_no ~ '^[0-9]{10}$'),
    company_name text not null default '',
    primary_workplace_management_no text not null default '',
    zip_code text not null default '',
    address text not null default '',
    province text not null default '',
    district text not null default '',
    industry_code text not null default '',
    industry_name text not null default '',
    workers_2023 integer not null default 0 check (workers_2023 >= 0),
    workers_2024 integer not null default 0 check (workers_2024 >= 0),
    workers_2025 integer not null check (workers_2025 >= 1),
    growth_2023_2024 integer not null,
    growth_2024_2025 integer not null,
    growth_2023_2025 integer not null,
    workplace_count_2025 integer not null default 1
        check (workplace_count_2025 > 0),
    source_year_mask smallint not null
        check (source_year_mask between 1 and 7),
    is_new_2025 boolean not null default false,
    source_name text not null default '근로복지공단 고용·산재보험 가입 현황',
    source_reference_date date not null default date '2025-12-31',
    updated_at timestamptz not null default now()
);

alter table public.oasis_comwel_annual_growth enable row level security;

revoke all on table public.oasis_comwel_annual_growth from anon, authenticated;

create index if not exists idx_oasis_comwel_annual_growth_growth
    on public.oasis_comwel_annual_growth
    (growth_2024_2025 desc, workers_2025)
    where growth_2024_2025 > 0;

create index if not exists idx_oasis_comwel_annual_growth_region
    on public.oasis_comwel_annual_growth
    (province, district, growth_2024_2025 desc)
    where growth_2024_2025 > 0;

create index if not exists idx_oasis_comwel_annual_growth_industry
    on public.oasis_comwel_annual_growth
    (industry_code, growth_2024_2025 desc)
    where growth_2024_2025 > 0;

comment on table public.oasis_comwel_annual_growth is
    '근로복지공단 공개자료를 사업자번호별 1행으로 합산한 현재 1명 이상 업체의 2023~2025 연간 고용 증감 캐시';

comment on column public.oasis_comwel_annual_growth.workers_2025 is
    '같은 사업장관리번호에서는 고용·산재 상시근로자수 중 큰 값, 동일 사업자번호의 여러 사업장은 합산';

comment on column public.oasis_comwel_annual_growth.source_year_mask is
    '연도 존재 비트: 2023=1, 2024=2, 2025=4';
