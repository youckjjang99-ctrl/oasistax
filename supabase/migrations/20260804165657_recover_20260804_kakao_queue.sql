begin;

create schema if not exists oasis_private;
revoke all on schema oasis_private from public, anon, authenticated;
revoke all on schema oasis_private from service_role;
grant usage on schema oasis_private to service_role;

create table if not exists oasis_private.oasis_kakao_queue_recovery_runs (
    recovery_key text primary key,
    run_id uuid not null unique,
    expected_moved_count integer not null
        check (expected_moved_count >= 0),
    expected_stuck_count integer not null
        check (expected_stuck_count >= 0),
    snapshot_count integer not null default 0
        check (snapshot_count >= 0),
    recovered_count integer not null default 0
        check (recovered_count >= 0),
    status text not null default 'running'
        check (status in ('running', 'completed')),
    invariant_counts_before jsonb not null default '{}'::jsonb
        check (jsonb_typeof(invariant_counts_before) = 'object'),
    invariant_counts_after jsonb not null default '{}'::jsonb
        check (jsonb_typeof(invariant_counts_after) = 'object'),
    created_at timestamptz not null default now(),
    completed_at timestamptz
);

create table if not exists oasis_private.oasis_kakao_queue_recovery_items (
    run_id uuid not null
        references oasis_private.oasis_kakao_queue_recovery_runs(run_id)
        on delete restrict,
    contact_key text not null,
    cohort text not null check (cohort in ('moved', 'stuck')),
    prior_state jsonb not null
        check (jsonb_typeof(prior_state) = 'object'),
    backed_up_at timestamptz not null default now(),
    primary key (run_id, contact_key)
);

alter table oasis_private.oasis_kakao_queue_recovery_runs
    enable row level security;
alter table oasis_private.oasis_kakao_queue_recovery_runs
    force row level security;
alter table oasis_private.oasis_kakao_queue_recovery_items
    enable row level security;
alter table oasis_private.oasis_kakao_queue_recovery_items
    force row level security;

revoke all on table
    oasis_private.oasis_kakao_queue_recovery_runs,
    oasis_private.oasis_kakao_queue_recovery_items
from public, anon, authenticated;

revoke all on table
    oasis_private.oasis_kakao_queue_recovery_runs,
    oasis_private.oasis_kakao_queue_recovery_items
from service_role;

grant select on table
    oasis_private.oasis_kakao_queue_recovery_runs,
    oasis_private.oasis_kakao_queue_recovery_items
to service_role;

comment on table oasis_private.oasis_kakao_queue_recovery_runs is
    'Internal audit summary for the guarded Kakao queue recovery.';
comment on table oasis_private.oasis_kakao_queue_recovery_items is
    'Private full-row snapshots retained for an exact rollback.';

do $$
declare
    v_recovery_key constant text :=
        '20260804_kakao_provider_error_queue_recovery';
    v_run_id uuid;
    v_recovery_lease_token uuid;
    v_lease_acquired boolean;
    v_lease_released boolean;
    v_expected_moved constant integer := 54238;
    v_expected_moved_comwel constant integer := 54235;
    v_expected_moved_nps constant integer := 3;
    v_expected_stuck constant integer := 12;
    v_expected_total constant integer := 54250;
    v_existing_status text;
    v_existing_snapshot_count integer;
    v_existing_recovered_count integer;
    v_existing_item_count bigint;
    v_moved_count bigint;
    v_moved_comwel_count bigint;
    v_moved_nps_count bigint;
    v_stuck_count bigint;
    v_target_count bigint;
    v_signature_count bigint;
    v_snapshot_count bigint;
    v_updated_count bigint;
    v_missing_count bigint;
    v_invalid_state_count bigint;
    v_preserved_difference_count bigint;
    v_remaining_moved_count bigint;
    v_invariants_before jsonb;
    v_invariants_after jsonb;
    v_mutated_columns constant text[] := array[
        'phone_provider_stage',
        'phone_status',
        'phone_checked_at',
        'phone_next_check_at',
        'phone_attempt_count',
        'phone_last_error',
        'status',
        'checked_at',
        'next_check_at',
        'attempt_count',
        'last_error',
        'updated_at'
    ]::text[];
