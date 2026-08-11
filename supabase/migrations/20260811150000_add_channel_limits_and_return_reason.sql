begin;

-- The legacy setting guarded only uncontacted assignments. Keep it aligned
-- with the new overall capacity while the wrappers below enforce active DB
-- capacity atomically: 30 landline + 30 mobile, 60 total.
alter table public.oasis_sales_assignment_settings
    alter column max_uncontacted set default 60;

update public.oasis_sales_assignment_settings
set
    max_uncontacted = 60,
    updated_at = now()
where max_uncontacted = 30;

alter function public.oasis_claim_company_sales_assignment(
    text, uuid, text, text
) rename to oasis_claim_company_sales_assignment_base_v1130;

create function public.oasis_claim_company_sales_assignment(
    p_current_user_id text,
    p_company_id uuid,
    p_company_uid text,
    p_session_id text default null
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
    v_user_id text := lower(btrim(coalesce(p_current_user_id, '')));
    v_uid text;
    v_channel text := lower(btrim(coalesce(
        current_setting('oasis.allocation_channel', true),
        ''
    )));
    v_active_total integer := 0;
    v_active_channel integer := 0;
    v_already_owned boolean := false;
begin
    if not public.oasis_sales_actor_is_active(v_user_id) then
        raise exception using
            errcode = '42501',
            message = 'OASIS_SALES_USER_NOT_AUTHORIZED';
    end if;

    perform public.oasis_release_expired_company_assignments(
        v_user_id,
        p_session_id
    );

    v_uid := public.oasis_resolve_company_sales_uid(
        p_company_id,
        p_company_uid
    );
    if v_uid is null then
        return query select
            false, 'invalid_company_uid',
            '업체 공통 식별키를 생성할 수 없습니다.',
            null::uuid, null::text, null::text,
            null::timestamptz, null::timestamptz;
        return;
    end if;

    -- Match the original lock order (company, then user). The base function
    -- reacquires these transaction-scoped locks safely after the limit check.
    perform pg_advisory_xact_lock(
        hashtextextended('oasis-company:' || v_uid, 0)
    );
    perform pg_advisory_xact_lock(
        hashtextextended('oasis-user:' || v_user_id, 0)
    );

    select exists (
        select 1
        from public.oasis_company_sales_assignments a
        where a.company_uid = v_uid
          and a.assigned_user_id = v_user_id
          and a.released_at is null
          and coalesce(a.permanently_excluded, false) is false
          and coalesce(a.status, '') not in (
              'unassigned', 'long_hold', 'permanently_excluded'
          )
          and (
              a.assignment_expires_at is null
              or a.assignment_expires_at > now()
          )
    ) into v_already_owned;

    if not v_already_owned then
        select count(*)::integer
        into v_active_total
        from public.oasis_company_sales_assignments a
        where a.assigned_user_id = v_user_id
          and a.released_at is null
          and coalesce(a.permanently_excluded, false) is false
          and coalesce(a.status, '') not in (
              'unassigned', 'long_hold', 'permanently_excluded'
          )
          and (
              a.assignment_expires_at is null
              or a.assignment_expires_at > now()
          );

        if v_active_total >= 60 then
            return query select
                false, 'total_db_limit_reached',
                '일반전화 30개와 핸드폰번호 DB 30개를 합쳐 최대 60개까지 보유할 수 있습니다.',
                null::uuid, v_uid, null::text,
                null::timestamptz, null::timestamptz;
            return;
        end if;

        if v_channel in ('landline', 'mobile') then
            select count(*)::integer
            into v_active_channel
            from public.oasis_company_sales_assignments a
            left join lateral (
                select p.source_data
                from public.oasis_prospect_companies p
                where p.company_uid = a.company_uid
                order by
                    (p.id = a.company_id) desc,
                    p.updated_at desc nulls last,
                    p.id
                limit 1
            ) company on true
            where a.assigned_user_id = v_user_id
              and a.released_at is null
              and coalesce(a.permanently_excluded, false) is false
              and coalesce(a.status, '') not in (
                  'unassigned', 'long_hold', 'permanently_excluded'
              )
              and (
                  a.assignment_expires_at is null
                  or a.assignment_expires_at > now()
              )
              and lower(btrim(coalesce(
                  company.source_data ->> 'allocation_channel',
                  ''
              ))) = v_channel;

            if v_active_channel >= 30 then
                return query select
                    false,
                    case
                        when v_channel = 'mobile'
                            then 'mobile_limit_reached'
                        else 'landline_limit_reached'
                    end,
                    case
                        when v_channel = 'mobile'
                            then '핸드폰번호 DB는 최대 30개까지 보유할 수 있습니다.'
                        else '일반전화 DB는 최대 30개까지 보유할 수 있습니다.'
                    end,
                    null::uuid, v_uid, null::text,
                    null::timestamptz, null::timestamptz;
                return;
            end if;
        end if;
    end if;

    return query
    select *
    from public.oasis_claim_company_sales_assignment_base_v1130(
        v_user_id,
        p_company_id,
        v_uid,
        p_session_id
    );
end;
$$;

alter function public.oasis_claim_and_save_company_sales_assignment(
    text, text, jsonb, text
) rename to oasis_claim_and_save_company_sales_assignment_base_v1130;

create function public.oasis_claim_and_save_company_sales_assignment(
    p_current_user_id text,
    p_company_uid text,
    p_company_payload jsonb,
    p_session_id text default null
)
returns table (
    success boolean,
    code text,
    message text,
    assignment_id uuid,
    company_uid text,
    status text,
    assigned_at timestamptz,
    assignment_expires_at timestamptz,
    prospect_id uuid
)
language plpgsql
volatile
set search_path = public, pg_temp
as $$
declare
    v_payload jsonb := case
        when jsonb_typeof(coalesce(p_company_payload, '{}'::jsonb)) = 'object'
            then coalesce(p_company_payload, '{}'::jsonb)
        else '{}'::jsonb
    end;
    v_channel text;
begin
    v_channel := lower(btrim(coalesce(
        v_payload -> 'source_data' ->> 'allocation_channel',
        ''
    )));
    perform set_config('oasis.allocation_channel', v_channel, true);

    return query
    select *
    from public.oasis_claim_and_save_company_sales_assignment_base_v1130(
        p_current_user_id,
        p_company_uid,
        v_payload,
        p_session_id
    );
end;
$$;

revoke all on function public.oasis_claim_company_sales_assignment_base_v1130(
    text, uuid, text, text
) from public, anon, authenticated;
revoke all on function public.oasis_claim_company_sales_assignment(
    text, uuid, text, text
) from public, anon, authenticated;
revoke all on function public.oasis_claim_and_save_company_sales_assignment_base_v1130(
    text, text, jsonb, text
) from public, anon, authenticated;
revoke all on function public.oasis_claim_and_save_company_sales_assignment(
    text, text, jsonb, text
) from public, anon, authenticated;

grant execute on function public.oasis_claim_company_sales_assignment(
    text, uuid, text, text
) to service_role;
grant execute on function public.oasis_claim_and_save_company_sales_assignment(
    text, text, jsonb, text
) to service_role;

revoke all on function public.oasis_release_company_sales_assignment(
    text, text, text, text
) from public, anon, authenticated;
drop function public.oasis_release_company_sales_assignment(
    text, text, text, text
);

create function public.oasis_release_company_sales_assignment(
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
    if not v_is_admin
       and v_release_reason = 'contact_results_return'
       and v_return_reason = '' then
        return query select false, 'return_reason_required', '반납사유를 입력해 주세요.',
            v_assignment.id, v_uid, v_assignment.status,
            v_assignment.assigned_at, v_assignment.assignment_expires_at;
        return;
    end if;

    update public.oasis_company_sales_assignments a
    set
        assigned_user_id = null,
        status = case
            when not v_is_admin and v_release_reason = 'contact_results_return'
                then 'long_hold'
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

commit;
