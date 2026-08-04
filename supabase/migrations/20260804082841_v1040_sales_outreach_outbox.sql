-- OASIS CRM v10.4.0
-- Supabase migration history version: 20260804082841.
-- Durable, metadata-only reservation ledger for free-form sales outreach.
--
-- Deliberately excluded: recipient values, company names, subject/body text,
-- provider responses/IDs, recordings, evidence files, and evidence paths.

create table if not exists public.oasis_prospect_outreach_outbox (
    id uuid primary key default gen_random_uuid(),
    requested_by_user_id text not null,
    request_id text not null,
    content_hmac text not null,
    recipient_hmac text not null,
    recipient_phone_hash text,
    assignment_id uuid references public.oasis_company_sales_assignments(id)
        on delete set null,
    prospect_id uuid references public.oasis_prospect_companies(id)
        on delete set null,
    company_uid text not null,
    contact_id uuid references public.oasis_prospect_contacts(id)
        on delete set null,
    contact_updated_at timestamptz not null,
    channel text not null,
    status text not null default 'reserved',
    reservation_token uuid not null default gen_random_uuid(),
    safe_result_code text not null default '',
    reserved_at timestamptz not null default now(),
    dispatch_started_at timestamptz,
    finalized_at timestamptz,
    unknown_at timestamptz,
    reconciled_by_user_id text,
    reconciled_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint oasis_prospect_outreach_owner_request_unique
        unique (requested_by_user_id, request_id),
    constraint oasis_prospect_outreach_reservation_token_unique
        unique (reservation_token),
    constraint oasis_prospect_outreach_request_id_check
        check (request_id ~ '^[A-Za-z0-9._:-]{8,200}$'),
    constraint oasis_prospect_outreach_content_hmac_check
        check (content_hmac ~ '^[0-9a-f]{64}$'),
    constraint oasis_prospect_outreach_recipient_hmac_check
        check (recipient_hmac ~ '^[0-9a-f]{64}$'),
    constraint oasis_prospect_outreach_phone_hash_check
        check (
            recipient_phone_hash is null
            or recipient_phone_hash ~ '^[0-9a-f]{64}$'
        ),
    constraint oasis_prospect_outreach_company_uid_check
        check (company_uid ~ '^[A-Za-z0-9:_-]{1,180}$'),
    constraint oasis_prospect_outreach_channel_check
        check (channel in ('email', 'sms', 'kakao')),
    constraint oasis_prospect_outreach_status_check
        check (status in (
            'reserved', 'dispatching', 'provider_accepted',
            'provider_rejected', 'delivery_unknown',
            'confirmed_not_sent', 'cancelled_dnc',
            'cancelled_changed', 'cancelled_stale'
        )),
    constraint oasis_prospect_outreach_safe_code_check
        check (
            safe_result_code = ''
            or safe_result_code ~ '^[A-Z0-9_-]{1,80}$'
        ),
    constraint oasis_prospect_outreach_timestamps_check
        check (
            (status = 'reserved' and dispatch_started_at is null and finalized_at is null)
            or (status = 'dispatching' and dispatch_started_at is not null and finalized_at is null)
            or (status not in ('reserved', 'dispatching') and finalized_at is not null)
        )
);

create index if not exists idx_oasis_prospect_outreach_company_history
    on public.oasis_prospect_outreach_outbox (
        requested_by_user_id, company_uid, reserved_at desc
    );

create index if not exists idx_oasis_prospect_outreach_duplicate_guard
    on public.oasis_prospect_outreach_outbox (
        recipient_hmac, channel, content_hmac, status, reserved_at desc
    );

create index if not exists idx_oasis_prospect_outreach_open_dispatch
    on public.oasis_prospect_outreach_outbox (dispatch_started_at)
    where status = 'dispatching';


