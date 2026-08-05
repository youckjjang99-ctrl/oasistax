begin;

create schema if not exists oasis_private;
revoke all on schema oasis_private from public, anon, authenticated;

create table if not exists oasis_private.oasis_growth_kakao_recovery_runs (
    recovery_key text primary key,
    run_id uuid not null unique,
    expected_count integer not null check (expected_count > 0),
    selected_count integer not null default 0
        check (selected_count >= 0),
    snapshot_count integer not null default 0
        check (snapshot_count >= 0),
    recovered_count integer not null default 0
        check (recovered_count >= 0),
    status text not null default 'prepared'
        check (
            status in (
                'prepared',
                'snapshotting',
                'recovering',
                'completed'
            )
        ),
    invariant_counts_before jsonb not null default '{}'::jsonb
        check (jsonb_typeof(invariant_counts_before) = 'object'),
    invariant_counts_after jsonb not null default '{}'::jsonb
        check (jsonb_typeof(invariant_counts_after) = 'object'),
    created_at timestamptz not null default clock_timestamp(),
    completed_at timestamptz
);

create table if not exists oasis_private.oasis_growth_kakao_recovery_windows (
    recovery_key text not null,
    window_label text not null,
    started_at timestamptz not null,
    ended_at timestamptz not null,
    expected_count integer not null check (expected_count > 0),
    primary key (recovery_key, window_label),
    constraint oasis_growth_kakao_recovery_windows_label_check
        check (window_label in ('w1', 'w2', 'w3', 'w4')),
    constraint oasis_growth_kakao_recovery_windows_range_check
        check (ended_at >= started_at)
);

create table if not exists oasis_private.oasis_growth_kakao_recovery_items (
    run_id uuid not null
        references oasis_private.oasis_growth_kakao_recovery_runs(run_id)
        on delete restrict,
    contact_key text not null,
    window_label text not null
        check (window_label in ('w1', 'w2', 'w3', 'w4')),
    source_type text not null
        check (source_type in ('comwel_annual', 'nps_monthly')),
    prior_state jsonb,
    selected_at timestamptz not null default clock_timestamp(),
    backed_up_at timestamptz,
    recovered_at timestamptz,
    primary key (run_id, contact_key),
    constraint oasis_growth_kakao_recovery_items_prior_state_check
        check (
            prior_state is null
            or jsonb_typeof(prior_state) = 'object'
        ),
    constraint oasis_growth_kakao_recovery_items_recovered_check
        check (recovered_at is null or prior_state is not null)
);

create table if not exists oasis_private.oasis_growth_kakao_recovery_control (
    recovery_key text primary key
        references oasis_private.oasis_growth_kakao_recovery_runs(recovery_key)
        on delete restrict,
    lease_token uuid,
    created_at timestamptz not null default clock_timestamp(),
    released_at timestamptz,
    constraint oasis_growth_kakao_recovery_control_release_check
        check (
            (released_at is null and lease_token is not null)
            or (released_at is not null and lease_token is null)
        )
);

create index if not exists oasis_growth_kakao_snapshot_pending_idx
    on oasis_private.oasis_growth_kakao_recovery_items (
        run_id,
        contact_key
    )
    where prior_state is null;

create index if not exists oasis_growth_kakao_recovery_pending_idx
    on oasis_private.oasis_growth_kakao_recovery_items (
        run_id,
        contact_key
    )
    where prior_state is not null and recovered_at is null;

alter table oasis_private.oasis_growth_kakao_recovery_runs
    enable row level security;
alter table oasis_private.oasis_growth_kakao_recovery_runs
    force row level security;
alter table oasis_private.oasis_growth_kakao_recovery_windows
    enable row level security;
alter table oasis_private.oasis_growth_kakao_recovery_windows
    force row level security;
alter table oasis_private.oasis_growth_kakao_recovery_items
    enable row level security;
alter table oasis_private.oasis_growth_kakao_recovery_items
    force row level security;
alter table oasis_private.oasis_growth_kakao_recovery_control
    enable row level security;
