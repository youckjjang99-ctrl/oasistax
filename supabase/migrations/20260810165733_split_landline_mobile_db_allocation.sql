-- 일반번호 즉시배정과 핸드폰 DB 관리자 승인요청을 분리한다.
-- 요청 테이블에는 전화번호나 업체정보를 저장하지 않는다.

create table if not exists public.oasis_mobile_db_requests (
    id uuid primary key default extensions.gen_random_uuid(),
    requested_user_id text not null,
    region text not null,
    district text not null default '',
    business_type text not null default 'all',
    requested_count integer not null default 30,
    allocated_count integer not null default 0,
    status text not null default 'pending',
    decision_reason text not null default '',
    decided_by_user_id text,
    requested_at timestamptz not null default now(),
    decided_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint oasis_mobile_db_requests_business_type_check
        check (business_type in ('all', 'stock', 'individual')),
    constraint oasis_mobile_db_requests_count_check
        check (requested_count between 1 and 100),
    constraint oasis_mobile_db_requests_allocated_check
        check (allocated_count between 0 and requested_count),
    constraint oasis_mobile_db_requests_status_check
        check (status in (
            'pending', 'partially_approved', 'approved',
            'rejected', 'cancelled'
        ))
);

create unique index if not exists idx_oasis_mobile_db_requests_one_open
    on public.oasis_mobile_db_requests (requested_user_id)
    where status in ('pending', 'partially_approved');

create index if not exists idx_oasis_mobile_db_requests_admin_queue
    on public.oasis_mobile_db_requests (status, requested_at, id);

create table if not exists public.oasis_mobile_db_request_events (
    id uuid primary key default extensions.gen_random_uuid(),
    request_id uuid not null
        references public.oasis_mobile_db_requests(id) on delete cascade,
    actor_user_id text not null,
    action text not null,
    allocated_count integer not null default 0,
    reason text not null default '',
    created_at timestamptz not null default now(),
    constraint oasis_mobile_db_request_events_action_check
        check (action in ('requested', 'allocated', 'rejected', 'cancelled')),
    constraint oasis_mobile_db_request_events_count_check
        check (allocated_count between 0 and 100)
);

create index if not exists idx_oasis_mobile_db_request_events_request
    on public.oasis_mobile_db_request_events (request_id, created_at, id);

alter table public.oasis_mobile_db_requests enable row level security;
alter table public.oasis_mobile_db_request_events enable row level security;

revoke all on table public.oasis_mobile_db_requests
    from public, anon, authenticated;
revoke all on table public.oasis_mobile_db_request_events
    from public, anon, authenticated;
grant select, insert, update on table public.oasis_mobile_db_requests
    to service_role;
grant select, insert on table public.oasis_mobile_db_request_events
    to service_role;

