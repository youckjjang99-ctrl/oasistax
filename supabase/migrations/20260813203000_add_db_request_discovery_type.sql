begin;

alter table public.oasis_mobile_db_requests
    add column if not exists discovery_type text not null default 'all';

alter table public.oasis_mobile_db_requests
    drop constraint if exists oasis_mobile_db_requests_discovery_type_check;

alter table public.oasis_mobile_db_requests
    add constraint oasis_mobile_db_requests_discovery_type_check
    check (discovery_type in ('all', 'growth', 'recent_opening', 'other'));

drop function if exists public.oasis_submit_mobile_db_request(
    text, text, text, text, integer, integer, text
);

create function public.oasis_submit_mobile_db_request(
    p_current_user_id text,
    p_region text,
    p_district text default '',
    p_business_type text default 'all',
    p_discovery_type text default 'all',
    p_minimum_employees integer default 1,
    p_maximum_employees integer default 300,
    p_session_id text default null
)
returns table (
    success boolean,
    code text,
    message text,
    request_id uuid,
    status text,
    requested_count integer,
    allocated_count integer,
    discovery_type text,
    minimum_employees integer,
    maximum_employees integer
)
language plpgsql
volatile
set search_path = public, extensions, pg_temp
as $$
declare
    v_user_id text := lower(btrim(coalesce(p_current_user_id, '')));
    v_region text := btrim(coalesce(p_region, ''));
    v_district text := btrim(coalesce(p_district, ''));
    v_business_type text := lower(btrim(coalesce(p_business_type, 'all')));
    v_discovery_type text := lower(btrim(coalesce(p_discovery_type, 'all')));
    v_minimum_employees integer := coalesce(p_minimum_employees, 1);
    v_maximum_employees integer := coalesce(p_maximum_employees, 300);
    v_existing public.oasis_mobile_db_requests%rowtype;
    v_saved public.oasis_mobile_db_requests%rowtype;
begin
    if not public.oasis_sales_actor_is_active(v_user_id) then
        raise exception using
            errcode = '42501',
            message = 'OASIS_SALES_USER_NOT_AUTHORIZED';
    end if;
    if length(v_region) not between 1 and 100
       or length(v_district) > 100
       or v_business_type not in ('all', 'stock', 'individual')
       or v_discovery_type not in (
            'all', 'growth', 'recent_opening', 'other'
       )
       or v_minimum_employees not between 1 and 10000
       or v_maximum_employees not between v_minimum_employees and 10000 then
        return query select
            false, 'invalid_input',
            '지역·사업자 유형·업체 분류·고용인원을 확인해 주세요.',
            null::uuid, null::text, 30, 0,
            v_discovery_type,
            v_minimum_employees, v_maximum_employees;
        return;
    end if;

    perform pg_advisory_xact_lock(
        hashtextextended('oasis-mobile-request:' || v_user_id, 0)
    );
    select r.* into v_existing
    from public.oasis_mobile_db_requests r
    where r.requested_user_id = v_user_id
      and r.status in ('pending', 'partially_approved')
    order by r.requested_at desc, r.id desc
    limit 1
    for update;

    if v_existing.id is not null then
        return query select
            true, 'already_requested',
            '이미 처리 중인 핸드폰 DB 신청이 있습니다.',
            v_existing.id, v_existing.status,
            v_existing.requested_count, v_existing.allocated_count,
            v_existing.discovery_type,
            v_existing.minimum_employees, v_existing.maximum_employees;
        return;
    end if;

    insert into public.oasis_mobile_db_requests (
        requested_user_id,
        region,
        district,
        business_type,
        discovery_type,
        minimum_employees,
        maximum_employees
    ) values (
        v_user_id,
        v_region,
        v_district,
        v_business_type,
        v_discovery_type,
        v_minimum_employees,
        v_maximum_employees
    ) returning * into v_saved;

    insert into public.oasis_mobile_db_request_events (
        request_id, actor_user_id, action, reason
    ) values (
        v_saved.id, v_user_id, 'requested',
        left(coalesce(p_session_id, ''), 200)
    );

    return query select
        true, 'requested', '핸드폰 DB 배정 신청을 접수했습니다.',
        v_saved.id, v_saved.status,
        v_saved.requested_count, v_saved.allocated_count,
        v_saved.discovery_type,
        v_saved.minimum_employees, v_saved.maximum_employees;
end;
$$;