create or replace function public.oasis_reserve_prospect_outreach(
    p_current_user_id text,
    p_request_id text,
    p_content_hmac text,
    p_recipient_hmac text,
    p_assignment_id uuid,
    p_prospect_id uuid,
    p_company_uid text,
    p_contact_id uuid,
    p_contact_updated_at timestamptz,
    p_channel text,
    p_recipient_phone_hash text default ''
)
returns table (
    success boolean,
    code text,
    message text,
    outbox_id uuid,
    status text,
    acquired boolean,
    reservation_token uuid,
    reserved_at timestamptz
)
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
    v_actor text := lower(btrim(coalesce(p_current_user_id, '')));
    v_request text := btrim(coalesce(p_request_id, ''));
    v_content_hmac text := lower(btrim(coalesce(p_content_hmac, '')));
    v_recipient_hmac text := lower(btrim(coalesce(p_recipient_hmac, '')));
    v_uid text := btrim(coalesce(p_company_uid, ''));
    v_channel text := lower(btrim(coalesce(p_channel, '')));
    v_phone_hash text := lower(btrim(coalesce(p_recipient_phone_hash, '')));
    v_assignment public.oasis_company_sales_assignments%rowtype;
    v_contact public.oasis_prospect_contacts%rowtype;
    v_existing public.oasis_prospect_outreach_outbox%rowtype;
    v_saved public.oasis_prospect_outreach_outbox%rowtype;
