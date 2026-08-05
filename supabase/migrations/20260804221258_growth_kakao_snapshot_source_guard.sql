begin;

alter table oasis_private.oasis_growth_kakao_recovery_items
    add constraint oasis_growth_kakao_recovery_items_source_snapshot_check
    check (
        prior_state is null
        or prior_state->>'source_type' = source_type
    ) not valid;

alter table oasis_private.oasis_growth_kakao_recovery_items
    validate constraint
        oasis_growth_kakao_recovery_items_source_snapshot_check;

commit;
