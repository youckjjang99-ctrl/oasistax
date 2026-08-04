begin;

alter table oasis_private.oasis_growth_kakao_recovery_items
    drop constraint oasis_growth_kakao_recovery_items_verified_check;

alter table oasis_private.oasis_growth_kakao_recovery_items
    add constraint oasis_growth_kakao_recovery_items_verified_check
    check (
        verified_at is null
        or (
            recovered_at is not null
            and verified_at >= recovered_at
        )
    );

create or replace function oasis_private.oasis_verify_growth_kakao_batch(
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
    v_expected_count integer;
    v_selected_count integer;
    v_snapshot_count integer;
    v_recovered_count integer;
    v_lease_token uuid;
    v_lease_acquired boolean;
    v_batch_count bigint;
    v_valid_count bigint;
    v_marked_count bigint;
begin
    perform pg_advisory_xact_lock(
        hashtextextended(v_recovery_key, 0)
    );

    select
        r.run_id,
        r.status,
        r.expected_count,
        r.selected_count,
        r.snapshot_count,
        r.recovered_count
    into
        v_run_id,
        v_status,
        v_expected_count,
        v_selected_count,
        v_snapshot_count,
        v_recovered_count
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
       or v_snapshot_count <> v_expected_count
       or v_recovered_count <> v_expected_count then
        raise exception
            'Growth Kakao recovery is not ready to verify';
    end if;

    select c.lease_token
    into v_lease_token
    from oasis_private.oasis_growth_kakao_recovery_control c
    where c.recovery_key = v_recovery_key
      and c.released_at is null;

    if v_lease_token is null then
        raise exception 'Growth Kakao recovery lease is unavailable';
    end if;

    select public.oasis_renew_contact_provider_lease(
        'kakao_local',
        v_lease_token,
        3600
    )
    into v_lease_acquired;

    if not coalesce(v_lease_acquired, false) then
        raise exception
            'Kakao provider job is active; verification aborted safely';
    end if;

    if exists (
        select 1
        from public.oasis_employment_contacts ec
        where ec.phone_provider_stage = 'naver'
          and ec.phone_status = 'processing'
    ) then
        raise exception
            'Naver provider job is active; verification aborted safely';
    end if;

    with batch as materialized (
        select
            i.run_id,
            i.contact_key,
            i.prior_state
        from oasis_private.oasis_growth_kakao_recovery_items i
        where i.run_id = v_run_id
          and i.recovered_at is not null
          and i.verified_at is null
        order by i.contact_key
        limit v_batch_size
        for update of i skip locked
    ),
    valid_rows as materialized (
        select b.run_id, b.contact_key
        from batch b
        join public.oasis_employment_contacts ec
          on ec.contact_key = b.contact_key
        where ec.phone_provider_stage = 'kakao'
          and ec.phone_status = 'pending'
          and ec.phone_checked_at is null
          and ec.phone_next_check_at is null
          and ec.phone_attempt_count = 0
          and coalesce(ec.phone_last_error, '') = ''
          and (
              to_jsonb(ec) - v_mutated_columns
          ) is not distinct from (
              b.prior_state - v_mutated_columns
          )
        order by b.contact_key
        for share of ec
    ),
    marked as (
        update oasis_private.oasis_growth_kakao_recovery_items i
        set verified_at = clock_timestamp()
        from valid_rows v
        where i.run_id = v.run_id
          and i.contact_key = v.contact_key
          and i.verified_at is null
        returning i.contact_key
    )
    select
        (select count(*) from batch),
        (select count(*) from valid_rows),
        (select count(*) from marked)
    into v_batch_count, v_valid_count, v_marked_count;

    if v_batch_count <> v_valid_count
       or v_batch_count <> v_marked_count then
        raise exception
            'Growth Kakao verification batch invariant mismatch';
    end if;

    if v_batch_count > 0 then
        update oasis_private.oasis_growth_kakao_recovery_runs r
        set verified_count = r.verified_count + v_marked_count
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
    v_run_id uuid;
    v_status text;
    v_selected_count integer;
    v_snapshot_count integer;
    v_recovered_count integer;
    v_verified_count integer;
    v_lease_token uuid;
    v_lease_acquired boolean;
    v_lease_released boolean;
    v_item_count bigint;
    v_actual_snapshot_count bigint;
    v_actual_recovered_count bigint;
    v_actual_verified_count bigint;
begin
    perform pg_advisory_xact_lock(
        hashtextextended(v_recovery_key, 0)
    );

    select
        r.run_id,
        r.status,
        r.selected_count,
        r.snapshot_count,
        r.recovered_count,
        r.verified_count
    into
        v_run_id,
        v_status,
        v_selected_count,
        v_snapshot_count,
        v_recovered_count,
        v_verified_count
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
       or v_selected_count <> v_expected_total
       or v_snapshot_count <> v_expected_total
       or v_recovered_count <> v_expected_total
       or v_verified_count <> v_expected_total then
        raise exception 'Growth Kakao recovery verification is incomplete';
    end if;

    select c.lease_token
    into v_lease_token
    from oasis_private.oasis_growth_kakao_recovery_control c
    where c.recovery_key = v_recovery_key
      and c.released_at is null;

    if v_lease_token is null then
        raise exception 'Growth Kakao recovery lease is unavailable';
    end if;

    select public.oasis_renew_contact_provider_lease(
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
        count(*) filter (where i.recovered_at is not null),
        count(*) filter (where i.verified_at is not null)
    into
        v_item_count,
        v_actual_snapshot_count,
        v_actual_recovered_count,
        v_actual_verified_count
    from oasis_private.oasis_growth_kakao_recovery_items i
    where i.run_id = v_run_id;

    if v_item_count <> v_expected_total
       or v_actual_snapshot_count <> v_expected_total
       or v_actual_recovered_count <> v_expected_total
       or v_actual_verified_count <> v_expected_total then
        raise exception
            'Growth Kakao final recovery count mismatch';
    end if;

    update oasis_private.oasis_growth_kakao_recovery_runs r
    set
        status = 'completed',
        invariant_counts_after = jsonb_build_object(
            'item_count', v_item_count,
            'snapshot_count', v_actual_snapshot_count,
            'recovered_count', v_actual_recovered_count,
            'verified_count', v_actual_verified_count,
            'missing_count', 0,
            'invalid_state_count', 0,
            'preserved_difference_count', 0
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
    oasis_private.oasis_verify_growth_kakao_batch(integer)
from public, anon, authenticated, service_role;
revoke all on function
    oasis_private.oasis_finalize_growth_kakao_recovery()
from public, anon, authenticated, service_role;

commit;
