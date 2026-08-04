-- Keep business-type filtering before LIMIT while preventing the planner from
-- choosing a large BitmapOr + sort plan for phone-filtered growth searches.
--
-- For the broad stock/individual + mobile-or-landline path that timed out,
-- region predicates and the growth ordering are applied inside an OFFSET 0
-- optimization fence. A single industry is pushed into that inner scan as a
-- scalar equality so the existing province+industry ordering index remains
-- usable. Multi-industry and single-channel searches retain the existing plan.

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
    use_ordered_scan boolean := (
        business_kind in ('stock', 'individual')
        and coalesce(cardinality(p_industries), 0) <= 1
        and 'mobile_phone' = any(
            coalesce(p_contact_channels, '{}'::text[])
        )
        and 'landline_phone' = any(
            coalesce(p_contact_channels, '{}'::text[])
        )
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
    $query$;

    if use_ordered_scan then
        query_text := query_text || $query$
            from (
                select base.*
                from public.oasis_employment_contacts base
                where base.employee_growth > 0
        $query$;

        if trim(coalesce(p_province_code, '')) <> '' then
            query_text := query_text || format(
                ' and base.province_code = %L',
                trim(p_province_code)
            );
        elsif trim(coalesce(p_province_name, '')) <> '' then
            query_text := query_text || format(
                ' and base.province = %L',
                trim(p_province_name)
            );
        end if;

        if trim(coalesce(p_district, '')) <> '' then
            query_text := query_text || format(
                ' and base.district = %L',
                trim(p_district)
            );
        end if;

        if coalesce(cardinality(p_industries), 0) = 1 then
            query_text := query_text || format(
                ' and base.industry_category = %L',
                p_industries[1]
            );
        end if;

        query_text := query_text || $query$
                order by
                    base.employee_growth desc,
                    base.current_employee_count desc
                offset 0
            ) c
            where c.current_employee_count between
        $query$ || format(' %s and %s', minimum_count, maximum_count);
    else
        query_text := query_text || $query$
            from public.oasis_employment_contacts c
            where c.employee_growth > 0
        $query$ || format(
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
) is 'Filters business type before LIMIT; broad phone OR searches use an ordered growth-index scan.';

notify pgrst, 'reload schema';