begin
    if not public.oasis_sales_actor_is_active(v_actor) then
        raise exception using errcode = '42501', message = 'PERMISSION_DENIED';
    end if;
    if v_request !~ '^[A-Za-z0-9._:-]{8,200}$'
       or v_content_hmac !~ '^[0-9a-f]{64}$'
       or v_recipient_hmac !~ '^[0-9a-f]{64}$'
       or v_uid !~ '^[A-Za-z0-9:_-]{1,180}$'
       or v_channel not in ('email', 'sms', 'kakao')
       or p_assignment_id is null
       or p_prospect_id is null
       or p_contact_id is null
       or p_contact_updated_at is null
       or (
            v_channel in ('sms', 'kakao')
            and v_phone_hash !~ '^[0-9a-f]{64}$'
       )
       or (v_channel = 'email' and v_phone_hash <> '') then
        return query select false, 'INVALID_REQUEST',
            '발송 요청값을 다시 확인해 주세요.',
            null::uuid, null::text, false, null::uuid, null::timestamptz;
        return;
    end if;

    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended('oasis-outreach-request:' || v_actor || ':' || v_request, 0)
    );

    select o.* into v_existing
    from public.oasis_prospect_outreach_outbox o
    where o.requested_by_user_id = v_actor
      and o.request_id = v_request
    for update;

    if v_existing.id is not null then
        if v_existing.content_hmac is distinct from v_content_hmac
           or v_existing.recipient_hmac is distinct from v_recipient_hmac
           or v_existing.recipient_phone_hash is distinct from
                nullif(v_phone_hash, '')
           or v_existing.assignment_id is distinct from p_assignment_id
           or v_existing.prospect_id is distinct from p_prospect_id
           or v_existing.company_uid is distinct from v_uid
           or v_existing.contact_id is distinct from p_contact_id
           or v_existing.contact_updated_at is distinct from p_contact_updated_at
           or v_existing.channel is distinct from v_channel then
            return query select false, 'IDEMPOTENCY_CONFLICT',
                '같은 요청번호가 다른 발송 요청에 사용되어 중단했습니다.',
                v_existing.id, v_existing.status, false, null::uuid,
                v_existing.reserved_at;
            return;
        end if;

        if v_existing.status = 'reserved'
           and v_existing.reserved_at < now() - interval '10 minutes' then
            update public.oasis_prospect_outreach_outbox o
            set status = 'cancelled_stale',
                safe_result_code = 'RESERVATION_EXPIRED',
                finalized_at = now(),
                updated_at = now()
            where o.id = v_existing.id
            returning o.* into v_existing;
        elsif v_existing.status = 'dispatching'
           and v_existing.dispatch_started_at < now() - interval '10 minutes' then
            update public.oasis_prospect_outreach_outbox o
            set status = 'delivery_unknown',
                safe_result_code = 'STALE_DISPATCH',
                finalized_at = now(),
                unknown_at = now(),
                updated_at = now()
            where o.id = v_existing.id
            returning o.* into v_existing;
        end if;

        return query select true,
            case v_existing.status
                when 'reserved' then 'ALREADY_RESERVED'
                when 'dispatching' then 'ALREADY_DISPATCHING'
                when 'provider_accepted' then 'ALREADY_ACCEPTED'
                when 'provider_rejected' then 'ALREADY_REJECTED'
                when 'delivery_unknown' then 'DELIVERY_UNKNOWN'
                else upper(v_existing.status)
            end,
            '이미 접수된 요청입니다. 중복 발송하지 않았습니다.',
            v_existing.id, v_existing.status, false, null::uuid,
            v_existing.reserved_at;
        return;
    end if;

    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            'oasis-outreach-recipient:' || v_recipient_hmac || ':' || v_channel,
            0
        )
    );
    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended('oasis-outreach-company:' || v_uid || ':' || v_channel, 0)
    );

    select a.* into v_assignment
    from public.oasis_company_sales_assignments a
    where a.id = p_assignment_id
      and a.company_uid = v_uid
    for update;

    if v_assignment.id is null
       or v_assignment.assigned_user_id is distinct from v_actor
       or v_assignment.status not in (
            'assigned', 'pending_contact', 'contacted', 'consulting', 'follow_up'
       )
       or coalesce(v_assignment.permanently_excluded, false)
       or v_assignment.released_at is not null
       or (
            v_assignment.assignment_expires_at is not null
            and v_assignment.assignment_expires_at <= now()
       ) then
        return query select false, 'ASSIGNMENT_CHANGED',
            '업체 배정 상태가 변경되어 발송을 중단했습니다.',
            null::uuid, null::text, false, null::uuid, null::timestamptz;
        return;
    end if;

    if not exists (
        select 1
        from public.oasis_prospect_companies p
        where p.id = p_prospect_id
          and p.company_uid = v_uid
          and p.owner_user_id = v_actor
    ) then
        return query select false, 'TARGET_NOT_OWNED',
            '현재 담당 중인 영업후보가 아닙니다.',
            null::uuid, null::text, false, null::uuid, null::timestamptz;
        return;
    end if;

    select c.* into v_contact
    from public.oasis_prospect_contacts c
    where c.id = p_contact_id
      and c.prospect_id = p_prospect_id
    for update;

    if v_contact.id is null
       or v_contact.updated_at is distinct from p_contact_updated_at
       or coalesce(v_contact.do_not_contact, false)
       or v_contact.opt_out_at is not null
       or lower(coalesce(v_contact.verification_status, '')) = 'rejected'
       or (
            v_channel = 'email'
            and lower(coalesce(v_contact.contact_type, '')) <> 'email'
       )
       or (
            v_channel in ('sms', 'kakao')
            and lower(coalesce(v_contact.contact_type, '')) not in (
                'phone', 'mobile', 'mobile_phone'
            )
       ) then
        return query select false, 'CONTACT_CHANGED',
            '발송 연락처가 변경되었거나 사용할 수 없습니다.',
            null::uuid, null::text, false, null::uuid, null::timestamptz;
        return;
    end if;

    if exists (
        select 1
        from public.oasis_prospect_contacts c
        join public.oasis_prospect_companies p on p.id = c.prospect_id
        where p.company_uid = v_uid
          and (coalesce(c.do_not_contact, false) or c.opt_out_at is not null)
    ) or exists (
        select 1
        from public.oasis_company_kakao_contact_controls c
        where c.status in ('opted_out', 'admin_blocked')
          and (
              c.company_uid = v_uid
              or (v_phone_hash <> '' and c.recipient_phone_hash = v_phone_hash)
          )
    ) then
        return query select false, 'DO_NOT_CONTACT',
            '수신거부 또는 연락제외 업체라 발송할 수 없습니다.',
            null::uuid, null::text, false, null::uuid, null::timestamptz;
        return;
    end if;

    -- No automatic retry: close every expired dispatch globally as unknown.
    update public.oasis_prospect_outreach_outbox o
    set status = 'delivery_unknown',
        safe_result_code = 'STALE_DISPATCH',
        finalized_at = now(),
        unknown_at = now(),
        updated_at = now()
    where o.status = 'dispatching'
      and o.dispatch_started_at < now() - interval '10 minutes';

    update public.oasis_prospect_outreach_outbox o
    set status = 'cancelled_stale',
        safe_result_code = 'RESERVATION_EXPIRED',
        finalized_at = now(),
        updated_at = now()
    where o.recipient_hmac = v_recipient_hmac
      and o.channel = v_channel
      and o.status = 'reserved'
      and o.reserved_at < now() - interval '10 minutes';

    if exists (
        select 1
        from public.oasis_prospect_outreach_outbox o
        where o.recipient_hmac = v_recipient_hmac
          and o.channel = v_channel
          and (
              o.status in ('reserved', 'dispatching')
              or (
                  o.status = 'provider_accepted'
                  and (
                      o.finalized_at > now() - interval '10 minutes'
                      or (
                          o.content_hmac = v_content_hmac
                          and o.finalized_at > now() - interval '24 hours'
                      )
                  )
              )
              or o.status = 'delivery_unknown'
          )
    ) then
        return query select false, 'DUPLICATE_OUTREACH',
            '같은 연락처와 채널의 요청이 처리 중이거나 최근 처리되었습니다.',
            null::uuid, null::text, false, null::uuid, null::timestamptz;
        return;
    end if;

    insert into public.oasis_prospect_outreach_outbox (
        requested_by_user_id, request_id, content_hmac,
        recipient_hmac, recipient_phone_hash,
        assignment_id, prospect_id, company_uid, contact_id,
        contact_updated_at, channel
    ) values (
        v_actor, v_request, v_content_hmac,
        v_recipient_hmac, nullif(v_phone_hash, ''),
        p_assignment_id, p_prospect_id, v_uid, p_contact_id,
        p_contact_updated_at, v_channel
    ) returning * into v_saved;

    return query select true, 'RESERVED',
        '발송 요청을 안전하게 예약했습니다.',
        v_saved.id, v_saved.status, true, v_saved.reservation_token,
        v_saved.reserved_at;