alter table oasis_private.oasis_growth_kakao_recovery_control
    force row level security;

revoke all on table
    oasis_private.oasis_growth_kakao_recovery_runs,
    oasis_private.oasis_growth_kakao_recovery_windows,
    oasis_private.oasis_growth_kakao_recovery_items,
    oasis_private.oasis_growth_kakao_recovery_control
from public, anon, authenticated, service_role;

comment on table oasis_private.oasis_growth_kakao_recovery_runs is
    'Internal audit summary for the guarded growth-company Kakao recovery.';
comment on table oasis_private.oasis_growth_kakao_recovery_windows is
    'Exact inclusive UTC windows for high-confidence provider-error cohorts.';
comment on table oasis_private.oasis_growth_kakao_recovery_items is
    'Private full-row snapshots and batch progress for exact rollback.';
comment on table oasis_private.oasis_growth_kakao_recovery_control is
    'Postgres-only provider lease control; never exposed through the app.';

do $$
declare
    v_recovery_key constant text :=
        '20260805_growth_kakao_provider_error_recovery';
    v_definition_count integer;
begin
    insert into oasis_private.oasis_growth_kakao_recovery_windows (
        recovery_key,
        window_label,
        started_at,
        ended_at,
        expected_count
    ) values
        (
            v_recovery_key,
            'w1',
            timestamptz '2026-07-29 01:30:18.222368+00',
            timestamptz '2026-07-29 03:34:21.766558+00',
            77254
        ),
        (
            v_recovery_key,
            'w2',
            timestamptz '2026-07-31 18:40:57.54245+00',
            timestamptz '2026-08-01 22:01:19.552288+00',
            33989
        ),
        (
            v_recovery_key,
            'w3',
            timestamptz '2026-08-01 23:52:49.510115+00',
            timestamptz '2026-08-02 01:54:36.971516+00',
            98764
        ),
        (
            v_recovery_key,
            'w4',
            timestamptz '2026-08-03 01:20:27.086323+00',
            timestamptz '2026-08-03 02:18:48.295961+00',
            43789
        )
    on conflict (recovery_key, window_label) do nothing;

    select count(*)
    into v_definition_count
    from oasis_private.oasis_growth_kakao_recovery_windows w
    where w.recovery_key = v_recovery_key
      and (
          (w.window_label = 'w1'
           and w.started_at =
               timestamptz '2026-07-29 01:30:18.222368+00'
           and w.ended_at =
               timestamptz '2026-07-29 03:34:21.766558+00'
           and w.expected_count = 77254)
          or
          (w.window_label = 'w2'
           and w.started_at =
               timestamptz '2026-07-31 18:40:57.54245+00'
           and w.ended_at =
               timestamptz '2026-08-01 22:01:19.552288+00'
           and w.expected_count = 33989)
          or
          (w.window_label = 'w3'
           and w.started_at =
               timestamptz '2026-08-01 23:52:49.510115+00'
           and w.ended_at =
               timestamptz '2026-08-02 01:54:36.971516+00'
           and w.expected_count = 98764)
          or
          (w.window_label = 'w4'
           and w.started_at =
               timestamptz '2026-08-03 01:20:27.086323+00'
           and w.ended_at =
               timestamptz '2026-08-03 02:18:48.295961+00'
           and w.expected_count = 43789)
      );

    if v_definition_count <> 4 then
        raise exception
            'Growth Kakao recovery window definition mismatch';
    end if;
end
$$;

create or replace function oasis_private.oasis_growth_kakao_recovery_status()
returns jsonb
language plpgsql
stable
security invoker
set search_path = pg_catalog, public, oasis_private, pg_temp
as $$
declare
    v_recovery_key constant text :=
        '20260805_growth_kakao_provider_error_recovery';
    v_result jsonb;
