begin;

create or replace function public.oasis_record_contact_provider_usage(
    p_provider text,
    p_request_count integer,
    p_safe_error_code text default '',
    p_quota_date date default null
)
returns table (
    request_count bigint,
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
    v_code text := upper(btrim(coalesce(p_safe_error_code, '')));
    v_current_quota_date date := timezone(
        'Asia/Seoul',
        clock_timestamp()
    )::date;
    v_quota_date date := coalesce(p_quota_date, v_current_quota_date);
    v_blocked_until timestamptz;
begin
    if v_provider <> 'kakao_local'
       or v_count not between -10000 and 10000
       or v_quota_date not in (
           v_current_quota_date,
           v_current_quota_date - 1
       )
       or (
           v_code <> ''
           and v_code not in (
               'KEY_MISSING',
               'TIMEOUT',
               'NETWORK_ERROR',
               'INVALID_JSON',
               'HTTP_ERROR',
               'PROVIDER_ERROR'
           )
           and v_code !~ '^HTTP_[0-9]{3}$'
       ) then
        raise exception using
            errcode = '22023',
            message = 'OASIS_INVALID_PROVIDER_USAGE';
    end if;

    if v_code = 'HTTP_429' then
        v_blocked_until := (v_quota_date + 1)::timestamp
            at time zone 'Asia/Seoul';
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
        greatest(v_count, 0),
        v_blocked_until,
        v_code,
        clock_timestamp()
    )
    on conflict on constraint oasis_contact_provider_daily_usage_pkey
    do update
    set
        request_count = greatest(0, u.request_count + v_count),
        blocked_until = case
            when excluded.blocked_until is null then u.blocked_until
            when u.blocked_until is null then excluded.blocked_until
            else greatest(u.blocked_until, excluded.blocked_until)
        end,
        last_safe_error_code = case
            when excluded.last_safe_error_code = ''
                then u.last_safe_error_code
            else excluded.last_safe_error_code
        end,
        updated_at = clock_timestamp();

    return query
    select
        u.request_count,
        u.blocked_until,
        u.last_safe_error_code,
        u.quota_date
    from public.oasis_contact_provider_daily_usage u
    where u.provider = v_provider
      and u.quota_date = v_quota_date;
end;
$$;

revoke all on function public.oasis_record_contact_provider_usage(
    text, integer, text, date
) from public, anon, authenticated;

grant execute on function public.oasis_record_contact_provider_usage(
    text, integer, text, date
) to service_role;

commit;
