-- 연락처 대기열에 성장지표를 함께 저장해 220만 행 전체 조인을 피한다.
alter table public.oasis_employment_contacts
    add column if not exists province_code text not null default '',
    add column if not exists district_code text not null default '',
    add column if not exists industry_code text not null default '',
    add column if not exists industry_category text not null default '기타',
    add column if not exists current_employee_count integer not null default 0,
    add column if not exists previous_employee_count integer not null default 0,
    add column if not exists employee_growth integer not null default 0,
    add column if not exists previous_period text not null default '',
    add column if not exists current_period text not null default '',
    add column if not exists growth_frequency text not null default '',
    add column if not exists is_new_company boolean not null default false;

with latest_nps as (
    select distinct on (contact_key)
        contact_key,
        province_code,
        district_code,
        industry_code,
        industry_category,
        current_employee_count,
        previous_employee_count,
        employee_growth,
        previous_ym,
        current_ym
    from public.oasis_nps_growth_leads
    where contact_key <> ''
    order by contact_key, current_ym desc
)
update public.oasis_employment_contacts c
set
    province_code = n.province_code,
    district_code = n.district_code,
    industry_code = n.industry_code,
    industry_category = n.industry_category,
    current_employee_count = n.current_employee_count,
    previous_employee_count = n.previous_employee_count,
    employee_growth = n.employee_growth,
    previous_period = n.previous_ym,
    current_period = n.current_ym,
    growth_frequency = 'monthly',
    is_new_company = false
from latest_nps n
where c.contact_key = n.contact_key;

update public.oasis_employment_contacts c
set
    province_code = case w.province
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
    district_code = '',
    industry_code = w.industry_code,
    industry_category = w.industry_category,
    current_employee_count = w.workers_2025,
    previous_employee_count = w.workers_2024,
    employee_growth = w.growth_2024_2025,
    previous_period = '2024',
    current_period = '2025',
    growth_frequency = 'annual',
    is_new_company = w.is_new_2025
from public.oasis_comwel_annual_growth w
where c.contact_key = 'business:' || w.business_no;

create index if not exists idx_oasis_employment_contacts_growth_region
    on public.oasis_employment_contacts (
        province_code,
        district,
        employee_growth desc,
        current_employee_count desc
    )
    where employee_growth > 0;

create index if not exists idx_oasis_employment_contacts_growth_industry
    on public.oasis_employment_contacts (
        province_code,
        district,
        industry_category,
        employee_growth desc,
        current_employee_count desc
    )
    where employee_growth > 0;

create index if not exists idx_oasis_employment_contacts_mobile_growth
    on public.oasis_employment_contacts (
        province_code,
        district,
        employee_growth desc,
        current_employee_count desc
    )
    where employee_growth > 0 and has_mobile_phone;

create index if not exists idx_oasis_employment_contacts_landline_growth
    on public.oasis_employment_contacts (
        province_code,
        district,
        employee_growth desc,
        current_employee_count desc
    )
    where employee_growth > 0 and has_landline_phone;

create index if not exists idx_oasis_employment_contacts_email_growth
    on public.oasis_employment_contacts (
        province_code,
        district,
        employee_growth desc,
        current_employee_count desc
    )
    where employee_growth > 0 and has_email;

create index if not exists idx_oasis_employment_contacts_instagram_growth
    on public.oasis_employment_contacts (
        province_code,
        district,
        employee_growth desc,
        current_employee_count desc
    )
    where employee_growth > 0 and has_instagram;

