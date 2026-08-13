begin;

-- Daum Web uses the same Kakao app key but has an independent 30,000/day
-- endpoint quota. Keep its lease and usage counters separate from Kakao Local.
alter table public.oasis_contact_provider_job_leases
    drop constraint if exists
        oasis_contact_provider_job_leases_provider_check;
alter table public.oasis_contact_provider_job_leases
    add constraint oasis_contact_provider_job_leases_provider_check
    check (provider in ('kakao_local', 'daum_web'));

alter table public.oasis_contact_provider_daily_usage
    drop constraint if exists
        oasis_contact_provider_daily_usage_provider_check;
alter table public.oasis_contact_provider_daily_usage
    add constraint oasis_contact_provider_daily_usage_provider_check
    check (provider in ('kakao_local', 'daum_web'));

create or replace function public.oasis_acquire_contact_provider_lease(
    p_provider text,
    p_lease_token uuid,
    p_ttl_seconds integer default 600
)
returns boolean
language plpgsql
volatile
security invoker
set search_path = public, pg_temp
as $$
declare
    v_provider text := lower(btrim(coalesce(p_provider, '')));
    v_now timestamptz;
    v_ttl integer := greatest(60, least(coalesce(p_ttl_seconds, 600), 3600));
    v_acquired boolean := false;
begin
    if v_provider not in ('kakao_local', 'daum_web')
       or p_lease_token is null then
        raise exception using
            errcode = '22023',
            message = 'OASIS_INVALID_PROVIDER_LEASE';
    end if;

    perform pg_advisory_xact_lock(
        hashtextextended('oasis_contact_provider:' || v_provider, 0)
    );
    v_now := clock_timestamp();

    insert into public.oasis_contact_provider_job_leases (
        provider,
        lease_token,
        acquired_at,
        heartbeat_at,
        expires_at
    ) values (
        v_provider,
        p_lease_token,
        v_now,
        v_now,
        v_now + make_interval(secs => v_ttl)
    )
    on conflict (provider) do nothing;

    update public.oasis_contact_provider_job_leases as l
    set
        lease_token = p_lease_token,
        acquired_at = case
            when l.lease_token = p_lease_token then l.acquired_at
            else v_now
        end,
        heartbeat_at = v_now,
        expires_at = v_now + make_interval(secs => v_ttl)
    where l.provider = v_provider
      and (
          l.lease_token = p_lease_token
          or l.expires_at <= v_now
      )
    returning true into v_acquired;

    return coalesce(v_acquired, false);
end;
$$;

create or replace function public.oasis_renew_contact_provider_lease(
    p_provider text,
    p_lease_token uuid,
    p_ttl_seconds integer default 600
)
returns boolean
language plpgsql
volatile
security invoker
set search_path = public, pg_temp
as $$
declare
    v_provider text := lower(btrim(coalesce(p_provider, '')));
    v_now timestamptz;
    v_ttl integer := greatest(60, least(coalesce(p_ttl_seconds, 600), 3600));
    v_renewed boolean := false;
begin
    if v_provider not in ('kakao_local', 'daum_web')
       or p_lease_token is null then
        raise exception using
            errcode = '22023',
            message = 'OASIS_INVALID_PROVIDER_LEASE';
    end if;

    perform pg_advisory_xact_lock(
        hashtextextended('oasis_contact_provider:' || v_provider, 0)
    );
    v_now := clock_timestamp();

    update public.oasis_contact_provider_job_leases as l
    set
        heartbeat_at = v_now,
        expires_at = v_now + make_interval(secs => v_ttl)
    where l.provider = v_provider
      and l.lease_token = p_lease_token
      and l.expires_at > v_now
    returning true into v_renewed;

    return coalesce(v_renewed, false);
end;
$$;

create or replace function public.oasis_release_contact_provider_lease(
    p_provider text,
    p_lease_token uuid
)
returns boolean
language plpgsql
volatile
security invoker
set search_path = public, pg_temp
as $$
declare
    v_provider text := lower(btrim(coalesce(p_provider, '')));
    v_released boolean := false;
begin
    if v_provider not in ('kakao_local', 'daum_web')
       or p_lease_token is null then
        raise exception using
            errcode = '22023',
            message = 'OASIS_INVALID_PROVIDER_LEASE';
    end if;

    perform pg_advisory_xact_lock(
        hashtextextended('oasis_contact_provider:' || v_provider, 0)
    );

    delete from public.oasis_contact_provider_job_leases as l
    where l.provider = v_provider
      and l.lease_token = p_lease_token
    returning true into v_released;

    return coalesce(v_released, false);
end;
$$;

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
    v_provider_hard_limit integer := case
        when v_provider = 'daum_web' then 30000
        else 90000
    end;
    v_now timestamptz := clock_timestamp();
    v_quota_date date := timezone('Asia/Seoul', v_now)::date;
    v_request_count bigint;
    v_blocked_until timestamptz;
    v_last_safe_error_code text;
begin
    if v_provider not in ('kakao_local', 'daum_web')
       or v_count not between 1 and 10000
       or v_safe_limit not between 1 and v_provider_hard_limit
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
      and (u.blocked_until is null or u.blocked_until <= v_now)
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
    left join public.oasis_contact_provider_daily_usage as u
      on u.provider = v_provider
     and u.quota_date = v_quota_date;
end;
$$;

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
    if v_provider not in ('kakao_local', 'daum_web')
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
    from public.oasis_contact_provider_daily_usage as u
    where u.provider = v_provider
      and u.quota_date = v_quota_date;
end;
$$;

revoke all on function public.oasis_acquire_contact_provider_lease(
    text, uuid, integer
) from public, anon, authenticated;
revoke all on function public.oasis_renew_contact_provider_lease(
    text, uuid, integer
) from public, anon, authenticated;
revoke all on function public.oasis_release_contact_provider_lease(
    text, uuid
) from public, anon, authenticated;
revoke all on function public.oasis_reserve_contact_provider_quota(
    text, integer, integer
) from public, anon, authenticated;
revoke all on function public.oasis_record_contact_provider_usage(
    text, integer, text, date
) from public, anon, authenticated;

grant execute on function public.oasis_acquire_contact_provider_lease(
    text, uuid, integer
) to service_role;
grant execute on function public.oasis_renew_contact_provider_lease(
    text, uuid, integer
) to service_role;
grant execute on function public.oasis_release_contact_provider_lease(
    text, uuid
) to service_role;
grant execute on function public.oasis_reserve_contact_provider_quota(
    text, integer, integer
) to service_role;
grant execute on function public.oasis_record_contact_provider_usage(
    text, integer, text, date
) to service_role;

commit;
