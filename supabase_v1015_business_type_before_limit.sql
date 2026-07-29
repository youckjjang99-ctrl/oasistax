-- 사업자 유형을 조회 단위 적용 전에 필터링한다.
-- 기존 함수의 인자 순서는 유지하고 마지막에 p_business_type을 추가해
-- 이전 클라이언트도 기본값(all)으로 계속 호출할 수 있게 한다.

create or replace function public.oasis_is_stock_company(
    p_company_name text
)
returns boolean
language sql
immutable
parallel safe
security invoker
set search_path = public
as $$
    select
        position('(주)' in normalized_name) > 0
        or position('㈜' in normalized_name) > 0
        or position('주식회사' in normalized_name) > 0
        or position('유한회사' in normalized_name) > 0
        or position('유한책임회사' in normalized_name) > 0
        or position('법인' in normalized_name) > 0
        or position('co.,ltd' in normalized_name) > 0
        or position('corporation' in normalized_name) > 0
    from (
        select replace(
            lower(coalesce(p_company_name, '')),
            ' ',
            ''
        ) as normalized_name
    ) normalized;
$$;

revoke execute on function public.oasis_is_stock_company(text)
from public, anon, authenticated;

grant execute on function public.oasis_is_stock_company(text)
to service_role;

create or replace function public.oasis_search_employment_growth_v2(
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
language plpgsql
stable
security invoker
set search_path = public
as $$
declare
    query_text text;
    channel_conditions text[] := '{}';
    minimum_count integer := greatest(1, p_min_employees);
    maximum_count integer := greatest(
        greatest(1, p_min_employees),
        p_max_employees
    );
    result_limit integer := least(500, greatest(1, p_limit));
    business_kind text := lower(
        trim(coalesce(p_business_type, 'all'))
    );
begin
    query_text := $query$
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
    $query$;

    query_text := query_text || format(
        ' and c.current_employee_count between %s and %s',
        minimum_count,
        maximum_count
    );

    if trim(coalesce(p_province_code, '')) <> '' then
        query_text := query_text || format(
            ' and c.province_code = %L',
            trim(p_province_code)
        );
    elsif trim(coalesce(p_province_name, '')) <> '' then
        query_text := query_text || format(
            ' and c.province = %L',
            trim(p_province_name)
        );
    end if;

    if trim(coalesce(p_district, '')) <> '' then
        query_text := query_text || format(
            ' and c.district = %L',
            trim(p_district)
        );
    end if;

    if coalesce(cardinality(p_industries), 0) > 0 then
        query_text := query_text || format(
            ' and c.industry_category = any(%L::text[])',
            p_industries
        );
    end if;

    if 'mobile_phone' = any(p_contact_channels) then
        channel_conditions := array_append(
            channel_conditions,
            'c.has_mobile_phone'
        );
    end if;
    if 'landline_phone' = any(p_contact_channels) then
        channel_conditions := array_append(
            channel_conditions,
            'c.has_landline_phone'
        );
    end if;
    if 'email' = any(p_contact_channels) then
        channel_conditions := array_append(
            channel_conditions,
            'c.has_email'
        );
    end if;
    if 'instagram' = any(p_contact_channels) then
        channel_conditions := array_append(
            channel_conditions,
            'c.has_instagram'
        );
    end if;

    if cardinality(channel_conditions) > 0 then
        query_text := query_text
            || ' and ('
            || array_to_string(channel_conditions, ' or ')
            || ')';
    end if;

    if business_kind = 'stock' then
        query_text := query_text
            || ' and public.oasis_is_stock_company(c.company_name)';
    elsif business_kind = 'individual' then
        query_text := query_text
            || ' and not public.oasis_is_stock_company(c.company_name)';
    end if;

    query_text := query_text
        || ' order by c.employee_growth desc, '
        || 'c.current_employee_count desc'
        || format(' limit %s', result_limit);

    return query execute query_text;
end;
$$;

revoke execute on function public.oasis_search_employment_growth_v2(
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

grant execute on function public.oasis_search_employment_growth_v2(
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

create or replace function public.oasis_search_recent_openings_v2(
    p_province_code text default '',
    p_province_name text default '',
    p_district text default '',
    p_min_employees integer default 1,
    p_max_employees integer default 300,
    p_industries text[] default '{}',
    p_contact_channels text[] default '{}',
    p_recent_months integer default 6,
    p_include_comwel_annual boolean default true,
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
    opening_signal_date date,
    opening_signal_year integer,
    opening_signal_basis text,
    opening_signal_precision text,
    source_period text,
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
        case c.opening_signal_basis
            when 'nps_applied_on' then 'nps_monthly'
            else 'comwel_annual'
        end as source_type,
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
        c.opening_employee_count as current_employee_count,
        c.opening_signal_date,
        c.opening_signal_year,
        c.opening_signal_basis,
        c.opening_signal_precision,
        case c.opening_signal_basis
            when 'nps_applied_on'
                then to_char(c.opening_signal_date, 'YYYYMM')
            else coalesce(c.opening_signal_year::text, '2025')
        end as source_period,
        c.mobile_phone,
        c.landline_phone,
        c.email,
        c.instagram_id as instagram,
        c.instagram_url,
        c.status as contact_status,
        c.checked_at as contact_checked_at
    from public.oasis_employment_contacts c
    where trim(coalesce(c.source_record_key, '')) <> ''
      and c.opening_employee_count between
          greatest(1, p_min_employees)
          and greatest(greatest(1, p_min_employees), p_max_employees)
      and (
          (
              c.opening_signal_basis = 'nps_applied_on'
              and c.opening_signal_date >= current_date - make_interval(
                  months => case
                      when p_recent_months in (3, 6, 12)
                          then p_recent_months
                      else 6
                  end
              )
          )
          or (
              p_include_comwel_annual
              and c.opening_signal_basis = 'comwel_first_seen_2025'
          )
      )
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
        c.opening_signal_date desc nulls last,
        c.opening_signal_year desc,
        c.opening_employee_count desc,
        c.contact_key
    limit least(500, greatest(1, p_limit));
$$;

revoke execute on function public.oasis_search_recent_openings_v2(
    text,
    text,
    text,
    integer,
    integer,
    text[],
    text[],
    integer,
    boolean,
    integer,
    text
) from public, anon, authenticated;

grant execute on function public.oasis_search_recent_openings_v2(
    text,
    text,
    text,
    integer,
    integer,
    text[],
    text[],
    integer,
    boolean,
    integer,
    text
) to service_role;

comment on function public.oasis_search_employment_growth_v2(
    text,
    text,
    text,
    integer,
    integer,
    text[],
    text[],
    integer,
    text
) is 'Filters business type before applying the requested result limit.';

comment on function public.oasis_search_recent_openings_v2(
    text,
    text,
    text,
    integer,
    integer,
    text[],
    text[],
    integer,
    boolean,
    integer,
    text
) is 'Filters business type before applying the requested result limit.';

notify pgrst, 'reload schema';