begin
    select jsonb_build_object(
        'status', r.status,
        'expected_count', r.expected_count,
        'selected_count', r.selected_count,
        'snapshot_count', r.snapshot_count,
        'recovered_count', r.recovered_count,
        'item_count', (
            select count(*)
            from oasis_private.oasis_growth_kakao_recovery_items i
            where i.run_id = r.run_id
        ),
        'pending_snapshot_count', (
            select count(*)
            from oasis_private.oasis_growth_kakao_recovery_items i
            where i.run_id = r.run_id
              and i.prior_state is null
        ),
        'pending_recovery_count', (
            select count(*)
            from oasis_private.oasis_growth_kakao_recovery_items i
            where i.run_id = r.run_id
              and i.prior_state is not null
              and i.recovered_at is null
        )
    )
    into v_result
    from oasis_private.oasis_growth_kakao_recovery_runs r
    where r.recovery_key = v_recovery_key;

    return coalesce(
        v_result,
        jsonb_build_object(
            'status', 'not_started',
            'expected_count', 253796,
            'selected_count', 0,
            'snapshot_count', 0,
            'recovered_count', 0,
            'item_count', 0,
            'pending_snapshot_count', 0,
            'pending_recovery_count', 0
        )
    );
end
$$;

create or replace function oasis_private.oasis_prepare_growth_kakao_recovery()
returns jsonb
language plpgsql
volatile
security invoker
set search_path = pg_catalog, public, oasis_private, pg_temp
as $$
declare
    v_recovery_key constant text :=
        '20260805_growth_kakao_provider_error_recovery';
    v_expected_total constant integer := 253796;
    v_run_id uuid;
    v_lease_token uuid;
    v_status text;
    v_selected_count bigint;
    v_window_mismatch_count integer;
    v_window_source_mismatch_count integer;
    v_window_counts jsonb;
    v_window_source_counts jsonb;
    v_lease_acquired boolean;