drop function if exists public.oasis_list_user_mobile_db_requests(text, integer);
create function public.oasis_list_user_mobile_db_requests(
    p_current_user_id text,
    p_limit integer default 20
)
returns table (
    request_id uuid,
    requested_user_id text,
    region text,
    district text,
    business_type text,
    discovery_type text,
    minimum_employees integer,
    maximum_employees integer,
    requested_count integer,
    allocated_count integer,
    status text,
    decision_reason text,
    requested_at timestamptz,
    decided_at timestamptz
)
language plpgsql
stable
set search_path = public, pg_temp
as $$
begin
    if not public.oasis_sales_actor_is_active(p_current_user_id) then
        raise exception using
            errcode = '42501',
            message = 'OASIS_SALES_USER_NOT_AUTHORIZED';
    end if;
    return query
    select
        r.id, r.requested_user_id, r.region, r.district,
        r.business_type, r.discovery_type,
        r.minimum_employees, r.maximum_employees,
        r.requested_count, r.allocated_count,
        r.status, r.decision_reason, r.requested_at, r.decided_at
    from public.oasis_mobile_db_requests r
    where r.requested_user_id = lower(btrim(p_current_user_id))
    order by r.requested_at desc, r.id desc
    limit greatest(1, least(coalesce(p_limit, 20), 100));
end;
$$;

drop function if exists public.oasis_list_admin_mobile_db_requests(
    text, text[], integer
);
create function public.oasis_list_admin_mobile_db_requests(
    p_current_user_id text,
    p_statuses text[] default null,
    p_limit integer default 200
)
returns table (
    request_id uuid,
    requested_user_id text,
    requested_user_name text,
    region text,
    district text,
    business_type text,
    discovery_type text,
    minimum_employees integer,
    maximum_employees integer,
    requested_count integer,
    allocated_count integer,
    status text,
    decision_reason text,
    requested_at timestamptz,
    decided_at timestamptz
)
language plpgsql
stable
set search_path = public, pg_temp
as $$
begin
    if not public.oasis_sales_actor_is_admin(p_current_user_id) then
        raise exception using
            errcode = '42501',
            message = 'OASIS_SALES_ADMIN_REQUIRED';
    end if;
    return query
    select
        r.id, r.requested_user_id, coalesce(u.name, r.requested_user_id),
        r.region, r.district, r.business_type, r.discovery_type,
        r.minimum_employees, r.maximum_employees,
        r.requested_count, r.allocated_count, r.status,
        r.decision_reason, r.requested_at, r.decided_at
    from public.oasis_mobile_db_requests r
    left join public.oasis_users u
      on lower(u.user_id) = lower(r.requested_user_id)
    where coalesce(cardinality(p_statuses), 0) = 0
       or r.status = any(p_statuses)
    order by
        case when r.status in ('pending', 'partially_approved') then 0 else 1 end,
        r.requested_at asc,
        r.id
    limit greatest(1, least(coalesce(p_limit, 200), 1000));
end;
$$;

revoke execute on function public.oasis_submit_mobile_db_request(
    text, text, text, text, text, integer, integer, text
) from public, anon, authenticated;
revoke execute on function public.oasis_list_user_mobile_db_requests(
    text, integer
) from public, anon, authenticated;
revoke execute on function public.oasis_list_admin_mobile_db_requests(
    text, text[], integer
) from public, anon, authenticated;

grant execute on function public.oasis_submit_mobile_db_request(
    text, text, text, text, text, integer, integer, text
) to service_role;
grant execute on function public.oasis_list_user_mobile_db_requests(
    text, integer
) to service_role;
grant execute on function public.oasis_list_admin_mobile_db_requests(
    text, text[], integer
) to service_role;

create or replace function public.oasis_search_other_companies_v2(
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
set search_path = public, extensions, pg_temp
as $$
    with candidates as (
        select *
        from public.oasis_search_other_companies_v1(
            p_province_code => p_province_code,
            p_province_name => p_province_name,
            p_district => p_district,
            p_min_employees => p_min_employees,
            p_max_employees => p_max_employees,
            p_industries => p_industries,
            p_contact_channels => p_contact_channels,
            p_limit => 500,
            p_business_type => p_business_type
        )
    )
    select o.*
    from candidates o
    join public.oasis_employment_contacts c
      on c.contact_key = o.source_record_key
    where c.is_new_company is false
      and btrim(coalesce(c.opening_signal_basis, '')) = ''
      and not exists (
          select 1
          from public.oasis_recent_license_signals s
          where s.is_active
            and s.license_date >= current_date - interval '6 months'
            and s.contact_ref_key = encode(
                extensions.digest(
                    convert_to(c.source_record_key, 'UTF8'),
                    'sha256'
                ),
                'hex'
            )
      )
    order by
        o.current_employee_count desc,
        o.contact_checked_at desc nulls last,
        o.source_record_key
    limit least(500, greatest(1, p_limit));
$$;

revoke execute on function public.oasis_search_other_companies_v2(
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

grant execute on function public.oasis_search_other_companies_v2(
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

comment on function public.oasis_search_other_companies_v2(
    text,
    text,
    text,
    integer,
    integer,
    text[],
    text[],
    integer,
    text
) is 'Returns contactable companies outside growth, estimated-new, and recent licence-confirmed pools.';

notify pgrst, 'reload schema';

commit;
