drop view if exists public.oasis_fast_employment_growth_leads;

create view public.oasis_fast_employment_growth_leads
with (security_invoker = true)
as
select
    'nps_monthly'::text as source_type,
    growth.snapshot_identity as source_record_key,
    growth.business_no,
    growth.company_name,
    growth.address,
    ''::text as province_name,
    ''::text as district_name,
    growth.province_code,
    growth.district_code,
    growth.industry_code,
    growth.industry_name,
    growth.current_employee_count,
    growth.previous_employee_count,
    growth.employee_growth,
    growth.previous_ym as previous_period,
    growth.current_ym as current_period,
    'monthly'::text as growth_frequency,
    false as is_new_company
from public.oasis_nps_growth_leads growth
where growth.current_employee_count >= 10
union all
select
    'comwel_annual'::text,
    annual.business_no,
    annual.business_no,
    annual.company_name,
    annual.address,
    annual.province,
    annual.district,
    case annual.province
        when '서울특별시' then '11'
        when '부산광역시' then '26'
        when '대구광역시' then '27'
        when '인천광역시' then '28'
        when '광주광역시' then '29'
        when '대전광역시' then '30'
        when '울산광역시' then '31'
        when '세종특별자치시' then '36'
        when '경기도' then '41'
        when '강원특별자치도' then '51'
        when '충청북도' then '43'
        when '충청남도' then '44'
        when '전북특별자치도' then '52'
        when '전라남도' then '46'
        when '경상북도' then '47'
        when '경상남도' then '48'
        when '제주특별자치도' then '50'
        else ''
    end,
    ''::text,
    annual.industry_code,
    annual.industry_name,
    annual.workers_2025,
    annual.workers_2024,
    annual.growth_2024_2025,
    '2024',
    '2025',
    'annual',
    annual.is_new_2025
from public.oasis_comwel_annual_growth annual
where annual.workers_2025 between 1 and 9
  and annual.growth_2024_2025 > 0;

comment on view public.oasis_fast_employment_growth_leads is
    '빠른 DB발굴용: 국민연금 10명 이상 월별 증가 + 근로복지공단 1~9명 연간 증가';
