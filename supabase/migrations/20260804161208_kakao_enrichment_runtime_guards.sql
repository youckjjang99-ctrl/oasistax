begin;

create table if not exists public.oasis_contact_provider_job_leases (
    provider text primary key,
    lease_token uuid not null,
    acquired_at timestamptz not null default clock_timestamp(),
    heartbeat_at timestamptz not null default clock_timestamp(),
    expires_at timestamptz not null,
    constraint oasis_contact_provider_job_leases_provider_check
        check (provider in ('kakao_local')),
    constraint oasis_contact_provider_job_leases_expiry_check
        check (expires_at > acquired_at)
);

create table if not exists public.oasis_contact_provider_daily_usage (
    provider text not null,
    quota_date date not null,
    request_count bigint not null default 0,
    blocked_until timestamptz,
    last_safe_error_code text not null default '',
    updated_at timestamptz not null default clock_timestamp(),
    primary key (provider, quota_date),
    constraint oasis_contact_provider_daily_usage_provider_check
        check (provider in ('kakao_local')),
    constraint oasis_contact_provider_daily_usage_count_check
        check (request_count >= 0),
    constraint oasis_contact_provider_daily_usage_error_check
        check (
            last_safe_error_code = ''
            or last_safe_error_code in (
                'KEY_MISSING',
                'TIMEOUT',
                'NETWORK_ERROR',
                'INVALID_JSON',
                'HTTP_ERROR',
                'PROVIDER_ERROR'
            )
            or last_safe_error_code ~ '^HTTP_[0-9]{3}$'
    )
);

create table if not exists public.oasis_contact_provider_guard_state (
    provider text primary key,
    guard_generation bigint not null default 0,
    approved_generation bigint not null default 0,
    consumed_generation bigint not null default 0,
    incident_token uuid,
    guard_reason text not null default '',
    source_job text not null default '',
    observed_count integer not null default 0,
    matched_count integer not null default 0,
    tripped_at timestamptz,
    approved_at timestamptz,
    resumed_at timestamptz,
    updated_at timestamptz not null default clock_timestamp(),
    constraint oasis_contact_provider_guard_state_provider_check
        check (provider in ('kakao_local')),
    constraint oasis_contact_provider_guard_state_generation_check
        check (
            consumed_generation >= 0
            and consumed_generation <= approved_generation
            and approved_generation <= guard_generation
        ),
    constraint oasis_contact_provider_guard_state_reason_check
        check (
            guard_reason = ''
            or guard_reason in (
                'INITIAL_ZERO_MATCH_RATE',
                'ROLLING_ZERO_MATCH_RATE',
                'CONSECUTIVE_PROVIDER_ERRORS',
                'ORPHANED_HOLDS',
                'PROVIDER_GUARD'
            )
        ),
    constraint oasis_contact_provider_guard_state_source_check
        check (source_job in ('', 'employment', 'license')),
    constraint oasis_contact_provider_guard_state_counts_check
        check (
            observed_count >= 0
            and matched_count >= 0
            and matched_count <= observed_count
        )
);

insert into public.oasis_contact_provider_guard_state (provider)
values ('kakao_local')
on conflict (provider) do nothing;

alter table public.oasis_contact_provider_job_leases
    enable row level security;
alter table public.oasis_contact_provider_job_leases
    force row level security;
alter table public.oasis_contact_provider_daily_usage
    enable row level security;
alter table public.oasis_contact_provider_daily_usage
    force row level security;
alter table public.oasis_contact_provider_guard_state
    enable row level security;
alter table public.oasis_contact_provider_guard_state
    force row level security;

revoke all on table public.oasis_contact_provider_job_leases
    from public, anon, authenticated, service_role;
revoke all on table public.oasis_contact_provider_daily_usage
    from public, anon, authenticated, service_role;
revoke all on table public.oasis_contact_provider_guard_state
    from public, anon, authenticated, service_role;
grant select, insert, update, delete
    on table public.oasis_contact_provider_job_leases to service_role;
grant select, insert, update
    on table public.oasis_contact_provider_daily_usage to service_role;
