begin;

alter table oasis_private.oasis_growth_kakao_recovery_runs
    add constraint oasis_growth_kakao_recovery_runs_count_order_check
    check (
        recovered_count <= snapshot_count
        and snapshot_count <= selected_count
        and selected_count <= expected_count
    );

create or replace function oasis_private.oasis_growth_kakao_recovery_status()
returns jsonb
language sql
stable
security invoker
set search_path = pg_catalog, public, oasis_private, pg_temp
as $$
    select coalesce(
        (
            select jsonb_build_object(
                'status', r.status,
                'expected_count', r.expected_count,
                'selected_count', r.selected_count,
                'snapshot_count', r.snapshot_count,
                'recovered_count', r.recovered_count,
                'item_count', r.selected_count,
                'pending_snapshot_count',
                    r.selected_count - r.snapshot_count,
                'pending_recovery_count',
                    r.snapshot_count - r.recovered_count
            )
            from oasis_private.oasis_growth_kakao_recovery_runs r
            where r.recovery_key =
                '20260805_growth_kakao_provider_error_recovery'
        ),
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
$$;

revoke all on function
    oasis_private.oasis_growth_kakao_recovery_status()
from public, anon, authenticated, service_role;

commit;
