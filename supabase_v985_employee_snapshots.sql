-- OASIS CRM v9.8.5
-- DB발굴 전년 동월 가입자수 비교용 월별 스냅샷
-- 기존 테이블과 데이터는 삭제하거나 변경하지 않습니다.

create table if not exists public.oasis_nps_employee_snapshots (
    snapshot_identity text not null,
    data_created_ym text not null check (data_created_ym ~ '^[0-9]{6}$'),
    employee_count integer not null check (employee_count >= 0),
    company_name text not null default '',
    address text not null default '',
    source_key text not null default '',
    captured_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (snapshot_identity, data_created_ym)
);

create index if not exists idx_oasis_nps_employee_snapshots_month
on public.oasis_nps_employee_snapshots (data_created_ym, snapshot_identity);

alter table public.oasis_nps_employee_snapshots enable row level security;

comment on table public.oasis_nps_employee_snapshots is
    'DB발굴에서 수집한 월별 국민연금 가입자수. 전년 동월 비교에 사용';
