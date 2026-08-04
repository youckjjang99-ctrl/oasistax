begin;

create or replace function public.oasis_reserve_contact_provider_quota(
    p_provider text,
    p_request_count integer,
    p_safe_limit integer
)
returns table (
    request_count bigint,
    reserved boolean,
    blocked_until timestamptz,
    last_safe_error_code text,
    quota_date date
)
language plpgsql
volatile
security invoker
set search_path = public, pg_temp
as $$
declare
    v_provider text := lower(btrim(coalesce(p_provider, '')));
    v_count integer := coalesce(p_request_count, 0);
    v_safe_limit integer := coalesce(p_safe_limit, 0);
    v_now timestamptz := clock_timestamp();
    v_quota_date date := timezone(
        'Asia/Seoul',
        v_now
    )::date;
    v_request_count bigint;
    v_blocked_until timestamptz;
    v_last_safe_error_code text;
begin
    if v_provider <> 'kakao_local'
       or v_count not between 1 and 10000
       or v_safe_limit not between 1 and 90000
       or v_count > v_safe_limit then
        raise exception using
            errcode = '22023',
            message = 'OASIS_INVALID_PROVIDER_RESERVATION';
    end if;

    insert into public.oasis_contact_provider_daily_usage as u (
        provider,
        quota_date,
        request_count,
        blocked_until,
        last_safe_error_code,
        updated_at
    ) values (
        v_provider,
        v_quota_date,
        v_count,
        null,
        '',
        v_now
    )
    on conflict on constraint oasis_contact_provider_daily_usage_pkey
    do update
    set
        request_count = u.request_count + excluded.request_count,
        updated_at = v_now
    where u.request_count + excluded.request_count <= v_safe_limit
      and (
          u.blocked_until is null
          or u.blocked_until <= v_now
      )
    returning
        u.request_count,
        u.blocked_until,
        u.last_safe_error_code
    into
        v_request_count,
        v_blocked_until,
        v_last_safe_error_code;

    if found then
        return query
        select
            v_request_count,
            true,
            v_blocked_until,
            v_last_safe_error_code,
            v_quota_date;
        return;
    end if;

    return query
    select
        coalesce(u.request_count, 0)::bigint,
        false,
        u.blocked_until,
        coalesce(u.last_safe_error_code, '')::text,
        v_quota_date
    from (values (1)) as seed(value)
    left join public.oasis_contact_provider_daily_usage u
      on u.provider = v_provider
     and u.quota_date = v_quota_date;
end;
$$;

revoke all on function public.oasis_reserve_contact_provider_quota(
    text, integer, integer
) from public, anon, authenticated;

grant execute on function public.oasis_reserve_contact_provider_quota(
    text, integer, integer
) to service_role;

commit;
