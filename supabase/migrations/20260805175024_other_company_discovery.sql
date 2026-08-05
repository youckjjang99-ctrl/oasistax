-- Add a third prospect discovery lane for companies that are neither
-- employment-growth nor recent-opening candidates.

alter table public.oasis_prospect_search_history
    drop constraint if exists
        oasis_prospect_search_history_discovery_type_check;

alter table public.oasis_prospect_search_history
    add constraint oasis_prospect_search_history_discovery_type_check
    check (discovery_type in ('growth', 'recent_opening', 'other'));

create index if not exists idx_oasis_employment_contacts_other_search
    on public.oasis_employment_contacts (
        province_code,
        district,
        current_employee_count desc,
        contact_key
    )
    where employee_growth <= 0
      and is_new_company is false
      and opening_signal_basis = ''
      and (
          has_mobile_phone
          or has_landline_phone
          or has_email
          or has_instagram
      );

create or replace function public.oasis_search_other_companies_v1(
    p_province_code text default '',
    p_province_name text default '',
    p_district text default '',
    p_min_employees integer default 1,
    p_max_employees integer default 300,
    p_industries text[] default '{}',
    p_contact_channels text[] default '{}',
    p_limit integer default 100,
    p_business_type text default 'all'
)
returns table (
    source_type text,
    source_record_key text,
    business_no text,
    company_name text,
    address text,
    province_name text,
    district_name text,
    province_code text,
    district_code text,
    industry_code text,
    industry_name text,
    industry_category text,
    current_employee_count integer,
    previous_employee_count integer,
    employee_growth integer,
    previous_period text,
    current_period text,
    growth_frequency text,
    is_new_company boolean,
    mobile_phone text,
    landline_phone text,
    email text,
    instagram text,
    instagram_url text,
    contact_status text,
    contact_checked_at timestamptz
)
language sql
stable
security invoker
set search_path = public
as $$
    select
        'other_company'::text as source_type,
        c.contact_key as source_record_key,
        c.business_no,
        c.company_name,
        c.address,
        case c.province_code
            when '11' then '서울특별시'
            when '26' then '부산광역시'
            when '27' then '대구광역시'
            when '28' then '인천광역시'
            when '29' then '광주광역시'
            when '30' then '대전광역시'
            when '31' then '울산광역시'
            when '36' then '세종특별자치시'
            when '41' then '경기도'
            when '51' then '강원특별자치도'
            when '43' then '충청북도'
            when '44' then '충청남도'
            when '52' then '전북특별자치도'
            when '46' then '전라남도'
            when '47' then '경상북도'
            when '48' then '경상남도'
            when '50' then '제주특별자치도'
            else c.province
        end as province_name,
        c.district as district_name,
        c.province_code,
        c.district_code,
        c.industry_code,
        c.industry_name,
        c.industry_category,
        c.current_employee_count,
        c.previous_employee_count,
        c.employee_growth,
        c.previous_period,
        c.current_period,
        c.growth_frequency,
        false as is_new_company,
        c.mobile_phone,
        c.landline_phone,
        c.email,
        c.instagram_id as instagram,
        c.instagram_url,
        c.status as contact_status,
        c.checked_at as contact_checked_at
    from public.oasis_employment_contacts c
    where c.employee_growth <= 0
      and c.is_new_company is false
      and c.opening_signal_basis = ''
      and (
          c.has_mobile_phone
          or c.has_landline_phone
          or c.has_email
          or c.has_instagram
      )
      and c.current_employee_count between
          greatest(1, p_min_employees)
          and greatest(greatest(1, p_min_employees), p_max_employees)
      and (
          trim(coalesce(p_province_code, '')) = ''
          or c.province_code = trim(p_province_code)
      )
      and (
          trim(coalesce(p_province_name, '')) = ''
          or case c.province_code
              when '11' then '서울특별시'
              when '26' then '부산광역시'
              when '27' then '대구광역시'
              when '28' then '인천광역시'
              when '29' then '광주광역시'
              when '30' then '대전광역시'
              when '31' then '울산광역시'
              when '36' then '세종특별자치시'
              when '41' then '경기도'
              when '51' then '강원특별자치도'
              when '43' then '충청북도'
              when '44' then '충청남도'
              when '52' then '전북특별자치도'
              when '46' then '전라남도'
              when '47' then '경상북도'
              when '48' then '경상남도'
              when '50' then '제주특별자치도'
              else c.province
          end = trim(p_province_name)
      )
      and (
          trim(coalesce(p_district, '')) = ''
          or c.district = trim(p_district)
      )
      and (
          coalesce(cardinality(p_industries), 0) = 0
          or c.industry_category = any(p_industries)
      )
      and (
          coalesce(cardinality(p_contact_channels), 0) = 0
          or (
              'mobile_phone' = any(p_contact_channels)
              and c.has_mobile_phone
          )
          or (
              'landline_phone' = any(p_contact_channels)
              and c.has_landline_phone
          )
          or (
              'email' = any(p_contact_channels)
              and c.has_email
          )
          or (
              'instagram' = any(p_contact_channels)
              and c.has_instagram
          )
      )
      and (
          lower(trim(coalesce(p_business_type, 'all')))
              not in ('stock', 'individual')
          or (
              lower(trim(coalesce(p_business_type, 'all'))) = 'stock'
              and public.oasis_is_stock_company(c.company_name)
          )
          or (
              lower(trim(coalesce(p_business_type, 'all'))) = 'individual'
              and not public.oasis_is_stock_company(c.company_name)
          )
      )
    order by
        c.current_employee_count desc,
        c.checked_at desc nulls last,
        c.contact_key
    limit least(500, greatest(1, p_limit));
$$;

revoke execute on function public.oasis_search_other_companies_v1(
    text,
    text,
    text,
    integer,
    integer,
    text[],
    text[],
    integer,
    text
) from public, anon, authenticated;

grant execute on function public.oasis_search_other_companies_v1(
    text,
    text,
    text,
    integer,
    integer,
    text[],
    text[],
    integer,
    text
) to service_role;

comment on function public.oasis_search_other_companies_v1(
    text,
    text,
    text,
    integer,
    integer,
    text[],
    text[],
    integer,
    text
) is 'Returns contactable companies with neither employment-growth nor recent-opening signals.';

notify pgrst, 'reload schema';