exception
    when unique_violation then
        return query select false, 'DUPLICATE_OUTREACH',
            '동일한 발송 요청이 이미 존재합니다.',
            null::uuid, null::text, false, null::uuid, null::timestamptz;
end;
$$;


create or replace function public.oasis_begin_prospect_outreach_dispatch(
    p_current_user_id text,
    p_outbox_id uuid,
    p_reservation_token uuid,
    p_recipient_hmac text,
    p_recipient_phone_hash text default ''
)
returns table (
    success boolean,
    code text,
    message text,
    outbox_id uuid,
    status text,
    dispatch_started boolean
)
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
    v_actor text := lower(btrim(coalesce(p_current_user_id, '')));
    v_recipient_hmac text := lower(btrim(coalesce(p_recipient_hmac, '')));
    v_phone_hash text := lower(btrim(coalesce(p_recipient_phone_hash, '')));
    v_outbox public.oasis_prospect_outreach_outbox%rowtype;
    v_assignment public.oasis_company_sales_assignments%rowtype;
    v_contact public.oasis_prospect_contacts%rowtype;
begin
    if not public.oasis_sales_actor_is_active(v_actor) then
        raise exception using errcode = '42501', message = 'PERMISSION_DENIED';
    end if;
    if p_outbox_id is null
       or p_reservation_token is null
       or v_recipient_hmac !~ '^[0-9a-f]{64}$' then
        return query select false, 'INVALID_REQUEST',
            '발송 예약정보를 확인할 수 없습니다.',
            p_outbox_id, null::text, false;
        return;
    end if;

    select o.* into v_outbox
    from public.oasis_prospect_outreach_outbox o
    where o.id = p_outbox_id
      and o.requested_by_user_id = v_actor
      and o.reservation_token = p_reservation_token
    for update;

    if v_outbox.id is null then
        return query select false, 'RESERVATION_NOT_FOUND',
            '발송 예약정보가 없거나 권한이 없습니다.',
            p_outbox_id, null::text, false;
        return;
    end if;
    if v_outbox.status <> 'reserved' then
        return query select false,
            case v_outbox.status
                when 'dispatching' then 'ALREADY_DISPATCHING'
                when 'provider_accepted' then 'ALREADY_ACCEPTED'
                when 'delivery_unknown' then 'DELIVERY_UNKNOWN'
                else upper(v_outbox.status)
            end,
            '이미 처리된 요청이라 다시 발송하지 않았습니다.',
            v_outbox.id, v_outbox.status, false;
        return;
    end if;
    if v_outbox.reserved_at < now() - interval '10 minutes' then
        update public.oasis_prospect_outreach_outbox o
        set status = 'cancelled_stale',
            safe_result_code = 'RESERVATION_EXPIRED',
            finalized_at = now(),
            updated_at = now()
        where o.id = v_outbox.id;
        return query select false, 'RESERVATION_EXPIRED',
            '발송 예약 시간이 지나 새 요청이 필요합니다.',
            v_outbox.id, 'cancelled_stale', false;
        return;
    end if;
    if v_outbox.channel in ('sms', 'kakao')
       and v_phone_hash !~ '^[0-9a-f]{64}$' then
        return query select false, 'INVALID_REQUEST',
            '수신거부 확인정보를 확인할 수 없습니다.',
            v_outbox.id, v_outbox.status, false;
        return;
    end if;
    if v_outbox.recipient_hmac is distinct from v_recipient_hmac
       or v_outbox.recipient_phone_hash is distinct from
            nullif(v_phone_hash, '') then
        return query select false, 'RECIPIENT_BINDING_CHANGED',
            '발송 수신처 결속정보가 변경되어 중단했습니다.',
            v_outbox.id, v_outbox.status, false;
        return;
    end if;

    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            'oasis-outreach-company:' || v_outbox.company_uid || ':' || v_outbox.channel,
            0
        )
    );

    select a.* into v_assignment
    from public.oasis_company_sales_assignments a
    where a.id = v_outbox.assignment_id
      and a.company_uid = v_outbox.company_uid
    for update;

    select c.* into v_contact
    from public.oasis_prospect_contacts c
    where c.id = v_outbox.contact_id
      and c.prospect_id = v_outbox.prospect_id
    for update;

    if v_assignment.id is null
       or v_assignment.assigned_user_id is distinct from v_actor
       or v_assignment.status not in (
            'assigned', 'pending_contact', 'contacted', 'consulting', 'follow_up'
       )
       or coalesce(v_assignment.permanently_excluded, false)
       or v_assignment.released_at is not null
       or (
            v_assignment.assignment_expires_at is not null
            and v_assignment.assignment_expires_at <= now()
       )
       or v_contact.id is null
       or v_contact.updated_at is distinct from v_outbox.contact_updated_at
       or lower(coalesce(v_contact.verification_status, '')) = 'rejected'
       or (
            v_outbox.channel = 'email'
            and lower(coalesce(v_contact.contact_type, '')) <> 'email'
       )
       or (
            v_outbox.channel in ('sms', 'kakao')
            and lower(coalesce(v_contact.contact_type, '')) not in (
                'phone', 'mobile', 'mobile_phone'
            )
       ) then
        update public.oasis_prospect_outreach_outbox o
        set status = 'cancelled_changed',
            safe_result_code = 'TARGET_CHANGED',
            finalized_at = now(),
            updated_at = now()
        where o.id = v_outbox.id;
        return query select false, 'TARGET_CHANGED',
            '배정 또는 연락처가 변경되어 발송을 취소했습니다.',
            v_outbox.id, 'cancelled_changed', false;
        return;
    end if;

    if coalesce(v_contact.do_not_contact, false)
       or v_contact.opt_out_at is not null
       or exists (
            select 1
            from public.oasis_prospect_contacts c
            join public.oasis_prospect_companies p on p.id = c.prospect_id
            where p.company_uid = v_outbox.company_uid
              and (coalesce(c.do_not_contact, false) or c.opt_out_at is not null)
       )
       or exists (
            select 1
            from public.oasis_company_kakao_contact_controls c
            where c.status in ('opted_out', 'admin_blocked')
              and (
                  c.company_uid = v_outbox.company_uid
                  or (
                      v_phone_hash <> ''
                      and c.recipient_phone_hash = v_phone_hash
                  )
              )
       ) then
        update public.oasis_prospect_outreach_outbox o
        set status = 'cancelled_dnc',
            safe_result_code = 'DO_NOT_CONTACT',
            finalized_at = now(),
            updated_at = now()
        where o.id = v_outbox.id;
        return query select false, 'DNC_CANCELLED',
            '발송 직전 수신거부가 확인되어 자동 취소했습니다.',
            v_outbox.id, 'cancelled_dnc', false;
        return;
    end if;

    update public.oasis_prospect_outreach_outbox o
    set status = 'dispatching',
        dispatch_started_at = now(),
        updated_at = now()
    where o.id = v_outbox.id
    returning o.* into v_outbox;

    return query select true, 'DISPATCH_STARTED',
        '발송 직전 안전 확인을 완료했습니다.',
        v_outbox.id, v_outbox.status, true;
