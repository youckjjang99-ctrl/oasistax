-- Generic contact-cache rows keep location and employment facts in the
-- comprehensive Comwel source table. Join that source so UI filters use the
-- real province and employee counts instead of empty cache defaults.

create index if not exists idx_oasis_employment_contacts_other_location
    on public.oasis_employment_contacts (
        province,
        district,
        contact_key
    )
    where source_type = 'comwel_all_employers'
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
        w.province as province_name,
        w.district as district_name,
        case w.province
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
        end as province_code,
        ''::text as district_code,
        w.industry_code,
        w.industry_name,
        w.industry_category,
        w.workers_2025 as current_employee_count,
        w.workers_2024 as previous_employee_count,
        w.growth_2024_2025 as employee_growth,
        '2024'::text as previous_period,
        '2025'::text as current_period,
        'annual'::text as growth_frequency,
        false as is_new_company,
        c.mobile_phone,
        c.landline_phone,
        c.email,
        c.instagram_id as instagram,
        c.instagram_url,
        c.status as contact_status,
        c.checked_at as contact_checked_at
    from public.oasis_employment_contacts c
    join public.oasis_comwel_annual_growth w
      on w.business_no = c.business_no
    where c.source_type = 'comwel_all_employers'
      and w.growth_2024_2025 <= 0
      and w.is_new_2025 is false
      and (
          c.has_mobile_phone
          or c.has_landline_phone
          or c.has_email
          or c.has_instagram
      )
      and w.workers_2025 between
          greatest(1, p_min_employees)
          and greatest(greatest(1, p_min_employees), p_max_employees)
      and (
          trim(coalesce(p_province_code, '')) = ''
          or case w.province
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
          end = trim(p_province_code)
      )
      and (
          trim(coalesce(p_province_name, '')) = ''
          or w.province = trim(p_province_name)
      )
      and (
          trim(coalesce(p_district, '')) = ''
          or w.district = trim(p_district)
      )
      and (
          coalesce(cardinality(p_industries), 0) = 0
          or w.industry_category = any(p_industries)
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
        w.workers_2025 desc,
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
) is 'Returns contactable Comwel companies with neither growth nor new-company signals.';

notify pgrst, 'reload schema';