create or replace function public.oasis_enqueue_nps_employment_contact()
returns trigger
language plpgsql
set search_path = public
as $$
begin
    if new.employee_growth > 0 and new.contact_key <> '' then
        insert into public.oasis_employment_contacts (
            contact_key,
            source_type,
            source_record_key,
            business_no,
            company_name,
            address,
            province,
            district,
            industry_name,
            province_code,
            district_code,
            industry_code,
            industry_category,
            current_employee_count,
            previous_employee_count,
            employee_growth,
            previous_period,
            current_period,
            growth_frequency,
            is_new_company
        )
        values (
            new.contact_key,
            'nps_monthly',
            new.snapshot_identity,
            new.business_no,
            new.company_name,
            new.address,
            new.province_code,
            new.district_name,
            new.industry_name,
            new.province_code,
            new.district_code,
            new.industry_code,
            new.industry_category,
            new.current_employee_count,
            new.previous_employee_count,
            new.employee_growth,
            new.previous_ym,
            new.current_ym,
            'monthly',
            false
        )
        on conflict (contact_key) do update
        set
            source_type = excluded.source_type,
            source_record_key = excluded.source_record_key,
            business_no = excluded.business_no,
            company_name = excluded.company_name,
            address = excluded.address,
            province = excluded.province,
            district = excluded.district,
            industry_name = excluded.industry_name,
            province_code = excluded.province_code,
            district_code = excluded.district_code,
            industry_code = excluded.industry_code,
            industry_category = excluded.industry_category,
            current_employee_count = excluded.current_employee_count,
            previous_employee_count = excluded.previous_employee_count,
            employee_growth = excluded.employee_growth,
            previous_period = excluded.previous_period,
            current_period = excluded.current_period,
            growth_frequency = excluded.growth_frequency,
            is_new_company = excluded.is_new_company;
    end if;
    return new;
end;
$$;

create or replace function public.oasis_enqueue_comwel_employment_contact()
returns trigger
language plpgsql
set search_path = public
as $$
begin
    if new.growth_2024_2025 > 0
       and new.business_no ~ '^[0-9]{10}$' then
        insert into public.oasis_employment_contacts (
            contact_key,
            source_type,
            source_record_key,
            business_no,
            company_name,
            address,
            province,
            district,
            industry_name,
            province_code,
            district_code,
            industry_code,
            industry_category,
            current_employee_count,
            previous_employee_count,
            employee_growth,
            previous_period,
            current_period,
            growth_frequency,
            is_new_company
        )
        values (
            'business:' || new.business_no,
            'comwel_annual',
            new.business_no,
            new.business_no,
            new.company_name,
            new.address,
            new.province,
            new.district,
            new.industry_name,
            case new.province
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
            '',
            new.industry_code,
            new.industry_category,
            new.workers_2025,
            new.workers_2024,
            new.growth_2024_2025,
            '2024',
            '2025',
            'annual',
            new.is_new_2025
        )
        on conflict (contact_key) do update
        set
            source_type = excluded.source_type,
            source_record_key = excluded.source_record_key,
            business_no = excluded.business_no,
            company_name = excluded.company_name,
            address = excluded.address,
            province = excluded.province,
            district = excluded.district,
            industry_name = excluded.industry_name,
            province_code = excluded.province_code,
            district_code = excluded.district_code,
            industry_code = excluded.industry_code,
            industry_category = excluded.industry_category,
            current_employee_count = excluded.current_employee_count,
            previous_employee_count = excluded.previous_employee_count,
            employee_growth = excluded.employee_growth,
            previous_period = excluded.previous_period,
            current_period = excluded.current_period,
            growth_frequency = excluded.growth_frequency,
            is_new_company = excluded.is_new_company;
    end if;
    return new;
end;
$$;

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
    select
        c.source_type,
        c.source_record_key,
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
        c.is_new_company,
        c.mobile_phone,
        c.landline_phone,
        c.email,
        c.instagram_id as instagram,
        c.instagram_url,
        c.status as contact_status,
        c.checked_at as contact_checked_at
    from public.oasis_employment_contacts c
    where c.employee_growth > 0
      and c.current_employee_count >= greatest(1, p_min_employees)
      and c.current_employee_count <= greatest(
          greatest(1, p_min_employees),
          p_max_employees
      )
      and (
          p_province_code = ''
          or c.province_code = p_province_code
      )
      and (
          p_province_name = ''
          or c.province = p_province_name
          or c.province_code = p_province_code
      )
      and (p_district = '' or c.district = p_district)
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
    order by
        c.employee_growth desc,
        c.current_employee_count desc
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