end;
$$;


create or replace function public.oasis_finalize_prospect_outreach(
    p_current_user_id text,
    p_outbox_id uuid,
    p_reservation_token uuid,
    p_status text,
    p_safe_result_code text default ''
)
returns table (
    success boolean,
    code text,
    message text,
    outbox_id uuid,
    status text,
    finalized_at timestamptz
)
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
    v_actor text := lower(btrim(coalesce(p_current_user_id, '')));
    v_status text := lower(btrim(coalesce(p_status, '')));
    v_code text := left(
        pg_catalog.regexp_replace(
            upper(coalesce(p_safe_result_code, '')),
            '[^A-Z0-9_-]', '_', 'g'
        ),
        80
    );
    v_outbox public.oasis_prospect_outreach_outbox%rowtype;
begin
    if not public.oasis_sales_actor_is_active(v_actor) then
        raise exception using errcode = '42501', message = 'PERMISSION_DENIED';
    end if;
    if v_status not in (
        'provider_accepted', 'provider_rejected', 'delivery_unknown'
    ) then
        return query select false, 'INVALID_STATUS',
            '발송 결과 상태를 확인할 수 없습니다.',
            p_outbox_id, null::text, null::timestamptz;
        return;
    end if;

    select o.* into v_outbox
    from public.oasis_prospect_outreach_outbox o
    where o.id = p_outbox_id
      and o.requested_by_user_id = v_actor
      and o.reservation_token = p_reservation_token
    for update;

    if v_outbox.id is null then
        return query select false, 'RESERVATION_NOT_FOUND',
            '발송 예약정보가 없거나 권한이 없습니다.',
            p_outbox_id, null::text, null::timestamptz;
        return;
    end if;
    if v_outbox.status = v_status then
        return query select true, 'IDEMPOTENT_FINALIZE',
            '이미 같은 결과로 처리되었습니다.',
            v_outbox.id, v_outbox.status, v_outbox.finalized_at;
        return;
    end if;
    if v_outbox.status <> 'dispatching' then
        return query select false, 'TERMINAL_STATE',
            '이미 종료되거나 취소된 요청이라 결과를 변경하지 않았습니다.',
            v_outbox.id, v_outbox.status, v_outbox.finalized_at;
        return;
    end if;

    update public.oasis_prospect_outreach_outbox o
    set status = v_status,
        safe_result_code = coalesce(nullif(v_code, ''),
            case v_status
                when 'provider_accepted' then 'PROVIDER_ACCEPTED'
                when 'provider_rejected' then 'PROVIDER_REJECTED'
                else 'DELIVERY_UNKNOWN'
            end
        ),
        finalized_at = now(),
        unknown_at = case
            when v_status = 'delivery_unknown' then now()
            else o.unknown_at
        end,
        updated_at = now()
    where o.id = v_outbox.id
    returning o.* into v_outbox;

    return query select true, 'FINALIZED',
        '발송 결과를 자동 이력에 저장했습니다.',
        v_outbox.id, v_outbox.status, v_outbox.finalized_at;