begin
    perform pg_advisory_xact_lock(
        hashtextextended(v_recovery_key, 0)
    );

    select r.run_id, r.status
    into v_run_id, v_status
    from oasis_private.oasis_growth_kakao_recovery_runs r
    where r.recovery_key = v_recovery_key
    for update;

    if found and v_status = 'completed' then
        return oasis_private.oasis_growth_kakao_recovery_status();
    end if;

    if not found then
        v_run_id := gen_random_uuid();
        v_lease_token := gen_random_uuid();

        insert into oasis_private.oasis_growth_kakao_recovery_runs (
            recovery_key,
            run_id,
            expected_count,
            status
        ) values (
            v_recovery_key,
            v_run_id,
            v_expected_total,
            'prepared'
        );

        insert into oasis_private.oasis_growth_kakao_recovery_control (
            recovery_key,
            lease_token
        ) values (
            v_recovery_key,
            v_lease_token
        );
    else
        select c.lease_token
        into v_lease_token
        from oasis_private.oasis_growth_kakao_recovery_control c
        where c.recovery_key = v_recovery_key
          and c.released_at is null;

        if v_lease_token is null then
            raise exception
                'Growth Kakao recovery control is unavailable';
        end if;
    end if;

    select public.oasis_acquire_contact_provider_lease(
        'kakao_local',
        v_lease_token,
        3600
    )
    into v_lease_acquired;

    if not coalesce(v_lease_acquired, false) then
        raise exception
            'Kakao provider job is active; growth recovery aborted safely';
    end if;

    if exists (
        select 1
        from public.oasis_employment_contacts ec
        where ec.phone_provider_stage = 'naver'
          and ec.phone_status = 'processing'
    ) then
        raise exception
            'Naver provider job is active; growth recovery aborted safely';
    end if;

    insert into oasis_private.oasis_growth_kakao_recovery_items (
        run_id,
        contact_key,
        window_label,
        source_type
    )
    select
        v_run_id,
        ec.contact_key,
        w.window_label,
        ec.source_type
    from oasis_private.oasis_growth_kakao_recovery_windows w
    join public.oasis_employment_contacts ec
      on ec.phone_checked_at >= w.started_at
     and ec.phone_checked_at <= w.ended_at
    where w.recovery_key = v_recovery_key
      and ec.employee_growth > 0
      and ec.source_type in ('comwel_annual', 'nps_monthly')
      and ec.phone_provider_stage = 'naver'
      and ec.phone_status = 'pending'
      and ec.phone_attempt_count = 1
      and ec.phone_next_check_at = ec.phone_checked_at
      and coalesce(ec.phone_last_error, '') = ''
      and coalesce(ec.mobile_phone, '') = ''
      and coalesce(ec.landline_phone, '') = ''
    on conflict (run_id, contact_key) do nothing;

    select count(*)
    into v_selected_count
    from oasis_private.oasis_growth_kakao_recovery_items i
    where i.run_id = v_run_id;

    select count(*)
    into v_window_mismatch_count
    from oasis_private.oasis_growth_kakao_recovery_windows w
    left join lateral (
        select count(*)::bigint as actual_count
        from oasis_private.oasis_growth_kakao_recovery_items i
        where i.run_id = v_run_id
          and i.window_label = w.window_label
    ) observed on true
    where w.recovery_key = v_recovery_key
      and observed.actual_count <> w.expected_count;

    select jsonb_object_agg(
        w.window_label,
        observed.actual_count
        order by w.window_label
    )
    into v_window_counts
    from oasis_private.oasis_growth_kakao_recovery_windows w
    left join lateral (
        select count(*)::bigint as actual_count
        from oasis_private.oasis_growth_kakao_recovery_items i
        where i.run_id = v_run_id
          and i.window_label = w.window_label
    ) observed on true
    where w.recovery_key = v_recovery_key;

    with expected(window_label, source_type, expected_count) as (
        values
            ('w1'::text, 'comwel_annual'::text, 76154::bigint),
            ('w1'::text, 'nps_monthly'::text, 1100::bigint),
            ('w2'::text, 'comwel_annual'::text, 33257::bigint),
            ('w2'::text, 'nps_monthly'::text, 732::bigint),
            ('w3'::text, 'comwel_annual'::text, 97066::bigint),
            ('w3'::text, 'nps_monthly'::text, 1698::bigint),
            ('w4'::text, 'comwel_annual'::text, 42879::bigint),
            ('w4'::text, 'nps_monthly'::text, 910::bigint)
    ), observed as (
        select
            i.window_label,
            i.source_type,
            count(*)::bigint as actual_count
        from oasis_private.oasis_growth_kakao_recovery_items i
        where i.run_id = v_run_id
        group by i.window_label, i.source_type
    )
    select
        count(*) filter (
            where coalesce(o.actual_count, 0) <> e.expected_count
        ),
        jsonb_object_agg(
            e.window_label || ':' || e.source_type,
            coalesce(o.actual_count, 0)
            order by e.window_label, e.source_type
        )
    into v_window_source_mismatch_count, v_window_source_counts
    from expected e
    left join observed o
      on o.window_label = e.window_label
     and o.source_type = e.source_type;

    if v_selected_count <> v_expected_total
       or v_window_mismatch_count <> 0
       or v_window_source_mismatch_count <> 0 then
        raise exception
            'Growth Kakao recovery target count mismatch';
    end if;

    update oasis_private.oasis_growth_kakao_recovery_runs r
    set
        selected_count = v_selected_count,
        status = 'snapshotting',
        invariant_counts_before = jsonb_build_object(
            'selected_count', v_selected_count,
            'window_counts', v_window_counts,
            'window_source_counts', v_window_source_counts
        )
    where r.recovery_key = v_recovery_key
      and r.run_id = v_run_id;

    return oasis_private.oasis_growth_kakao_recovery_status();
end
$$;

create or replace function oasis_private.oasis_snapshot_growth_kakao_batch(
    p_batch_size integer default 5000
)
returns jsonb
language plpgsql
volatile
security invoker
set search_path = pg_catalog, public, oasis_private, pg_temp
as $$
declare
    v_recovery_key constant text :=
        '20260805_growth_kakao_provider_error_recovery';
    v_batch_size integer := greatest(
        1,
        least(coalesce(p_batch_size, 5000), 5000)
    );
    v_run_id uuid;
    v_status text;
    v_lease_token uuid;
    v_lease_acquired boolean;
    v_batch_count bigint;
    v_source_count bigint;
    v_snapshot_count bigint;
