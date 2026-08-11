begin;

-- Admin users can also own sales DBs. A return from the normal activity UI
-- must enter the same review queue regardless of the user's role.
update public.oasis_company_sales_assignments
set
    status = 'long_hold',
    last_status_changed_at = now(),
    updated_at = now()
where status = 'unassigned'
  and assigned_user_id is null
  and released_reason = 'contact_results_return'
  and coalesce(permanently_excluded, false) is false;

create or replace function public.oasis_release_company_sales_assignment(
    p_current_user_id text,
    p_company_uid text,
    p_reason text default 'user_released',
    p_session_id text default null,
    p_return_reason text default null
)
returns table (
    success boolean,
    code text,
    message text,
    assignment_id uuid,
    company_uid text,
    status text,
    assigned_at timestamptz,
    assignment_expires_at timestamptz
)
language plpgsql
volatile
set search_path = public, pg_temp
as $$
declare
    v_uid text;
    v_assignment record;
    v_saved record;
    v_is_admin boolean;
    v_release_reason text;
    v_return_reason text := left(btrim(coalesce(p_return_reason, '')), 500);
begin
    if not public.oasis_sales_actor_is_active(p_current_user_id) then
        raise exception using errcode = '42501', message = 'OASIS_SALES_USER_NOT_AUTHORIZED';
    end if;

    v_is_admin := public.oasis_sales_actor_is_admin(p_current_user_id);
    v_uid := public.oasis_resolve_company_sales_uid(null, p_company_uid);
    if v_uid is null then
        return query select false, 'invalid_company_uid', '업체 공통 식별키를 확인할 수 없습니다.',
            null::uuid, null::text, null::text, null::timestamptz, null::timestamptz;
        return;
    end if;

    perform pg_advisory_xact_lock(hashtextextended('oasis-company:' || v_uid, 0));
    select a.* into v_assignment
    from public.oasis_company_sales_assignments a
    where a.company_uid = v_uid
    for update;

    if v_assignment.id is null then
        return query select false, 'assignment_not_found', '배정 정보를 찾을 수 없습니다.',
            null::uuid, v_uid, null::text, null::timestamptz, null::timestamptz;
        return;
    end if;

    if v_assignment.permanently_excluded then
        return query select false, 'permanently_excluded', '영구 제외는 관리자 재활성화로만 해제할 수 있습니다.',
            v_assignment.id, v_uid, v_assignment.status,
            v_assignment.assigned_at, v_assignment.assignment_expires_at;
        return;
    end if;

    if v_assignment.migration_conflict then
        return query select false, 'migration_conflict', '기존 중복 저장 충돌은 관리자 담당자 지정으로만 해결할 수 있습니다.',
            v_assignment.id, v_uid, v_assignment.status,
            v_assignment.assigned_at, v_assignment.assignment_expires_at;
        return;
    end if;

    if not v_is_admin and (
        v_assignment.assigned_user_id <> p_current_user_id
        or v_assignment.status not in ('assigned', 'pending_contact')
        or v_assignment.current_assignment_contact_count > 0
        or v_assignment.current_assignment_first_contacted_at is not null
    ) then
        return query select false, 'release_not_allowed', '본인의 미접촉 임시 배정만 해제할 수 있습니다.',
            v_assignment.id, v_uid, v_assignment.status,
            v_assignment.assigned_at, v_assignment.assignment_expires_at;
        return;
    end if;

    v_release_reason := coalesce(nullif(btrim(p_reason), ''), 'user_released');
    if v_release_reason = 'contact_results_return' and v_return_reason = '' then
        return query select false, 'return_reason_required', '반납사유를 입력해 주세요.',
            v_assignment.id, v_uid, v_assignment.status,
            v_assignment.assigned_at, v_assignment.assignment_expires_at;
        return;
    end if;

    update public.oasis_company_sales_assignments a
    set
        assigned_user_id = null,
        status = case
            when v_release_reason = 'contact_results_return' then 'long_hold'
            else 'unassigned'
        end,
        assigned_at = null,
        assignment_expires_at = null,
        next_contact_at = null,
        current_assignment_contact_count = 0,
        current_assignment_first_contacted_at = null,
        reactivate_at = null,
        legacy_hold = false,
        released_at = now(),
        released_reason = v_release_reason,
        last_status_changed_at = now()
    where a.id = v_assignment.id
    returning * into v_saved;

    update public.oasis_prospect_companies p
    set owner_user_id = '', updated_at = now()
    where p.company_uid = v_uid
      and p.owner_user_id = v_assignment.assigned_user_id;

    perform public.oasis_write_company_assignment_audit(
        p_current_user_id,
        v_saved.company_id,
        v_saved.company_uid,
        case when v_is_admin then 'admin_recall' else 'assignment_released' end,
        jsonb_build_object(
            'status', v_assignment.status,
            'assigned_user_id', v_assignment.assigned_user_id
        ),
        jsonb_build_object(
            'status', v_saved.status,
            'assigned_user_id', null,
            'reason', v_saved.released_reason,
            'return_reason', nullif(v_return_reason, '')
        ),
        p_session_id
    );

    return query select true, 'released', '배정을 해제했습니다.',
        v_saved.id, v_saved.company_uid, v_saved.status,
        v_saved.assigned_at, v_saved.assignment_expires_at;