end;
$$;


create or replace function public.oasis_list_prospect_outreach_history(
    p_current_user_id text,
    p_company_uid text default '',
    p_limit integer default 100,
    p_offset integer default 0
)
returns table (
    outbox_id uuid,
    channel text,
    status text,
    safe_result_code text,
    reserved_at timestamptz,
    dispatch_started_at timestamptz,
    finalized_at timestamptz
)
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
    v_actor text := lower(btrim(coalesce(p_current_user_id, '')));
    v_uid text := btrim(coalesce(p_company_uid, ''));
begin
    if not public.oasis_sales_actor_is_active(v_actor) then
        raise exception using errcode = '42501', message = 'PERMISSION_DENIED';
    end if;
    -- Reconcile only to unknown; this never retries or reacquires a send.
    update public.oasis_prospect_outreach_outbox o
    set status = 'delivery_unknown',
        safe_result_code = 'STALE_DISPATCH',
        finalized_at = now(),
        unknown_at = now(),
        updated_at = now()
    where o.requested_by_user_id = v_actor
      and o.status = 'dispatching'
      and o.dispatch_started_at < now() - interval '10 minutes';

    return query
    select
        o.id,
        o.channel,
        o.status,
        o.safe_result_code,
        o.reserved_at,
        o.dispatch_started_at,
        o.finalized_at
    from public.oasis_prospect_outreach_outbox o
    where o.requested_by_user_id = v_actor
      and (v_uid = '' or o.company_uid = v_uid)
    order by o.reserved_at desc
    limit greatest(1, least(coalesce(p_limit, 100), 500))
    offset greatest(0, coalesce(p_offset, 0));
