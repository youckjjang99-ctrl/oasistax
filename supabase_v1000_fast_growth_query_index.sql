-- DB발굴의 지역별 상위 고용증가 업체 조회를 30초 안에 끝내기 위한 인덱스다.
-- 통합 뷰 대신 두 원천 테이블에서 지역별 상위 후보만 각각 조회한다.
create index if not exists idx_oasis_comwel_fast_growth_province_name
on public.oasis_comwel_annual_growth (
    province,
    growth_2024_2025 desc,
    workers_2025 desc
)
where workers_2025 between 1 and 9
  and growth_2024_2025 > 0;

create index if not exists idx_oasis_nps_fast_growth_province
on public.oasis_nps_growth_leads (
    province_code,
    employee_growth desc,
    current_employee_count desc
)
where current_employee_count >= 10
  and employee_growth > 0;

analyze public.oasis_comwel_annual_growth;
analyze public.oasis_nps_growth_leads;