begin
    perform pg_advisory_xact_lock(
        hashtextextended(v_recovery_key, 0)
    );

    select r.run_id, r.status
    into v_run_id, v_status
    from oasis_private.oasis_growth_kakao_recovery_runs r
    where r.recovery_key = v_recovery_key
    for update;

    if not found then
        raise exception 'Growth Kakao recovery is not prepared';
    end if;

    if v_status = 'completed' then
        return oasis_private.oasis_growth_kakao_recovery_status();
    end if;

    if v_status not in ('snapshotting', 'recovering') then
        raise exception 'Growth Kakao recovery state is invalid';
    end if;

    select c.lease_token
    into v_lease_token
    from oasis_private.oasis_growth_kakao_recovery_control c
    where c.recovery_key = v_recovery_key
      and c.released_at is null;

    if v_lease_token is null then
        raise exception 'Growth Kakao recovery lease is unavailable';
    end if;

    select public.oasis_acquire_contact_provider_lease(
        'kakao_local',
        v_lease_token,
        3600
    )
    into v_lease_acquired;

    if not coalesce(v_lease_acquired, false) then
        raise exception
            'Kakao provider job is active; snapshot aborted safely';
    end if;

    if exists (
        select 1
        from public.oasis_employment_contacts ec
        where ec.phone_provider_stage = 'naver'
          and ec.phone_status = 'processing'
    ) then
        raise exception
            'Naver provider job is active; snapshot aborted safely';
    end if;

    with batch as materialized (
        select i.run_id, i.contact_key, i.window_label
        from oasis_private.oasis_growth_kakao_recovery_items i
        where i.run_id = v_run_id
          and i.prior_state is null
        order by i.contact_key
        limit v_batch_size
        for update of i skip locked
    ),
    source_rows as materialized (
        select
            b.run_id,
            b.contact_key,
            to_jsonb(ec) as prior_state
        from batch b
        join oasis_private.oasis_growth_kakao_recovery_windows w
          on w.recovery_key = v_recovery_key
         and w.window_label = b.window_label
        join public.oasis_employment_contacts ec
          on ec.contact_key = b.contact_key
        where ec.phone_checked_at >= w.started_at
          and ec.phone_checked_at <= w.ended_at
          and ec.employee_growth > 0
          and ec.source_type in ('comwel_annual', 'nps_monthly')
          and ec.phone_provider_stage = 'naver'
          and ec.phone_status = 'pending'
          and ec.phone_attempt_count = 1
          and ec.phone_next_check_at = ec.phone_checked_at
          and coalesce(ec.phone_last_error, '') = ''
          and coalesce(ec.mobile_phone, '') = ''
          and coalesce(ec.landline_phone, '') = ''
    ),
    snapshotted as (
        update oasis_private.oasis_growth_kakao_recovery_items i
        set
            prior_state = source_rows.prior_state,
            backed_up_at = clock_timestamp()
        from source_rows
        where i.run_id = source_rows.run_id
          and i.contact_key = source_rows.contact_key
          and i.prior_state is null
        returning i.contact_key
    )
    select
        (select count(*) from batch),
        (select count(*) from source_rows),
        (select count(*) from snapshotted)
    into v_batch_count, v_source_count, v_snapshot_count;

    if v_batch_count <> v_source_count
       or v_batch_count <> v_snapshot_count then
        raise exception
            'Growth Kakao snapshot signature mismatch';
    end if;

    if v_batch_count = 0 then
        update oasis_private.oasis_growth_kakao_recovery_runs r
        set status = 'recovering'
        where r.recovery_key = v_recovery_key
          and r.run_id = v_run_id
          and r.snapshot_count = r.selected_count
          and r.selected_count = r.expected_count;
    else
        update oasis_private.oasis_growth_kakao_recovery_runs r
        set snapshot_count = r.snapshot_count + v_snapshot_count
        where r.recovery_key = v_recovery_key
          and r.run_id = v_run_id;
    end if;

    return oasis_private.oasis_growth_kakao_recovery_status();