end;
$$;


create or replace function public.oasis_admin_reconcile_prospect_outreach(
    p_current_user_id text,
    p_outbox_id uuid,
    p_outcome text,
    p_safe_result_code text default ''
)
returns table (
    success boolean,
    code text,
    message text,
    outbox_id uuid,
    status text,
    finalized_at timestamptz
)
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
    v_actor text := lower(btrim(coalesce(p_current_user_id, '')));
    v_outcome text := lower(btrim(coalesce(p_outcome, '')));
    v_code text := left(
        pg_catalog.regexp_replace(
            upper(coalesce(p_safe_result_code, '')),
            '[^A-Z0-9_-]', '_', 'g'
        ),
        80
    );
    v_outbox public.oasis_prospect_outreach_outbox%rowtype;
begin
    if not public.oasis_sales_actor_is_admin(v_actor) then
        raise exception using errcode = '42501', message = 'PERMISSION_DENIED';
    end if;
    if v_outcome not in ('provider_accepted', 'confirmed_not_sent') then
        return query select false, 'INVALID_STATUS',
            '관리자 확인 결과를 확인할 수 없습니다.',
            p_outbox_id, null::text, null::timestamptz;
        return;
    end if;

    select o.* into v_outbox
    from public.oasis_prospect_outreach_outbox o
    where o.id = p_outbox_id
    for update;
    if v_outbox.id is null then
        return query select false, 'OUTBOX_NOT_FOUND',
            '확인할 발송 이력이 없습니다.',
            p_outbox_id, null::text, null::timestamptz;
        return;
    end if;
    if v_outbox.status <> 'delivery_unknown' then
        return query select false, 'TERMINAL_STATE',
            '접수 여부 확인 필요 상태만 관리자 확인으로 종료할 수 있습니다.',
            v_outbox.id, v_outbox.status, v_outbox.finalized_at;
        return;
    end if;

    update public.oasis_prospect_outreach_outbox o
    set status = v_outcome,
        safe_result_code = coalesce(
            nullif(v_code, ''),
            case v_outcome
                when 'provider_accepted' then 'ADMIN_CONFIRMED_ACCEPTED'
                else 'ADMIN_CONFIRMED_NOT_SENT'
            end
        ),
        reconciled_by_user_id = v_actor,
        reconciled_at = now(),
        updated_at = now()
    where o.id = v_outbox.id
    returning o.* into v_outbox;

    return query select true, 'RECONCILED',
        '관리자 확인 결과를 저장했습니다.',
        v_outbox.id, v_outbox.status, v_outbox.finalized_at;