create or replace function public.oasis_submit_mobile_db_request(
    p_current_user_id text,
    p_region text,
    p_district text default '',
    p_business_type text default 'all',
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
set search_path = public, extensions, pg_temp
as $$
declare
    v_user_id text := lower(btrim(coalesce(p_current_user_id, '')));
    v_region text := btrim(coalesce(p_region, ''));
    v_district text := btrim(coalesce(p_district, ''));
    v_business_type text := lower(btrim(coalesce(p_business_type, 'all')));
    v_existing public.oasis_mobile_db_requests%rowtype;
    v_saved public.oasis_mobile_db_requests%rowtype;
begin
    if not public.oasis_sales_actor_is_active(v_user_id) then
        raise exception using
            errcode = '42501',
            message = 'OASIS_SALES_USER_NOT_AUTHORIZED';
    end if;
    if length(v_region) not between 1 and 100
       or length(v_district) > 100
       or v_business_type not in ('all', 'stock', 'individual') then
        return query select
            false, 'invalid_input', '지역과 사업자 유형을 확인해 주세요.',
            null::uuid, null::text, 30, 0;
        return;
    end if;

    perform pg_advisory_xact_lock(
        hashtextextended('oasis-mobile-request:' || v_user_id, 0)
    );
    select r.* into v_existing
    from public.oasis_mobile_db_requests r
    where r.requested_user_id = v_user_id
      and r.status in ('pending', 'partially_approved')
    order by r.requested_at desc, r.id desc
    limit 1
    for update;

    if v_existing.id is not null then
        return query select
            true, 'already_requested',
            '이미 처리 중인 핸드폰 DB 신청이 있습니다.',
            v_existing.id, v_existing.status,
            v_existing.requested_count, v_existing.allocated_count;
        return;
    end if;

    insert into public.oasis_mobile_db_requests (
        requested_user_id, region, district, business_type
    ) values (
        v_user_id, v_region, v_district, v_business_type
    ) returning * into v_saved;

    insert into public.oasis_mobile_db_request_events (
        request_id, actor_user_id, action, reason
    ) values (
        v_saved.id, v_user_id, 'requested',
        left(coalesce(p_session_id, ''), 200)
    );

    return query select
        true, 'requested', '핸드폰 DB 배정 신청을 접수했습니다.',
        v_saved.id, v_saved.status,
        v_saved.requested_count, v_saved.allocated_count;
end;
$$;

create or replace function public.oasis_list_user_mobile_db_requests(
    p_current_user_id text,
    p_limit integer default 20
)
returns table (
    request_id uuid,
    requested_user_id text,
    region text,
    district text,
    business_type text,
    requested_count integer,
    allocated_count integer,
    status text,
    decision_reason text,
    requested_at timestamptz,
    decided_at timestamptz
)
language plpgsql
stable
set search_path = public, pg_temp
as $$
begin
    if not public.oasis_sales_actor_is_active(p_current_user_id) then
        raise exception using
            errcode = '42501',
            message = 'OASIS_SALES_USER_NOT_AUTHORIZED';
    end if;
    return query
    select
        r.id, r.requested_user_id, r.region, r.district,
        r.business_type, r.requested_count, r.allocated_count,
        r.status, r.decision_reason, r.requested_at, r.decided_at
    from public.oasis_mobile_db_requests r
    where r.requested_user_id = lower(btrim(p_current_user_id))
    order by r.requested_at desc, r.id desc
    limit greatest(1, least(coalesce(p_limit, 20), 100));
end;
$$;

create or replace function public.oasis_list_admin_mobile_db_requests(
    p_current_user_id text,
    p_statuses text[] default null,
    p_limit integer default 200
)
returns table (
    request_id uuid,
    requested_user_id text,
    requested_user_name text,
    region text,
    district text,
    business_type text,
    requested_count integer,
    allocated_count integer,
    status text,
    decision_reason text,
    requested_at timestamptz,
    decided_at timestamptz
)
language plpgsql
stable
set search_path = public, pg_temp
as $$
begin
    if not public.oasis_sales_actor_is_admin(p_current_user_id) then
        raise exception using
            errcode = '42501',
            message = 'OASIS_SALES_ADMIN_REQUIRED';
    end if;
    return query
    select
        r.id, r.requested_user_id, coalesce(u.name, r.requested_user_id),
        r.region, r.district, r.business_type,
        r.requested_count, r.allocated_count, r.status,
        r.decision_reason, r.requested_at, r.decided_at
    from public.oasis_mobile_db_requests r
    left join public.oasis_users u
      on lower(u.user_id) = lower(r.requested_user_id)
    where coalesce(cardinality(p_statuses), 0) = 0
       or r.status = any(p_statuses)
    order by
        case when r.status in ('pending', 'partially_approved') then 0 else 1 end,
        r.requested_at asc,
        r.id
    limit greatest(1, least(coalesce(p_limit, 200), 1000));
end;
$$;

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
    if v_request.status not in ('pending', 'partially_approved') then
        return query select
            false, 'already_resolved', '이미 처리가 끝난 신청입니다.',
            v_request.id, v_request.status,
            v_request.requested_count, v_request.allocated_count;
        return;
    end if;

    if v_action = 'reject' then
        if v_request.allocated_count > 0 then
            return query select
                false, 'partial_allocation_exists',
                '이미 일부 배정된 신청은 거절할 수 없습니다.',
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
        status = case
            when r.allocated_count + v_add_count >= r.requested_count
                then 'approved'
            else 'partially_approved'
        end,
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
        v_add_count::text || '개 핸드폰 DB 배정을 기록했습니다.',
        v_saved.id, v_saved.status,
        v_saved.requested_count, v_saved.allocated_count;
end;
$$;

revoke execute on function public.oasis_submit_mobile_db_request(
    text, text, text, text, text
) from public, anon, authenticated;
revoke execute on function public.oasis_list_user_mobile_db_requests(
    text, integer
) from public, anon, authenticated;
revoke execute on function public.oasis_list_admin_mobile_db_requests(
    text, text[], integer
) from public, anon, authenticated;
revoke execute on function public.oasis_admin_update_mobile_db_request(
    text, uuid, text, integer, text, text
) from public, anon, authenticated;

grant execute on function public.oasis_submit_mobile_db_request(
    text, text, text, text, text
) to service_role;
grant execute on function public.oasis_list_user_mobile_db_requests(
    text, integer
) to service_role;
grant execute on function public.oasis_list_admin_mobile_db_requests(
    text, text[], integer
) to service_role;
grant execute on function public.oasis_admin_update_mobile_db_request(
    text, uuid, text, integer, text, text
) to service_role;