end
$$;

create or replace function oasis_private.oasis_recover_growth_kakao_batch(
    p_batch_size integer default 5000
)
returns jsonb
language plpgsql
volatile
security invoker
set search_path = pg_catalog, public, oasis_private, pg_temp
as $$
declare
    v_recovery_key constant text :=
        '20260805_growth_kakao_provider_error_recovery';
    v_batch_size integer := greatest(
        1,
        least(coalesce(p_batch_size, 5000), 5000)
    );
    v_mutated_columns constant text[] := array[
        'phone_provider_stage',
        'phone_status',
        'phone_checked_at',
        'phone_next_check_at',
        'phone_attempt_count',
        'phone_last_error',
        'updated_at'
    ]::text[];
    v_run_id uuid;
    v_status text;
    v_selected_count integer;
    v_snapshot_count integer;
    v_expected_count integer;
    v_lease_token uuid;
    v_lease_acquired boolean;
    v_batch_count bigint;
    v_valid_count bigint;
    v_updated_count bigint;
    v_post_valid_count bigint;
    v_marked_count bigint;
begin
    perform pg_advisory_xact_lock(
        hashtextextended(v_recovery_key, 0)
    );

    select
        r.run_id,
        r.status,
        r.selected_count,
        r.snapshot_count,
        r.expected_count
    into
        v_run_id,
        v_status,
        v_selected_count,
        v_snapshot_count,
        v_expected_count
    from oasis_private.oasis_growth_kakao_recovery_runs r
    where r.recovery_key = v_recovery_key
    for update;

    if not found then
        raise exception 'Growth Kakao recovery is not prepared';
    end if;

    if v_status = 'completed' then
        return oasis_private.oasis_growth_kakao_recovery_status();
    end if;

    if v_status <> 'recovering'
       or v_selected_count <> v_expected_count
       or v_snapshot_count <> v_expected_count then
        raise exception
            'Growth Kakao recovery snapshot is incomplete';
    end if;

    select c.lease_token
    into v_lease_token
    from oasis_private.oasis_growth_kakao_recovery_control c
    where c.recovery_key = v_recovery_key
      and c.released_at is null;

    if v_lease_token is null then
        raise exception 'Growth Kakao recovery lease is unavailable';
    end if;

    select public.oasis_acquire_contact_provider_lease(
        'kakao_local',
        v_lease_token,
        3600
    )
    into v_lease_acquired;

    if not coalesce(v_lease_acquired, false) then
        raise exception
            'Kakao provider job is active; recovery batch aborted safely';
    end if;

    if exists (
        select 1
        from public.oasis_employment_contacts ec
        where ec.phone_provider_stage = 'naver'
          and ec.phone_status = 'processing'
    ) then
        raise exception
            'Naver provider job is active; recovery batch aborted safely';
    end if;

    with batch as materialized (
        select
            i.run_id,
            i.contact_key,
            i.prior_state
        from oasis_private.oasis_growth_kakao_recovery_items i
        where i.run_id = v_run_id
          and i.prior_state is not null
          and i.recovered_at is null
        order by i.contact_key
        limit v_batch_size
        for update of i skip locked
    ),
    valid_rows as materialized (
        select b.run_id, b.contact_key, b.prior_state
        from batch b
        join public.oasis_employment_contacts ec
          on ec.contact_key = b.contact_key
        where to_jsonb(ec) is not distinct from b.prior_state
        for update of ec
    ),
    updated as (
        update public.oasis_employment_contacts ec
        set
            phone_provider_stage = 'kakao',
            phone_status = 'pending',
            phone_checked_at = null,
            phone_next_check_at = null,
            phone_attempt_count = 0,
            phone_last_error = ''
        from valid_rows v
        where ec.contact_key = v.contact_key
          and to_jsonb(ec) is not distinct from v.prior_state
        returning ec.contact_key, to_jsonb(ec) as current_state
    ),
    post_valid as materialized (
        select u.contact_key
        from updated u
        join valid_rows v
          on v.contact_key = u.contact_key
        where u.current_state->>'phone_provider_stage' = 'kakao'
          and u.current_state->>'phone_status' = 'pending'
          and u.current_state->'phone_checked_at' = 'null'::jsonb
          and u.current_state->'phone_next_check_at' = 'null'::jsonb
          and (u.current_state->>'phone_attempt_count')::integer = 0
          and coalesce(u.current_state->>'phone_last_error', '') = ''
          and (
              u.current_state - v_mutated_columns
          ) is not distinct from (
              v.prior_state - v_mutated_columns
          )
    ),
    marked as (
        update oasis_private.oasis_growth_kakao_recovery_items i
        set recovered_at = clock_timestamp()
        from post_valid p
        where i.run_id = v_run_id
          and i.contact_key = p.contact_key
          and i.recovered_at is null
        returning i.contact_key
    )
    select
        (select count(*) from batch),
        (select count(*) from valid_rows),
        (select count(*) from updated),
        (select count(*) from post_valid),
        (select count(*) from marked)
    into
        v_batch_count,
        v_valid_count,
        v_updated_count,
        v_post_valid_count,
        v_marked_count;

    if v_batch_count <> v_valid_count
       or v_batch_count <> v_updated_count
       or v_batch_count <> v_post_valid_count
       or v_batch_count <> v_marked_count then
        raise exception
            'Growth Kakao recovery batch invariant mismatch';
    end if;

    if v_batch_count > 0 then
        update oasis_private.oasis_growth_kakao_recovery_runs r
        set recovered_count = r.recovered_count + v_marked_count
        where r.recovery_key = v_recovery_key
          and r.run_id = v_run_id;
    end if;

    return oasis_private.oasis_growth_kakao_recovery_status();