end;
$$;


alter table public.oasis_prospect_outreach_outbox enable row level security;
alter table public.oasis_prospect_outreach_outbox force row level security;

revoke all on table public.oasis_prospect_outreach_outbox
    from PUBLIC, anon, authenticated, service_role;

revoke all on function public.oasis_reserve_prospect_outreach(
    text, text, text, text, uuid, uuid, text, uuid, timestamptz, text, text
) from PUBLIC, anon, authenticated;
revoke all on function public.oasis_begin_prospect_outreach_dispatch(
    text, uuid, uuid, text, text
) from PUBLIC, anon, authenticated;
revoke all on function public.oasis_finalize_prospect_outreach(
    text, uuid, uuid, text, text
) from PUBLIC, anon, authenticated;
revoke all on function public.oasis_list_prospect_outreach_history(
    text, text, integer, integer
) from PUBLIC, anon, authenticated;
revoke all on function public.oasis_admin_reconcile_prospect_outreach(
    text, uuid, text, text
) from PUBLIC, anon, authenticated;

grant execute on function public.oasis_reserve_prospect_outreach(
    text, text, text, text, uuid, uuid, text, uuid, timestamptz, text, text
) to service_role;
grant execute on function public.oasis_begin_prospect_outreach_dispatch(
    text, uuid, uuid, text, text
) to service_role;
grant execute on function public.oasis_finalize_prospect_outreach(
    text, uuid, uuid, text, text
) to service_role;
grant execute on function public.oasis_list_prospect_outreach_history(
    text, text, integer, integer
) to service_role;
grant execute on function public.oasis_admin_reconcile_prospect_outreach(
    text, uuid, text, text
) to service_role;

comment on table public.oasis_prospect_outreach_outbox is
    'Metadata-only, no-auto-retry ledger for free-form sales outreach.';
comment on column public.oasis_prospect_outreach_outbox.content_hmac is
    'Secret-key HMAC idempotency fingerprint; message content is not stored.';
comment on column public.oasis_prospect_outreach_outbox.recipient_hmac is
    'Secret-key HMAC binding; the recipient value is not stored.';
comment on column public.oasis_prospect_outreach_outbox.safe_result_code is
    'Sanitized result code only; provider payloads and identifiers are excluded.';