begin
    -- Serialize this one-time recovery even if two migration runners start.
    perform pg_advisory_xact_lock(
        hashtextextended(v_recovery_key, 0)
    );

    select
        run_id,
        status,
        snapshot_count,
        recovered_count
    into
        v_run_id,
        v_existing_status,
        v_existing_snapshot_count,
        v_existing_recovered_count
    from oasis_private.oasis_kakao_queue_recovery_runs
    where recovery_key = v_recovery_key;

    if found then
        select count(*)
        into v_existing_item_count
        from oasis_private.oasis_kakao_queue_recovery_items
        where run_id = v_run_id;

        if v_existing_status = 'completed'
           and v_existing_snapshot_count = v_expected_total
           and v_existing_recovered_count = v_expected_total
           and v_existing_item_count = v_expected_total then
            -- A completed, internally consistent run is a safe no-op.
            return;
        end if;

        raise exception
            'Kakao queue recovery marker is incomplete or inconsistent';
    end if;

    v_run_id := gen_random_uuid();
    v_recovery_lease_token := gen_random_uuid();

    -- Refuse to race an active Kakao collector. The lease row remains locked
    -- for this transaction and is released only after every invariant passes.
    select public.oasis_acquire_contact_provider_lease(
        'kakao_local',
        v_recovery_lease_token,
        3600
    )
    into v_lease_acquired;

    if not coalesce(v_lease_acquired, false) then
        raise exception
            'Kakao provider job is active; recovery aborted safely';
    end if;

    create temporary table oasis_kakao_queue_recovery_targets (
        contact_key text primary key,
        cohort text not null check (cohort in ('moved', 'stuck'))
    ) on commit drop;

    -- Exact rows incorrectly moved from Kakao to Naver during the incident.
    insert into oasis_kakao_queue_recovery_targets (
        contact_key,
        cohort
    )
    select
        ec.contact_key,
        'moved'
    from public.oasis_employment_contacts ec
    where ec.source_type in ('comwel_all_employers', 'nps_monthly')
      and ec.phone_provider_stage = 'naver'
      and ec.phone_status = 'pending'
      and ec.status = 'pending'
      and ec.employee_growth = 0
      and ec.is_new_company is false
      and ec.phone_attempt_count = 1
      and ec.attempt_count = 1
      and ec.phone_checked_at = ec.checked_at
      and ec.phone_next_check_at = ec.phone_checked_at
      and ec.next_check_at = ec.checked_at
      and ec.phone_checked_at >= timestamptz '2026-08-03 17:44:51+00'
      and ec.phone_checked_at < timestamptz '2026-08-04 04:30:32+00'
      and ec.mobile_phone = ''
      and ec.landline_phone = ''
      and ec.email = ''
      and ec.instagram_id = ''
      and ec.instagram_url = ''
      and ec.contact_sources = '{}'::jsonb
      and ec.phone_last_error = ''
      and ec.last_error = ''
      and ec.digital_status = 'pending'
      and ec.digital_attempt_count = 0
    for update of ec;

    get diagnostics v_moved_count = row_count;

    select
        count(*) filter (
            where ec.source_type = 'comwel_all_employers'
        ),
        count(*) filter (
            where ec.source_type = 'nps_monthly'
        )
    into
        v_moved_comwel_count,
        v_moved_nps_count
    from oasis_kakao_queue_recovery_targets target
    join public.oasis_employment_contacts ec
      on ec.contact_key = target.contact_key
    where target.cohort = 'moved';

    if v_moved_count <> v_expected_moved
       or v_moved_comwel_count <> v_expected_moved_comwel
       or v_moved_nps_count <> v_expected_moved_nps then
        raise exception
            'Kakao moved cohort count mismatch; recovery aborted';
    end if;

    -- Exact rows left processing when the same run stopped.
    insert into oasis_kakao_queue_recovery_targets (
        contact_key,
        cohort
    )
    select
        ec.contact_key,
        'stuck'
    from public.oasis_employment_contacts ec
    where ec.source_type = 'comwel_all_employers'
      and ec.created_at =
          timestamptz '2026-08-04 03:05:22.347848+00'
      and ec.phone_provider_stage = 'kakao'
      and ec.phone_status = 'processing'
      and ec.status = 'pending'
      and ec.employee_growth = 0
      and ec.is_new_company is false
      and ec.phone_attempt_count = 0
      and ec.attempt_count = 0
      and ec.phone_checked_at is null
      and ec.phone_next_check_at is null
      and ec.checked_at is null
      and ec.next_check_at is null
      and ec.phone_last_error = ''
      and ec.last_error = ''
      and ec.mobile_phone = ''
      and ec.landline_phone = ''
      and ec.email = ''
      and ec.instagram_id = ''
      and ec.instagram_url = ''
      and ec.contact_sources = '{}'::jsonb
      and ec.digital_status = 'pending'
      and ec.digital_attempt_count = 0
      and ec.updated_at >= timestamptz '2026-08-04 04:30:32+00'
      and ec.updated_at < timestamptz '2026-08-04 04:30:33+00'
    for update of ec;

    get diagnostics v_stuck_count = row_count;

    select count(*)
    into v_target_count
    from oasis_kakao_queue_recovery_targets;

    if v_stuck_count <> v_expected_stuck
       or v_target_count <> v_expected_total then
        raise exception
            'Kakao stuck or total cohort count mismatch; recovery aborted';
    end if;

    -- Recheck every locked row immediately before the backup and update.
    select count(*)
    into v_signature_count
    from oasis_kakao_queue_recovery_targets target
    join public.oasis_employment_contacts ec
      on ec.contact_key = target.contact_key
    where (
        target.cohort = 'moved'
        and ec.source_type in ('comwel_all_employers', 'nps_monthly')
        and ec.phone_provider_stage = 'naver'
        and ec.phone_status = 'pending'
        and ec.status = 'pending'
        and ec.employee_growth = 0
        and ec.is_new_company is false
        and ec.phone_attempt_count = 1
        and ec.attempt_count = 1
        and ec.phone_checked_at = ec.checked_at
        and ec.phone_next_check_at = ec.phone_checked_at
        and ec.next_check_at = ec.checked_at
        and ec.phone_checked_at >=
            timestamptz '2026-08-03 17:44:51+00'
        and ec.phone_checked_at <
            timestamptz '2026-08-04 04:30:32+00'
        and ec.mobile_phone = ''
        and ec.landline_phone = ''
        and ec.email = ''
        and ec.instagram_id = ''
        and ec.instagram_url = ''
        and ec.contact_sources = '{}'::jsonb
        and ec.phone_last_error = ''
        and ec.last_error = ''
        and ec.digital_status = 'pending'
        and ec.digital_attempt_count = 0
    ) or (
        target.cohort = 'stuck'
        and ec.source_type = 'comwel_all_employers'
        and ec.created_at =
            timestamptz '2026-08-04 03:05:22.347848+00'
        and ec.phone_provider_stage = 'kakao'
        and ec.phone_status = 'processing'
        and ec.status = 'pending'
        and ec.employee_growth = 0
        and ec.is_new_company is false
        and ec.phone_attempt_count = 0
        and ec.attempt_count = 0
        and ec.phone_checked_at is null
        and ec.phone_next_check_at is null
        and ec.checked_at is null
        and ec.next_check_at is null
        and ec.phone_last_error = ''
        and ec.last_error = ''
        and ec.mobile_phone = ''
        and ec.landline_phone = ''
        and ec.email = ''
        and ec.instagram_id = ''
        and ec.instagram_url = ''
        and ec.contact_sources = '{}'::jsonb
        and ec.digital_status = 'pending'
        and ec.digital_attempt_count = 0
        and ec.updated_at >=
            timestamptz '2026-08-04 04:30:32+00'
        and ec.updated_at <
            timestamptz '2026-08-04 04:30:33+00'
    );

    if v_signature_count <> v_expected_total then
        raise exception
            'Kakao recovery cohort changed before snapshot; recovery aborted';
    end if;

    -- Avoid a full-table scan over the multi-million-row contact table. Every
    -- target row is locked and signature-checked above; the full prior row is
    -- snapshotted below, and non-mutated columns are compared after update.
    v_invariants_before := jsonb_build_object(
        'target_count', v_target_count,
        'moved_count', v_moved_count,
        'stuck_count', v_stuck_count,
        'signature_count', v_signature_count
    );

    insert into oasis_private.oasis_kakao_queue_recovery_runs (
        recovery_key,
        run_id,
        expected_moved_count,
        expected_stuck_count,
        invariant_counts_before
    ) values (
        v_recovery_key,
        v_run_id,
        v_expected_moved,
        v_expected_stuck,
        v_invariants_before
    );

    insert into oasis_private.oasis_kakao_queue_recovery_items (
        run_id,
        contact_key,
        cohort,
        prior_state
    )
    select
        v_run_id,
        ec.contact_key,
        target.cohort,
        to_jsonb(ec)
    from oasis_kakao_queue_recovery_targets target
    join public.oasis_employment_contacts ec
      on ec.contact_key = target.contact_key;

    get diagnostics v_snapshot_count = row_count;

    select count(*)
    into v_missing_count
    from oasis_kakao_queue_recovery_targets target
    left join oasis_private.oasis_kakao_queue_recovery_items backup
      on backup.run_id = v_run_id
     and backup.contact_key = target.contact_key
    where backup.contact_key is null;

    if v_snapshot_count <> v_expected_total
       or v_missing_count <> 0 then
        raise exception
            'Kakao recovery snapshot is incomplete; recovery aborted';
    end if;

    -- Update strictly through the successfully persisted backup keys.
    update public.oasis_employment_contacts ec
    set
        phone_provider_stage = 'kakao',
        phone_status = 'pending',
        phone_checked_at = null,
        phone_next_check_at = null,
        phone_attempt_count = 0,
        phone_last_error = '',
        status = 'pending',
        checked_at = null,
        next_check_at = null,
        attempt_count = 0,
        last_error = ''
    from oasis_private.oasis_kakao_queue_recovery_items backup
    where backup.run_id = v_run_id
      and backup.contact_key = ec.contact_key;

    get diagnostics v_updated_count = row_count;

    if v_updated_count <> v_expected_total then
        raise exception
            'Kakao recovery update count mismatch; recovery aborted';
    end if;

    select count(*)
    into v_missing_count
    from oasis_private.oasis_kakao_queue_recovery_items backup
    left join public.oasis_employment_contacts ec
      on ec.contact_key = backup.contact_key
    where backup.run_id = v_run_id
      and ec.contact_key is null;

    select count(*)
    into v_invalid_state_count
    from oasis_private.oasis_kakao_queue_recovery_items backup
    join public.oasis_employment_contacts ec
      on ec.contact_key = backup.contact_key
    where backup.run_id = v_run_id
      and (
          ec.phone_provider_stage <> 'kakao'
          or ec.phone_status <> 'pending'
          or ec.phone_checked_at is not null
          or ec.phone_next_check_at is not null
          or ec.phone_attempt_count <> 0
          or ec.phone_last_error <> ''
          or ec.status <> 'pending'
          or ec.checked_at is not null
          or ec.next_check_at is not null
          or ec.attempt_count <> 0
          or ec.last_error <> ''
      );

    select count(*)
    into v_preserved_difference_count
    from oasis_private.oasis_kakao_queue_recovery_items backup
    join public.oasis_employment_contacts ec
      on ec.contact_key = backup.contact_key
    where backup.run_id = v_run_id
      and (
          to_jsonb(ec) - v_mutated_columns
      ) is distinct from (
          backup.prior_state - v_mutated_columns
      );

    select count(*)
    into v_remaining_moved_count
    from oasis_private.oasis_kakao_queue_recovery_items backup
    join public.oasis_employment_contacts ec
      on ec.contact_key = backup.contact_key
    where backup.run_id = v_run_id
      and backup.cohort = 'moved'
      and ec.phone_provider_stage = 'naver'
      and ec.phone_status = 'pending'
      and ec.status = 'pending';

    v_invariants_after := jsonb_build_object(
        'target_count', v_target_count,
        'updated_count', v_updated_count,
        'missing_count', v_missing_count,
        'invalid_state_count', v_invalid_state_count,
        'preserved_difference_count', v_preserved_difference_count,
        'remaining_moved_count', v_remaining_moved_count
    );

    if v_missing_count <> 0
       or v_invalid_state_count <> 0
       or v_preserved_difference_count <> 0
       or v_remaining_moved_count <> 0 then
        raise exception
            'Kakao recovery invariant verification failed; recovery aborted';
    end if;

    update oasis_private.oasis_kakao_queue_recovery_runs
    set
        snapshot_count = v_snapshot_count,
        recovered_count = v_updated_count,
        status = 'completed',
        invariant_counts_after = v_invariants_after,
        completed_at = now()
    where recovery_key = v_recovery_key
      and run_id = v_run_id
      and status = 'running';

    if not found then
        raise exception
            'Kakao recovery completion marker failed; recovery aborted';
    end if;

    select public.oasis_release_contact_provider_lease(
        'kakao_local',
        v_recovery_lease_token
    )
    into v_lease_released;

    if not coalesce(v_lease_released, false) then
        raise exception
            'Kakao recovery lease release failed; recovery aborted';
    end if;
end
$$;

commit;