end;
$$;

revoke all on function public.oasis_release_company_sales_assignment(
    text, text, text, text, text
) from public, anon, authenticated;
grant execute on function public.oasis_release_company_sales_assignment(
    text, text, text, text, text
) to service_role;

create or replace function public.oasis_admin_review_returned_companies_batch(
    p_current_user_id text,
    p_company_uids text[],
    p_action text,
    p_reason text,
    p_session_id text default null
)
returns table (
    success boolean,
    code text,
    message text,
    processed_count integer
)
language plpgsql
volatile
set search_path = public, pg_temp
as $$
declare
    v_uids text[];
    v_uid text;
    v_reason text := left(btrim(coalesce(p_reason, '')), 400);
    v_result record;
    v_processed integer := 0;
    v_matching integer := 0;
begin
    if not public.oasis_sales_actor_is_admin(p_current_user_id) then
        raise exception using errcode = '42501', message = 'OASIS_SALES_ADMIN_REQUIRED';
    end if;

    select array_agg(candidate.uid order by candidate.uid)
    into v_uids
    from (
        select distinct btrim(value) as uid
        from unnest(coalesce(p_company_uids, array[]::text[])) as item(value)
        where nullif(btrim(value), '') is not null
    ) candidate;

    if coalesce(cardinality(v_uids), 0) < 1
       or cardinality(v_uids) > 100 then
        return query select false, 'invalid_batch_size',
            '한 번에 1개 이상 100개 이하의 반납 DB를 선택해 주세요.', 0;
        return;
    end if;
    if p_action not in ('reactivate', 'permanent_exclude') then
        return query select false, 'invalid_action',
            '지원하지 않는 반납 DB 처리 방식입니다.', 0;
        return;
    end if;
    if v_reason = '' then
        return query select false, 'reason_required', '검토 사유를 입력해 주세요.', 0;
        return;
    end if;
    if exists (
        select 1 from unnest(v_uids) candidate(uid)
        where not public.oasis_is_valid_company_uid(candidate.uid)
    ) then
        return query select false, 'invalid_company_uid',
            '선택한 업체 식별정보를 확인할 수 없습니다.', 0;
        return;
    end if;

    select count(*)::integer
    into v_matching
    from public.oasis_company_sales_assignments a
    where a.company_uid = any(v_uids)
      and a.status = 'long_hold'
      and a.released_reason = 'contact_results_return'
      and coalesce(a.permanently_excluded, false) is false;

    if v_matching <> cardinality(v_uids) then
        return query select false, 'stale_selection',
            '선택한 반납 DB 상태가 변경되었습니다. 목록을 새로고침해 주세요.', 0;
        return;
    end if;

    foreach v_uid in array v_uids loop
        if p_action = 'reactivate' then
            select result.* into v_result
            from public.oasis_admin_reactivate_company_assignment(
                p_current_user_id,
                v_uid,
                '반납 검토 일괄 승인: ' || v_reason,
                p_session_id
            ) result;
        else
            select result.* into v_result
            from public.oasis_admin_permanent_exclude_company(
                p_current_user_id,
                v_uid,
                '반납 검토 일괄 영구 제외: ' || v_reason,
                p_session_id
            ) result;
        end if;

        if not coalesce(v_result.success, false) then
            raise exception using
                errcode = 'P0001',
                message = 'OASIS_BATCH_RETURN_REVIEW_FAILED';
        end if;
        v_processed := v_processed + 1;
    end loop;

    return query select true, 'batch_reviewed',
        '선택한 반납 DB를 일괄 처리했습니다.', v_processed;
end;
$$;

revoke all on function public.oasis_admin_review_returned_companies_batch(
    text, text[], text, text, text
) from public, anon, authenticated;
grant execute on function public.oasis_admin_review_returned_companies_batch(
    text, text[], text, text, text
) to service_role;

commit;