grant select, insert, update
    on table public.oasis_contact_provider_guard_state to service_role;

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
    if v_provider <> 'kakao_local' or p_lease_token is null then
        raise exception using
            errcode = '22023',
            message = 'OASIS_INVALID_PROVIDER_LEASE';
    end if;

    perform pg_advisory_xact_lock(
        hashtextextended('oasis_contact_provider:kakao_local', 0)
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

    update public.oasis_contact_provider_job_leases l
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
    perform pg_advisory_xact_lock(
        hashtextextended('oasis_contact_provider:kakao_local', 0)
    );
    v_now := clock_timestamp();

    update public.oasis_contact_provider_job_leases l
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
    v_released boolean := false;
begin
    perform pg_advisory_xact_lock(
        hashtextextended('oasis_contact_provider:kakao_local', 0)
    );

    delete from public.oasis_contact_provider_job_leases l
    where l.provider = lower(btrim(coalesce(p_provider, '')))
      and l.lease_token = p_lease_token
    returning true into v_released;

    return coalesce(v_released, false);
end;
$$;

create or replace function public.oasis_get_contact_provider_daily_usage(
    p_provider text
)
returns table (
    request_count bigint,
    blocked_until timestamptz,
    last_safe_error_code text,
    quota_date date
)
language sql
stable
security invoker
set search_path = public, pg_temp
as $$
    select
        coalesce(u.request_count, 0)::bigint,
        u.blocked_until,
        coalesce(u.last_safe_error_code, '')::text,
        timezone('Asia/Seoul', current_timestamp)::date
    from (values (1)) as seed(value)
    left join public.oasis_contact_provider_daily_usage u
      on u.provider = lower(btrim(coalesce(p_provider, '')))
     and u.quota_date = timezone(
         'Asia/Seoul',
         current_timestamp
     )::date;
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

create or replace function public.oasis_get_contact_provider_guard(
    p_provider text
)
returns table (
    guard_state text,
    guard_generation bigint,
    approved_generation bigint,
    consumed_generation bigint,
    guard_reason text,
    source_job text,
    observed_count integer,
    matched_count integer,
    tripped_at timestamptz,
    approved_at timestamptz,
    resumed_at timestamptz
)
language sql
stable
security invoker
set search_path = public, pg_temp
as $$
    select
        case
            when coalesce(g.guard_generation, 0)
                > coalesce(g.approved_generation, 0)
                then 'blocked'
            when coalesce(g.approved_generation, 0)
                > coalesce(g.consumed_generation, 0)
                then 'resume_approved'
            else 'ready'
        end::text,
        coalesce(g.guard_generation, 0)::bigint,
        coalesce(g.approved_generation, 0)::bigint,
        coalesce(g.consumed_generation, 0)::bigint,
        coalesce(g.guard_reason, '')::text,
        coalesce(g.source_job, '')::text,
        coalesce(g.observed_count, 0)::integer,
        coalesce(g.matched_count, 0)::integer,
        g.tripped_at,
        g.approved_at,
        g.resumed_at
    from public.oasis_contact_provider_guard_state g
    where g.provider = lower(btrim(coalesce(p_provider, '')));
$$;

create or replace function public.oasis_trip_contact_provider_guard(
    p_provider text,
    p_lease_token uuid,
    p_incident_token uuid,
    p_guard_reason text,
    p_source_job text,
    p_observed_count integer default 0,
    p_matched_count integer default 0
)
returns boolean
language plpgsql
volatile
security invoker
set search_path = public, pg_temp
as $$
declare
    v_provider text := lower(btrim(coalesce(p_provider, '')));
    v_reason text := upper(btrim(coalesce(p_guard_reason, '')));
    v_source_job text := lower(btrim(coalesce(p_source_job, '')));
    v_observed integer := coalesce(p_observed_count, 0);
    v_matched integer := coalesce(p_matched_count, 0);
    v_now timestamptz;
    v_guard_generation bigint;
    v_approved_generation bigint;
    v_incident_token uuid;
begin
    if v_provider <> 'kakao_local'
       or p_lease_token is null
       or p_incident_token is null
       or v_reason not in (
           'INITIAL_ZERO_MATCH_RATE',
           'ROLLING_ZERO_MATCH_RATE',
           'CONSECUTIVE_PROVIDER_ERRORS',
           'ORPHANED_HOLDS',
           'PROVIDER_GUARD'
       )
       or v_source_job not in ('employment', 'license')
       or v_observed < 0
       or v_matched < 0
       or v_matched > v_observed then
        raise exception using
            errcode = '22023',
            message = 'OASIS_INVALID_PROVIDER_GUARD';
    end if;

    perform pg_advisory_xact_lock(
        hashtextextended('oasis_contact_provider:kakao_local', 0)
    );
    v_now := clock_timestamp();

    if not exists (
        select 1
        from public.oasis_contact_provider_job_leases l
        where l.provider = v_provider
          and l.lease_token = p_lease_token
          and l.expires_at > v_now
    ) then
        return false;
    end if;

    insert into public.oasis_contact_provider_guard_state (provider)
    values (v_provider)
    on conflict (provider) do nothing;

    select
        g.guard_generation,
        g.approved_generation,
        g.incident_token
    into
        v_guard_generation,
        v_approved_generation,
        v_incident_token
    from public.oasis_contact_provider_guard_state g
    where g.provider = v_provider
    for update;

    if v_guard_generation > v_approved_generation then
        return true;
    end if;
    if v_incident_token = p_incident_token then
        return false;
    end if;

    update public.oasis_contact_provider_guard_state g
    set
        guard_generation = g.guard_generation + 1,
        incident_token = p_incident_token,
        guard_reason = v_reason,
        source_job = v_source_job,
        observed_count = v_observed,
        matched_count = v_matched,
        tripped_at = v_now,
        approved_at = null,
        resumed_at = null,
        updated_at = v_now
    where g.provider = v_provider;

    return true;
end;
$$;

create or replace function public.oasis_approve_contact_provider_guard(
    p_provider text,
    p_expected_generation bigint,
    p_confirmation text
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
    v_approved boolean := false;
begin
    if v_provider <> 'kakao_local'
       or coalesce(p_expected_generation, 0) <= 0
       or coalesce(p_confirmation, '') <> 'KAKAO_RESTART_APPROVED' then
        raise exception using
            errcode = '22023',
            message = 'OASIS_INVALID_PROVIDER_GUARD_APPROVAL';
    end if;

    perform pg_advisory_xact_lock(
        hashtextextended('oasis_contact_provider:kakao_local', 0)
    );
    v_now := clock_timestamp();

    if exists (
        select 1
        from public.oasis_contact_provider_job_leases l
        where l.provider = v_provider
          and l.expires_at > v_now
    ) then
        return false;
    end if;

    update public.oasis_contact_provider_guard_state g
    set
        approved_generation = g.guard_generation,
        approved_at = v_now,
        updated_at = v_now
    where g.provider = v_provider
      and g.guard_generation = p_expected_generation
      and g.guard_generation > g.approved_generation
    returning true into v_approved;

    return coalesce(v_approved, false);
end;
$$;

create or replace function public.oasis_consume_contact_provider_resume(
    p_provider text,
    p_lease_token uuid,
    p_expected_generation bigint
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
    v_consumed boolean := false;
begin
    if v_provider <> 'kakao_local'
       or p_lease_token is null
       or coalesce(p_expected_generation, 0) <= 0 then
        raise exception using
            errcode = '22023',
            message = 'OASIS_INVALID_PROVIDER_GUARD_RESUME';
    end if;

    perform pg_advisory_xact_lock(
        hashtextextended('oasis_contact_provider:kakao_local', 0)
    );
    v_now := clock_timestamp();

    if not exists (
        select 1
        from public.oasis_contact_provider_job_leases l
        where l.provider = v_provider
          and l.lease_token = p_lease_token
          and l.expires_at > v_now
    ) then
        return false;
    end if;

    update public.oasis_contact_provider_guard_state g
    set
        consumed_generation = g.approved_generation,
        resumed_at = v_now,
        updated_at = v_now
    where g.provider = v_provider
      and g.guard_generation = p_expected_generation
      and g.approved_generation = g.guard_generation
      and g.consumed_generation < g.approved_generation
    returning true into v_consumed;

    return coalesce(v_consumed, false);
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
revoke all on function public.oasis_get_contact_provider_daily_usage(text)
    from public, anon, authenticated;
revoke all on function public.oasis_reserve_contact_provider_quota(
    text, integer, integer
) from public, anon, authenticated;
revoke all on function public.oasis_record_contact_provider_usage(
    text, integer, text, date
) from public, anon, authenticated;
revoke all on function public.oasis_get_contact_provider_guard(text)
    from public, anon, authenticated;
revoke all on function public.oasis_trip_contact_provider_guard(
    text, uuid, uuid, text, text, integer, integer
) from public, anon, authenticated;
revoke all on function public.oasis_approve_contact_provider_guard(
    text, bigint, text
) from public, anon, authenticated;
revoke all on function public.oasis_consume_contact_provider_resume(
    text, uuid, bigint
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
grant execute on function public.oasis_get_contact_provider_daily_usage(text)
    to service_role;
grant execute on function public.oasis_reserve_contact_provider_quota(
    text, integer, integer
) to service_role;
grant execute on function public.oasis_record_contact_provider_usage(
    text, integer, text, date
) to service_role;
grant execute on function public.oasis_get_contact_provider_guard(text)
    to service_role;
grant execute on function public.oasis_trip_contact_provider_guard(
    text, uuid, uuid, text, text, integer, integer
) to service_role;
grant execute on function public.oasis_approve_contact_provider_guard(
    text, bigint, text
) to service_role;
grant execute on function public.oasis_consume_contact_provider_resume(
    text, uuid, bigint
) to service_role;

commit;