end
$$;

create or replace function oasis_private.oasis_finalize_growth_kakao_recovery()
returns jsonb
language plpgsql
volatile
security invoker
set search_path = pg_catalog, public, oasis_private, pg_temp
as $$
declare
    v_recovery_key constant text :=
        '20260805_growth_kakao_provider_error_recovery';
    v_expected_total constant integer := 253796;
    v_mutated_columns constant text[] := array[
        'phone_provider_stage',
        'phone_status',
        'phone_checked_at',
        'phone_next_check_at',
        'phone_attempt_count',
        'phone_last_error',
        'updated_at'
    ]::text[];
    v_run_id uuid;
    v_status text;
    v_lease_token uuid;
    v_lease_acquired boolean;
    v_lease_released boolean;
    v_item_count bigint;
    v_snapshot_count bigint;
    v_recovered_count bigint;
    v_missing_count bigint;
    v_invalid_state_count bigint;
    v_preserved_difference_count bigint;
begin
    perform pg_advisory_xact_lock(
        hashtextextended(v_recovery_key, 0)
    );

    select r.run_id, r.status
    into v_run_id, v_status
    from oasis_private.oasis_growth_kakao_recovery_runs r
    where r.recovery_key = v_recovery_key
    for update;

    if not found then
        raise exception 'Growth Kakao recovery is not prepared';
    end if;

    if v_status = 'completed' then
        return oasis_private.oasis_growth_kakao_recovery_status();
    end if;

    if v_status <> 'recovering' then
        raise exception 'Growth Kakao recovery is not ready to finalize';
    end if;

    select c.lease_token
    into v_lease_token
    from oasis_private.oasis_growth_kakao_recovery_control c
    where c.recovery_key = v_recovery_key
      and c.released_at is null;

    if v_lease_token is null then
        raise exception 'Growth Kakao recovery lease is unavailable';
    end if;

    select public.oasis_acquire_contact_provider_lease(
        'kakao_local',
        v_lease_token,
        3600
    )
    into v_lease_acquired;

    if not coalesce(v_lease_acquired, false) then
        raise exception
            'Kakao provider job is active; finalize aborted safely';
    end if;

    if exists (
        select 1
        from public.oasis_employment_contacts ec
        where ec.phone_provider_stage = 'naver'
          and ec.phone_status = 'processing'
    ) then
        raise exception
            'Naver provider job is active; finalize aborted safely';
    end if;

    select
        count(*),
        count(*) filter (where i.prior_state is not null),
        count(*) filter (where i.recovered_at is not null)
    into
        v_item_count,
        v_snapshot_count,
        v_recovered_count
    from oasis_private.oasis_growth_kakao_recovery_items i
    where i.run_id = v_run_id;

    select count(*)
    into v_missing_count
    from oasis_private.oasis_growth_kakao_recovery_items i
    left join public.oasis_employment_contacts ec
      on ec.contact_key = i.contact_key
    where i.run_id = v_run_id
      and ec.contact_key is null;

    select count(*)
    into v_invalid_state_count
    from oasis_private.oasis_growth_kakao_recovery_items i
    join public.oasis_employment_contacts ec
      on ec.contact_key = i.contact_key
    where i.run_id = v_run_id
      and (
          ec.phone_provider_stage <> 'kakao'
          or ec.phone_status <> 'pending'
          or ec.phone_checked_at is not null
          or ec.phone_next_check_at is not null
          or ec.phone_attempt_count <> 0
          or coalesce(ec.phone_last_error, '') <> ''
      );

    select count(*)
    into v_preserved_difference_count
    from oasis_private.oasis_growth_kakao_recovery_items i
    join public.oasis_employment_contacts ec
      on ec.contact_key = i.contact_key
    where i.run_id = v_run_id
      and (
          to_jsonb(ec) - v_mutated_columns
      ) is distinct from (
          i.prior_state - v_mutated_columns
      );

    if v_item_count <> v_expected_total
       or v_snapshot_count <> v_expected_total
       or v_recovered_count <> v_expected_total
       or v_missing_count <> 0
       or v_invalid_state_count <> 0
       or v_preserved_difference_count <> 0 then
        raise exception
            'Growth Kakao final recovery invariant mismatch';
    end if;

    update oasis_private.oasis_growth_kakao_recovery_runs r
    set
        selected_count = v_item_count,
        snapshot_count = v_snapshot_count,
        recovered_count = v_recovered_count,
        status = 'completed',
        invariant_counts_after = jsonb_build_object(
            'item_count', v_item_count,
            'snapshot_count', v_snapshot_count,
            'recovered_count', v_recovered_count,
            'missing_count', v_missing_count,
            'invalid_state_count', v_invalid_state_count,
            'preserved_difference_count',
                v_preserved_difference_count
        ),
        completed_at = clock_timestamp()
    where r.recovery_key = v_recovery_key
      and r.run_id = v_run_id;

    select public.oasis_release_contact_provider_lease(
        'kakao_local',
        v_lease_token
    )
    into v_lease_released;

    if not coalesce(v_lease_released, false) then
        raise exception
            'Growth Kakao recovery lease release failed';
    end if;

    update oasis_private.oasis_growth_kakao_recovery_control c
    set
        lease_token = null,
        released_at = clock_timestamp()
    where c.recovery_key = v_recovery_key
      and c.lease_token = v_lease_token
      and c.released_at is null;

    if not found then
        raise exception
            'Growth Kakao recovery control release failed';
    end if;

    return oasis_private.oasis_growth_kakao_recovery_status();
end
$$;

revoke all on function
    oasis_private.oasis_growth_kakao_recovery_status()
from public, anon, authenticated, service_role;
revoke all on function
    oasis_private.oasis_prepare_growth_kakao_recovery()
from public, anon, authenticated, service_role;
revoke all on function
    oasis_private.oasis_snapshot_growth_kakao_batch(integer)
from public, anon, authenticated, service_role;
revoke all on function
    oasis_private.oasis_recover_growth_kakao_batch(integer)
from public, anon, authenticated, service_role;
revoke all on function
    oasis_private.oasis_finalize_growth_kakao_recovery()
from public, anon, authenticated, service_role;

commit;
