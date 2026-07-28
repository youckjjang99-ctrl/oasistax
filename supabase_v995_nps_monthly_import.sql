-- 국민연금 월별 사업장 원문 파일의 검색·증감 분석용 컬럼
alter table public.oasis_nps_employee_snapshots
    add column if not exists join_status_code text not null default '',
    add column if not exists zip_code text not null default '',
    add column if not exists lot_address text not null default '',
    add column if not exists road_address text not null default '',
    add column if not exists legal_dong_code text not null default '',
    add column if not exists admin_dong_code text not null default '',
    add column if not exists province_code text not null default '',
    add column if not exists district_code text not null default '',
    add column if not exists neighborhood_code text not null default '',
    add column if not exists workplace_type_code text not null default '',
    add column if not exists industry_code text not null default '',
    add column if not exists industry_name text not null default '',
    add column if not exists applied_on date,
    add column if not exists reregistered_on date,
    add column if not exists withdrawn_on date,
    add column if not exists monthly_billed_amount bigint not null default 0,
    add column if not exists source_file_name text not null default '';

create index if not exists idx_oasis_nps_snapshots_month_region
    on public.oasis_nps_employee_snapshots
    (data_created_ym, province_code, district_code);

create index if not exists idx_oasis_nps_snapshots_month_industry
    on public.oasis_nps_employee_snapshots
    (data_created_ym, industry_code);

create index if not exists idx_oasis_nps_snapshots_month_employee
    on public.oasis_nps_employee_snapshots
    (data_created_ym, employee_count desc);

comment on column public.oasis_nps_employee_snapshots.source_file_name is
    '공공데이터포털 국민연금 월별 원문 파일명';
