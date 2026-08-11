begin;

-- A mobile DB request is fulfilled as soon as at least one company is assigned.
-- Close legacy partial requests so they do not block a new request.
update public.oasis_mobile_db_requests
set
    status = 'approved',
    decided_at = coalesce(decided_at, now()),
    updated_at = now()
where status in ('pending', 'partially_approved')
  and allocated_count > 0;

update public.oasis_mobile_db_requests
set
    status = 'pending',
    decided_by_user_id = null,
    decided_at = null,
    updated_at = now()
where status = 'partially_approved'
  and allocated_count = 0;

create or replace function public.oasis_admin_update_mobile_db_request(
    p_current_user_id text,
    p_request_id uuid,
    p_action text,
    p_allocated_count integer default 0,
    p_reason text default '',
    p_session_id text default null
)
returns table (
    success boolean,
    code text,
    message text,
    request_id uuid,
    status text,
    requested_count integer,
    allocated_count integer
)
language plpgsql
volatile
set search_path = public, pg_temp
as $$
declare
    v_action text := lower(btrim(coalesce(p_action, '')));
    v_reason text := left(btrim(coalesce(p_reason, '')), 500);
    v_request public.oasis_mobile_db_requests%rowtype;
    v_saved public.oasis_mobile_db_requests%rowtype;
    v_add_count integer := greatest(0, coalesce(p_allocated_count, 0));
begin
    if not public.oasis_sales_actor_is_admin(p_current_user_id) then
        raise exception using
            errcode = '42501',
            message = 'OASIS_SALES_ADMIN_REQUIRED';
    end if;
    if p_request_id is null or v_action not in ('allocate', 'reject') then
        return query select
            false, 'invalid_input', '처리할 신청과 작업을 확인해 주세요.',
            p_request_id, null::text, 0, 0;
        return;
    end if;

    perform pg_advisory_xact_lock(
        hashtextextended('oasis-mobile-request:' || p_request_id::text, 0)
    );
    select r.* into v_request
    from public.oasis_mobile_db_requests r
    where r.id = p_request_id
    for update;

    if v_request.id is null then
        return query select
            false, 'not_found', '핸드폰 DB 신청을 찾을 수 없습니다.',
            p_request_id, null::text, 0, 0;
        return;
    end if;
    if v_request.status <> 'pending' then
        return query select
            false, 'already_resolved', '이미 처리가 끝난 신청입니다.',
            v_request.id, v_request.status,
            v_request.requested_count, v_request.allocated_count;
        return;
    end if;

    if v_action = 'reject' then
        if v_request.allocated_count > 0 then
            return query select
                false, 'allocation_exists',
                '이미 배정된 신청은 거절할 수 없습니다.',
                v_request.id, v_request.status,
                v_request.requested_count, v_request.allocated_count;
            return;
        end if;
        update public.oasis_mobile_db_requests r
        set
            status = 'rejected',
            decision_reason = v_reason,
            decided_by_user_id = lower(btrim(p_current_user_id)),
            decided_at = now(),
            updated_at = now()
        where r.id = v_request.id
        returning * into v_saved;
        insert into public.oasis_mobile_db_request_events (
            request_id, actor_user_id, action, reason
        ) values (
            v_saved.id, lower(btrim(p_current_user_id)), 'rejected', v_reason
        );
        return query select
            true, 'rejected', '핸드폰 DB 신청을 거절했습니다.',
            v_saved.id, v_saved.status,
            v_saved.requested_count, v_saved.allocated_count;
        return;
    end if;

    if v_add_count < 1
       or v_request.allocated_count + v_add_count > v_request.requested_count then
        return query select
            false, 'invalid_allocation_count', '배정 수량을 확인해 주세요.',
            v_request.id, v_request.status,
            v_request.requested_count, v_request.allocated_count;
        return;
    end if;

    update public.oasis_mobile_db_requests r
    set
        allocated_count = r.allocated_count + v_add_count,
        status = 'approved',
        decision_reason = v_reason,
        decided_by_user_id = lower(btrim(p_current_user_id)),
        decided_at = now(),
        updated_at = now()
    where r.id = v_request.id
    returning * into v_saved;

    insert into public.oasis_mobile_db_request_events (
        request_id, actor_user_id, action, allocated_count, reason
    ) values (
        v_saved.id, lower(btrim(p_current_user_id)),
        'allocated', v_add_count,
        coalesce(nullif(v_reason, ''), left(coalesce(p_session_id, ''), 500))
    );

    return query select
        true, 'allocated',
        v_add_count::text || '개 핸드폰 DB를 배정하고 신청을 완료 처리했습니다.',
        v_saved.id, v_saved.status,
        v_saved.requested_count, v_saved.allocated_count;
end;
$$;

revoke all on function public.oasis_admin_update_mobile_db_request(
    text, uuid, text, integer, text, text
) from public, anon, authenticated;
grant execute on function public.oasis_admin_update_mobile_db_request(
    text, uuid, text, integer, text, text
) to service_role;

commit;
