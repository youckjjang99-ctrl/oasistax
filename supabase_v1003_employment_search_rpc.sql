-- 연락처 필터와 지역·업종 필터를 함께 사용할 때의 성장기업 조회 최적화
create index if not exists idx_oasis_employment_contacts_mobile
    on public.oasis_employment_contacts (contact_key)
    where has_mobile_phone;

create index if not exists idx_oasis_employment_contacts_landline
    on public.oasis_employment_contacts (contact_key)
    where has_landline_phone;

create index if not exists idx_oasis_employment_contacts_email
    on public.oasis_employment_contacts (contact_key)
    where has_email;

create index if not exists idx_oasis_employment_contacts_instagram
    on public.oasis_employment_contacts (contact_key)
    where has_instagram;

create or replace function public.oasis_search_employment_growth(
    p_province_code text default '',
    p_province_name text default '',
    p_district text default '',
    p_min_employees integer default 1,
    p_max_employees integer default 300,
    p_industries text[] default '{}',
    p_contact_channels text[] default '{}',
    p_limit integer default 100
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
    with nps_ranked as (
        select
            'nps_monthly'::text as source_type,
            n.snapshot_identity as source_record_key,
            n.business_no,
            n.company_name,
            n.address,
            case n.province_code
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
                else ''
            end as province_name,
            n.district_name,
            n.province_code,
            n.district_code,
            n.industry_code,
            n.industry_name,
            n.industry_category,
            n.current_employee_count,
            n.previous_employee_count,
            n.employee_growth,
            n.previous_ym as previous_period,
            n.current_ym as current_period,
            'monthly'::text as growth_frequency,
            false as is_new_company,
            c.mobile_phone,
            c.landline_phone,
            c.email,
            c.instagram_id as instagram,
            c.instagram_url,
            c.status as contact_status,
            c.checked_at as contact_checked_at
        from public.oasis_nps_growth_leads n
        left join public.oasis_employment_contacts c
          on c.contact_key = n.contact_key
        where n.current_employee_count >= greatest(10, p_min_employees)
          and n.current_employee_count <= greatest(
              greatest(10, p_min_employees),
              p_max_employees
          )
          and n.employee_growth > 0
          and (p_province_code = '' or n.province_code = p_province_code)
          and (p_district = '' or n.district_name = p_district)
          and (
              coalesce(cardinality(p_industries), 0) = 0
              or n.industry_category = any(p_industries)
          )
          and (
              coalesce(cardinality(p_contact_channels), 0) = 0
              or (
                  'mobile_phone' = any(p_contact_channels)
                  and coalesce(c.has_mobile_phone, false)
              )
              or (
                  'landline_phone' = any(p_contact_channels)
                  and coalesce(c.has_landline_phone, false)
              )
              or (
                  'email' = any(p_contact_channels)
                  and coalesce(c.has_email, false)
              )
              or (
                  'instagram' = any(p_contact_channels)
                  and coalesce(c.has_instagram, false)
              )
          )
        order by n.employee_growth desc, n.current_employee_count desc
        limit least(500, greatest(1, p_limit))
    ),
    comwel_ranked as (
        select
            'comwel_annual'::text as source_type,
            w.business_no as source_record_key,
            w.business_no,
            w.company_name,
            w.address,
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
            w.is_new_2025 as is_new_company,
            c.mobile_phone,
            c.landline_phone,
            c.email,
            c.instagram_id as instagram,
            c.instagram_url,
            c.status as contact_status,
            c.checked_at as contact_checked_at
        from public.oasis_comwel_annual_growth w
        left join public.oasis_employment_contacts c
          on c.contact_key = 'business:' || w.business_no
        where w.workers_2025 >= greatest(1, p_min_employees)
          and w.workers_2025 <= least(
              9,
              greatest(greatest(1, p_min_employees), p_max_employees)
          )
          and p_min_employees <= 9
          and w.growth_2024_2025 > 0
          and (p_province_name = '' or w.province = p_province_name)
          and (p_district = '' or w.district = p_district)
          and (
              coalesce(cardinality(p_industries), 0) = 0
              or w.industry_category = any(p_industries)
          )
          and (
              coalesce(cardinality(p_contact_channels), 0) = 0
              or (
                  'mobile_phone' = any(p_contact_channels)
                  and coalesce(c.has_mobile_phone, false)
              )
              or (
                  'landline_phone' = any(p_contact_channels)
                  and coalesce(c.has_landline_phone, false)
              )
              or (
                  'email' = any(p_contact_channels)
                  and coalesce(c.has_email, false)
              )
              or (
                  'instagram' = any(p_contact_channels)
                  and coalesce(c.has_instagram, false)
              )
          )
        order by w.growth_2024_2025 desc, w.workers_2025 desc
        limit least(500, greatest(1, p_limit))
    ),
    combined as (
        select * from nps_ranked
        union all
        select * from comwel_ranked
    )
    select *
    from combined
    order by employee_growth desc, current_employee_count desc
    limit least(500, greatest(1, p_limit));
$$;

revoke execute on function public.oasis_search_employment_growth(
    text,
    text,
    text,
    integer,
    integer,
    text[],
    text[],
    integer
) from public, anon, authenticated;

grant execute on function public.oasis_search_employment_growth(
    text,
    text,
    text,
    integer,
    integer,
    text[],
    text[],
    integer
) to service_role;
