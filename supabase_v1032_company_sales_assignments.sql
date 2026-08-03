-- Application compatibility: OASIS CRM v9.8.9
-- Database migration: v10.3.2 company-wide sales assignment and duplicate-contact prevention
--
-- This migration is intentionally additive. Existing prospect, search-history,
-- contact, CRM, consultation, and memo data are preserved in place.
-- The application uses a server-side service_role with custom oasis_users auth;
-- therefore every table and RPC below is service_role-only. RPCs validate the
-- explicit current user against oasis_users before reading or changing data.

begin;

create extension if not exists pgcrypto with schema extensions;

-- The first RPC group is declared before its additive tables so the patch can
-- retain the existing application-facing layout. Its PL/pgSQL bodies use
-- generic record variables and are resolved only after this transaction has
-- created every dependency. This transaction-local setting makes those
-- forward references safe on a brand-new project as well as on an upgrade.
set local check_function_bodies = off;

-- ---------------------------------------------------------------------------
-- 1. Canonical company identity helpers
-- ---------------------------------------------------------------------------

create or replace function public.oasis_sales_digits(p_value text)
returns text
language sql
immutable
set search_path = public, pg_temp
as $$
    select regexp_replace(normalize(coalesce(p_value, ''), NFKC), '[^0-9]', '', 'g');
$$;

create or replace function public.oasis_normalize_sales_company_name(p_value text)
returns text
language sql
immutable
set search_path = public, pg_temp
as $$
    select nullif(
        regexp_replace(
            regexp_replace(
                lower(
                    replace(
                        replace(
                            normalize(btrim(coalesce(p_value, '')), NFKC),
                            'ẞ', 'ss'
                        ),
                        'ß', 'ss'
                    )
                ),
                '([(]주[)]|[(]유[)]|㈜|주식회사|유한회사|합자회사|합명회사)',
                '',
                'g'
            ),
            '[^0-9a-z가-힣]',
            '',
            'g'
        ),
        ''
    );
$$;

create or replace function public.oasis_normalize_sales_address(p_value text)
returns text
language sql
immutable
set search_path = public, pg_temp
as $$
    select nullif(
        regexp_replace(
            lower(
                replace(
                    replace(
                        normalize(btrim(coalesce(p_value, '')), NFKC),
                        'ẞ', 'ss'
                    ),
                    'ß', 'ss'
                )
            ),
            '[^0-9a-z가-힣]',
            '',
            'g'
        ),
        ''
    );
$$;

create or replace function public.oasis_normalize_sales_phone(p_value text)
returns text
language plpgsql
immutable
set search_path = public, pg_temp
as $$
declare
    v_raw text := regexp_replace(
        normalize(coalesce(p_value, ''), NFKC),
        '(ext(ension)?[.]?|내선).*$',
        '',
        'i'
    );
    v_digits text := public.oasis_sales_digits(v_raw);
begin
    if v_digits = '' then
        return null;
    end if;

    if v_digits like '0082%' then
        v_digits := substr(v_digits, 5);
        if left(v_digits, 1) <> '0' then
            v_digits := '0' || v_digits;
        end if;
    elsif v_digits like '82%' and length(v_digits) >= 10 then
        v_digits := substr(v_digits, 3);
        if left(v_digits, 1) <> '0' then
            v_digits := '0' || v_digits;
        end if;
    end if;

    return nullif(v_digits, '');
end;
$$;

create or replace function public.oasis_save_user_prospect_note(
    p_current_user_id text,
    p_company_uid text,
    p_company_id uuid,
    p_memo text
)
returns boolean
language plpgsql
volatile
set search_path = public, pg_temp
as $$
declare
    v_uid text;
begin
    if not public.oasis_sales_actor_is_active(p_current_user_id) then
        raise exception using errcode = '42501', message = 'OASIS_SALES_USER_NOT_AUTHORIZED';
    end if;

    v_uid := public.oasis_resolve_company_sales_uid(p_company_id, p_company_uid);
    if v_uid is null then
        return false;
    end if;

    if not public.oasis_sales_actor_is_admin(p_current_user_id)
       and not exists (
           select 1
           from public.oasis_company_sales_assignments a
           where a.company_uid = v_uid
             and a.assigned_user_id = p_current_user_id
       )
       and not exists (
           select 1
           from public.oasis_prospect_companies p
           where p.company_uid = v_uid
             and p.owner_user_id = p_current_user_id
       ) then
        raise exception using errcode = '42501', message = 'OASIS_SALES_NOTE_ACCESS_DENIED';
    end if;

    insert into public.oasis_user_prospect_notes (
        company_uid,
        user_id,
        memo,
        source_prospect_id
    ) values (
        v_uid,
        p_current_user_id,
        coalesce(p_memo, ''),
        p_company_id
    )
    on conflict (company_uid, user_id) do update
    set
        memo = excluded.memo,
        source_prospect_id = coalesce(excluded.source_prospect_id, public.oasis_user_prospect_notes.source_prospect_id),
        updated_at = now();

    -- Keep the legacy memo field synchronized only on rows owned by this user.
    update public.oasis_prospect_companies p
    set memo = coalesce(p_memo, ''), updated_at = now()
    where p.company_uid = v_uid
      and p.owner_user_id = p_current_user_id
      and (p_company_id is null or p.id = p_company_id);

    return true;
end;
$$;

create or replace function public.oasis_record_company_views(
    p_current_user_id text,
    p_companies jsonb,
    p_session_id text default null
)
returns integer
language plpgsql
volatile
set search_path = public, pg_temp
as $$
declare
    v_item jsonb;
    v_company_id uuid;
    v_uid text;
    v_count integer := 0;
begin
    if not public.oasis_sales_actor_is_active(p_current_user_id) then
        raise exception using errcode = '42501', message = 'OASIS_SALES_USER_NOT_AUTHORIZED';
    end if;

    if p_companies is null or jsonb_typeof(p_companies) <> 'array' then
        return 0;
    end if;

    for v_item in
        select item.value
        from jsonb_array_elements(p_companies) with ordinality as item(value, position)
        where item.position <= 1000
    loop
        v_company_id := case
            when coalesce(v_item ->> 'company_id', '') ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            then (v_item ->> 'company_id')::uuid
            else null
        end;
        v_uid := public.oasis_resolve_company_sales_uid(
            v_company_id,
            v_item ->> 'company_uid'
        );

        if v_uid is not null then
            insert into public.oasis_company_view_history (
                company_id,
                company_uid,
                viewed_by_user_id,
                session_fingerprint
            ) values (
                v_company_id,
                v_uid,
                p_current_user_id,
                public.oasis_sales_session_fingerprint(p_session_id)
            );

            if v_company_id is not null then
                update public.oasis_prospect_companies p
                set company_uid = v_uid
                where p.id = v_company_id
                  and nullif(p.company_uid, '') is null;
            end if;
            v_count := v_count + 1;
        end if;
    end loop;

    return v_count;
end;
$$;

create or replace function public.oasis_list_user_company_assignments(
    p_current_user_id text,
    p_statuses text[] default null,
    p_limit integer default 200,
    p_offset integer default 0
)
returns table (
    assignment_id uuid,
    company_id uuid,
    company_uid text,
    source text,
    source_key text,
    business_no text,
    company_name text,
    address text,
    region text,
    industry_code text,
    industry_name text,
    employee_count integer,
    new_employee_count integer,
    lost_employee_count integer,
    monthly_notice_amount bigint,
    data_created_ym text,
    priority_score integer,
    priority_reasons jsonb,
    source_data jsonb,
    status text,
    assigned_at timestamptz,
    assignment_expires_at timestamptz,
    first_contacted_at timestamptz,
    last_contacted_at timestamptz,
    next_contact_at timestamptz,
    contact_count integer,
    own_memo text,
    legacy_hold boolean,
    updated_at timestamptz
)
language plpgsql
volatile
set search_path = public, pg_temp
as $$
begin
    if not public.oasis_sales_actor_is_active(p_current_user_id) then
        raise exception using errcode = '42501', message = 'OASIS_SALES_USER_NOT_AUTHORIZED';
    end if;

    perform public.oasis_release_expired_company_assignments(p_current_user_id, null);

    return query
    select
        a.id,
        p.id,
        a.company_uid,
        p.source,
        p.source_key,
        p.business_no,
        p.company_name,
        p.address,
        p.region,
        p.industry_code,
        p.industry_name,
        p.employee_count,
        p.new_employee_count,
        p.lost_employee_count,
        p.monthly_notice_amount,
        p.data_created_ym,
        p.priority_score,
        p.priority_reasons,
        p.source_data,
        a.status,
        a.assigned_at,
        a.assignment_expires_at,
        a.first_contacted_at,
        a.last_contacted_at,
        a.next_contact_at,
        a.contact_count,
        coalesce(n.memo, ''),
        a.legacy_hold,
        a.updated_at
    from public.oasis_company_sales_assignments a
    left join lateral (
        select candidate.*
        from public.oasis_prospect_companies candidate
        where candidate.company_uid = a.company_uid
        order by
            (candidate.id = a.company_id) desc,
            candidate.updated_at desc nulls last,
            candidate.id
        limit 1
    ) p on true
    left join public.oasis_user_prospect_notes n
      on n.company_uid = a.company_uid
     and n.user_id = p_current_user_id
    where a.assigned_user_id = p_current_user_id
      and (p_statuses is null or cardinality(p_statuses) = 0 or a.status = any(p_statuses))
    order by coalesce(a.next_contact_at, a.updated_at) desc nulls last
    limit greatest(1, least(coalesce(p_limit, 200), 1000))
    offset greatest(0, coalesce(p_offset, 0));
end;
$$;

create or replace function public.oasis_filter_blocked_company_uids(
    p_current_user_id text,
    p_company_uids text[],
    p_include_own boolean default false
)
returns table (
    company_uid text,
    relation text
)
language plpgsql
volatile
set search_path = public, pg_temp
as $$
begin
    if not public.oasis_sales_actor_is_active(p_current_user_id) then
        raise exception using errcode = '42501', message = 'OASIS_SALES_USER_NOT_AUTHORIZED';
    end if;

    perform public.oasis_release_expired_company_assignments(p_current_user_id, null);

    return query
    with requested as (
        select distinct btrim(uid) as uid
        from unnest(coalesce(p_company_uids, '{}'::text[])) uid
        where public.oasis_is_valid_company_uid(uid)
    )
    select
        r.uid,
        case
            when a.id is null then 'available'
            when a.status = 'unassigned'
              and a.assigned_user_id is null
              and a.permanently_excluded is false
              and a.migration_conflict is false then 'available'
            when a.assigned_user_id = p_current_user_id
              and coalesce(p_include_own, false) then 'own'
            else 'blocked'
        end
    from requested r
    left join public.oasis_company_sales_assignments a
      on a.company_uid = r.uid;
end;
$$;

create or replace function public.oasis_list_company_sales_contacts(
    p_current_user_id text,
    p_company_uid text default null,
    p_limit integer default 200,
    p_offset integer default 0
)
returns table (
    id uuid,
    company_id uuid,
    company_uid text,
    contact_method text,
    contact_result text,
    notes text,
    contacted_at timestamptz,
    next_contact_at timestamptz,
    assigned_user_id text,
    created_by_user_id text,
    created_at timestamptz
)
language plpgsql
stable
set search_path = public, pg_temp
as $$
declare
    v_is_admin boolean;
begin
    if not public.oasis_sales_actor_is_active(p_current_user_id) then
        raise exception using errcode = '42501', message = 'OASIS_SALES_USER_NOT_AUTHORIZED';
    end if;
    v_is_admin := public.oasis_sales_actor_is_admin(p_current_user_id);

    return query
    select
        l.id,
        l.company_id,
        l.company_uid,
        l.contact_method,
        l.contact_result,
        l.notes,
        l.contacted_at,
        l.next_contact_at,
        l.assigned_user_id,
        l.created_by_user_id,
        l.created_at
    from public.oasis_company_sales_contact_logs l
    left join public.oasis_company_sales_assignments a
      on a.company_uid = l.company_uid
    where (nullif(btrim(p_company_uid), '') is null or l.company_uid = btrim(p_company_uid))
      and (
          v_is_admin
          or (
              l.created_by_user_id = p_current_user_id
              and a.assigned_user_id = p_current_user_id
          )
      )
    order by l.contacted_at desc, l.created_at desc
    limit greatest(1, least(coalesce(p_limit, 200), 1000))
    offset greatest(0, coalesce(p_offset, 0));
end;
$$;


create or replace function public.oasis_record_company_sales_contact(
    p_current_user_id text,
    p_company_id uuid,
    p_company_uid text,
    p_contact_method text,
    p_contact_result text,
    p_notes text default null,
    p_next_contact_at timestamptz default null,
    p_contacted_at timestamptz default now(),
    p_session_id text default null
)
returns table (
    success boolean,
    code text,
    message text,
    assignment_id uuid,
    company_uid text,
    status text,
    first_contacted_at timestamptz,
    last_contacted_at timestamptz,
    next_contact_at timestamptz,
    contact_count integer
)
language plpgsql
volatile
set search_path = public, pg_temp
as $$
declare
    v_uid text;
    v_assignment record;
    v_saved record;
    v_result text := lower(btrim(coalesce(p_contact_result, '')));
    v_method text := lower(btrim(coalesce(p_contact_method, '')));
    v_status text;
    v_reactivate_at timestamptz;
    v_rejected_days integer;
    v_unreachable_days integer;
    v_contacted_at timestamptz := coalesce(p_contacted_at, now());
    v_is_admin boolean;
begin
    if not public.oasis_sales_actor_is_active(p_current_user_id) then
        raise exception using
            errcode = '42501',
            message = 'OASIS_SALES_USER_NOT_AUTHORIZED';
    end if;

    v_is_admin := public.oasis_sales_actor_is_admin(p_current_user_id);
    v_uid := public.oasis_resolve_company_sales_uid(p_company_id, p_company_uid);
    if v_uid is null then
        return query select
            false, 'invalid_company_uid', '업체 공통 식별키를 확인할 수 없습니다.',
            null::uuid, null::text, null::text,
            null::timestamptz, null::timestamptz, null::timestamptz, null::integer;
        return;
    end if;

    perform pg_advisory_xact_lock(hashtextextended('oasis-company:' || v_uid, 0));

    select a.*
    into v_assignment
    from public.oasis_company_sales_assignments a
    where a.company_uid = v_uid
    for update;

    if v_assignment.id is null
       or v_assignment.assigned_user_id is null
       or (not v_is_admin and v_assignment.assigned_user_id <> p_current_user_id) then
        perform public.oasis_write_company_assignment_audit(
            p_current_user_id,
            p_company_id,
            v_uid,
            'contact_record_failed',
            '{}'::jsonb,
            jsonb_build_object('reason', 'not_assigned_to_actor'),
            p_session_id
        );
        return query select
            false, 'not_assigned_to_user', '본인에게 배정된 업체만 연락결과를 기록할 수 있습니다.',
            null::uuid, v_uid, null::text,
            null::timestamptz, null::timestamptz, null::timestamptz, null::integer;
        return;
    end if;

    if v_assignment.permanently_excluded then
        return query select
            false, 'permanently_excluded', '영구 제외된 업체입니다.',
            v_assignment.id, v_uid, v_assignment.status,
            v_assignment.first_contacted_at, v_assignment.last_contacted_at,
            v_assignment.next_contact_at, v_assignment.contact_count;
        return;
    end if;

    v_method := case v_method
        when '전화' then 'phone'
        when '통화' then 'phone'
        when '문자' then 'sms'
        when '문자메시지' then 'sms'
        when '카카오톡' then 'kakao'
        when '카톡' then 'kakao'
        when '상담' then 'consultation'
        else v_method
    end;
    if v_method not in ('phone', 'sms', 'kakao', 'consultation', 'other') then
        return query select
            false, 'invalid_contact_method', '연락방식을 확인해 주세요.',
            v_assignment.id, v_uid, v_assignment.status,
            v_assignment.first_contacted_at, v_assignment.last_contacted_at,
            v_assignment.next_contact_at, v_assignment.contact_count;
        return;
    end if;

    v_result := case v_result
        when '부재중' then 'missed'
        when '연결됨' then 'connected'
        when '문자발송' then 'sms_sent'
        when '카카오톡 발송' then 'kakao_sent'
        when '카톡발송' then 'kakao_sent'
        when '상담예약' then 'consultation_scheduled'
        when '관심없음' then 'not_interested'
        when '재연락 요청' then 'follow_up_requested'
        when '재연락요청' then 'follow_up_requested'
        when '번호오류' then 'bad_number'
        when '기존거래처' then 'existing_customer'
        when '계약진행' then 'contract_in_progress'
        when '계약완료' then 'contracted'
        when '연락불가' then 'unreachable'
        else v_result
    end;

    if v_result not in (
        'missed', 'connected', 'sms_sent', 'kakao_sent',
        'consultation_scheduled', 'not_interested', 'follow_up_requested',
        'bad_number', 'existing_customer', 'contract_in_progress',
        'contracted', 'unreachable'
    ) then
        return query select
            false, 'invalid_contact_result', '연락결과를 확인해 주세요.',
            v_assignment.id, v_uid, v_assignment.status,
            v_assignment.first_contacted_at, v_assignment.last_contacted_at,
            v_assignment.next_contact_at, v_assignment.contact_count;
        return;
    end if;

    if v_result = 'follow_up_requested' and p_next_contact_at is null then
        return query select
            false, 'next_contact_required', '재연락 요청은 다음 연락예정일이 필요합니다.',
            v_assignment.id, v_uid, v_assignment.status,
            v_assignment.first_contacted_at, v_assignment.last_contacted_at,
            v_assignment.next_contact_at, v_assignment.contact_count;
        return;
    end if;

    select coalesce(
        (select s.rejected_reactivation_days from public.oasis_sales_assignment_settings s where s.user_id = v_assignment.assigned_user_id),
        (select s.rejected_reactivation_days from public.oasis_sales_assignment_settings s where s.user_id = '__default__'),
        180
    ), coalesce(
        (select s.unreachable_reactivation_days from public.oasis_sales_assignment_settings s where s.user_id = v_assignment.assigned_user_id),
        (select s.unreachable_reactivation_days from public.oasis_sales_assignment_settings s where s.user_id = '__default__'),
        30
    )
    into v_rejected_days, v_unreachable_days;

    v_status := case
        when v_result = 'not_interested' then 'rejected'
        when v_result = 'follow_up_requested' then 'follow_up'
        when v_result = 'bad_number' then 'wrong_number'
        when v_result in ('consultation_scheduled', 'contract_in_progress') then 'consulting'
        when v_result = 'contracted' then 'contracted'
        when v_result = 'existing_customer' then 'contacted'
        when v_result = 'unreachable' then 'unreachable'
        else 'contacted'
    end;

    v_reactivate_at := case
        when v_status = 'rejected' then v_contacted_at + make_interval(days => v_rejected_days)
        when v_status = 'unreachable' then v_contacted_at + make_interval(days => v_unreachable_days)
        else null
    end;

    update public.oasis_company_sales_assignments a
    set
        status = v_status,
        assignment_expires_at = null,
        first_contacted_by_user_id = coalesce(a.first_contacted_by_user_id, v_assignment.assigned_user_id),
        first_contacted_at = coalesce(a.first_contacted_at, v_contacted_at),
        last_contacted_at = greatest(coalesce(a.last_contacted_at, v_contacted_at), v_contacted_at),
        next_contact_at = p_next_contact_at,
        contact_count = a.contact_count + 1,
        current_assignment_contact_count = a.current_assignment_contact_count + 1,
        current_assignment_first_contacted_at = coalesce(a.current_assignment_first_contacted_at, v_contacted_at),
        wrong_number_phone_fingerprint = case
            when v_status = 'wrong_number'
                then public.oasis_company_sales_phone_fingerprint(v_uid)
            else null
        end,
        reactivate_at = v_reactivate_at,
        legacy_hold = false,
        released_at = null,
        released_reason = null,
        last_status_changed_at = now()
    where a.id = v_assignment.id
    returning * into v_saved;

    insert into public.oasis_company_sales_contact_logs (
        assignment_id,
        company_id,
        company_uid,
        assigned_user_id,
        created_by_user_id,
        contact_method,
        contact_result,
        notes,
        contacted_at,
        next_contact_at
    ) values (
        v_saved.id,
        coalesce(p_company_id, v_saved.company_id),
        v_saved.company_uid,
        v_saved.assigned_user_id,
        p_current_user_id,
        v_method,
        v_result,
        coalesce(p_notes, ''),
        v_contacted_at,
        p_next_contact_at
    );

    perform public.oasis_write_company_assignment_audit(
        p_current_user_id,
        v_saved.company_id,
        v_saved.company_uid,
        'contact_result_recorded',
        jsonb_build_object(
            'status', v_assignment.status,
            'contact_count', v_assignment.contact_count
        ),
        jsonb_build_object(
            'status', v_saved.status,
            'contact_count', v_saved.contact_count,
            'contact_method', v_method,
            'contact_result', v_result,
            'next_contact_at', v_saved.next_contact_at
        ),
        p_session_id
    );

    return query select
        true, 'contact_recorded', '연락결과를 저장했습니다.',
        v_saved.id, v_saved.company_uid, v_saved.status,
        v_saved.first_contacted_at, v_saved.last_contacted_at,
        v_saved.next_contact_at, v_saved.contact_count;
end;
$$;

create or replace function public.oasis_release_company_sales_assignment(
    p_current_user_id text,
    p_company_uid text,
    p_reason text default 'user_released',
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
    v_uid text;
    v_assignment record;
    v_saved record;
    v_is_admin boolean;
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

    update public.oasis_company_sales_assignments a
    set
        assigned_user_id = null,
        status = 'unassigned',
        assigned_at = null,
        assignment_expires_at = null,
        next_contact_at = null,
        current_assignment_contact_count = 0,
        current_assignment_first_contacted_at = null,
        reactivate_at = null,
        legacy_hold = false,
        released_at = now(),
        released_reason = coalesce(nullif(btrim(p_reason), ''), 'user_released'),
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
            'reason', v_saved.released_reason
        ),
        p_session_id
    );

    return query select true, 'released', '배정을 해제했습니다.',
        v_saved.id, v_saved.company_uid, v_saved.status,
        v_saved.assigned_at, v_saved.assignment_expires_at;
end;
$$;


set local check_function_bodies = on;


create or replace function public.oasis_make_company_uid(
    p_business_no text,
    p_corporate_registration_no text,
    p_nps_workplace_management_no text,
    p_company_name text,
    p_address text,
    p_phone text,
    p_source text,
    p_source_key text
)
returns text
language plpgsql
immutable
set search_path = public, extensions, pg_temp
as $$
declare
    v_business text := public.oasis_sales_digits(p_business_no);
    v_corporate text := public.oasis_sales_digits(p_corporate_registration_no);
    v_nps text := regexp_replace(
        upper(normalize(btrim(coalesce(p_nps_workplace_management_no, '')), NFKC)),
        '[^0-9A-Z]',
        '',
        'g'
    );
    v_name text := public.oasis_normalize_sales_company_name(p_company_name);
    v_address text := public.oasis_normalize_sales_address(p_address);
    v_phone text := public.oasis_normalize_sales_phone(p_phone);
    v_source text := nullif(
        lower(
            replace(
                replace(
                    normalize(btrim(coalesce(p_source, '')), NFKC),
                    'ẞ', 'ss'
                ),
                'ß', 'ss'
            )
        ),
        ''
    );
    v_source_key text := nullif(normalize(btrim(coalesce(p_source_key, '')), NFKC), '');
begin
    if length(v_business) = 10 then
        return 'business:' || v_business;
    end if;

    if length(v_corporate) = 13 then
        return 'corporate:' || v_corporate;
    end if;

    if v_nps <> '' then
        return 'nps:' || v_nps;
    end if;

    -- Never identify a company from name alone, or even name + address alone.
    if v_name is not null and v_address is not null and v_phone is not null then
        return 'fallback:' || encode(
            extensions.digest(v_name || '|' || v_address || '|' || v_phone, 'sha256'),
            'hex'
        );
    end if;

    -- A source-scoped identity avoids unsafe merging when contact data is sparse.
    if v_source is not null and v_source_key is not null then
        return 'source:' || encode(
            extensions.digest(v_source || '|' || v_source_key, 'sha256'),
            'hex'
        );
    end if;

    return null;
end;
$$;

create or replace function public.oasis_is_valid_company_uid(p_company_uid text)
returns boolean
language sql
immutable
set search_path = public, pg_temp
as $$
    select coalesce(
        btrim(p_company_uid) ~ '^(business:[0-9]{10}|corporate:[0-9]{13}|nps:[0-9A-Z]+|fallback:[0-9a-f]{64}|source:[0-9a-f]{64})$',
        false
    );
$$;

alter table public.oasis_prospect_companies
    add column if not exists company_uid text,
    add column if not exists corporate_registration_no text,
    add column if not exists nps_workplace_management_no text;

update public.oasis_prospect_companies p
set
    corporate_registration_no = coalesce(
        nullif(p.corporate_registration_no, ''),
        nullif(p.source_data ->> 'corporate_registration_no', ''),
        nullif(p.source_data ->> 'corporate_no', ''),
        nullif(p.source_data ->> 'corp_reg_no', ''),
        nullif(p.source_data ->> 'jurirno', '')
    ),
    nps_workplace_management_no = coalesce(
        nullif(p.nps_workplace_management_no, ''),
        nullif(p.source_data ->> 'nps_workplace_management_no', ''),
        nullif(p.source_data ->> 'nps_workplace_no', ''),
        nullif(p.source_data ->> 'workplace_management_no', ''),
        nullif(p.source_data ->> 'workplace_id', '')
    )
where nullif(p.corporate_registration_no, '') is null
   or nullif(p.nps_workplace_management_no, '') is null;

update public.oasis_prospect_companies p
set company_uid = public.oasis_make_company_uid(
    p.business_no,
    p.corporate_registration_no,
    p.nps_workplace_management_no,
    p.company_name,
    p.address,
    coalesce(
        nullif(p.source_data ->> 'mobile_phone', ''),
        nullif(p.source_data ->> 'landline_phone', ''),
        nullif(p.source_data ->> 'phone', ''),
        (
            select c.contact_value
            from public.oasis_prospect_contacts c
            where c.prospect_id = p.id
              and c.do_not_contact is not true
              and c.contact_type in ('mobile', 'mobile_phone', 'phone', 'landline', 'landline_phone')
            order by c.is_primary desc, c.confidence desc, c.created_at asc
            limit 1
        )
    ),
    p.source,
    p.source_key
)
where nullif(p.company_uid, '') is null;

create index if not exists oasis_prospect_companies_company_uid_idx
    on public.oasis_prospect_companies (company_uid)
    where company_uid is not null;

create index if not exists oasis_prospect_companies_corporate_no_idx
    on public.oasis_prospect_companies (corporate_registration_no)
    where corporate_registration_no is not null;

create index if not exists oasis_prospect_companies_nps_no_idx
    on public.oasis_prospect_companies (nps_workplace_management_no)
    where nps_workplace_management_no is not null;

-- ---------------------------------------------------------------------------
-- 2. Company-wide assignments, private notes, contact/view history, audit
-- ---------------------------------------------------------------------------

create table if not exists public.oasis_company_sales_assignments (
    id uuid primary key default extensions.gen_random_uuid(),
    company_id uuid references public.oasis_prospect_companies(id) on delete set null,
    company_uid text not null unique,
    assigned_user_id text,
    status text not null default 'unassigned',
    assigned_at timestamptz,
    assignment_expires_at timestamptz,
    first_assigned_by_user_id text,
    first_assigned_at timestamptz,
    first_contacted_by_user_id text,
    first_contacted_at timestamptz,
    last_contacted_at timestamptz,
    next_contact_at timestamptz,
    contact_count integer not null default 0,
    current_assignment_contact_count integer not null default 0,
    current_assignment_first_contacted_at timestamptz,
    wrong_number_phone_fingerprint text,
    reactivate_at timestamptz,
    permanently_excluded boolean not null default false,
    legacy_hold boolean not null default false,
    migration_conflict boolean not null default false,
    released_at timestamptz,
    released_reason text,
    last_status_changed_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint oasis_company_sales_assignments_status_check check (
        status in (
            'unassigned', 'assigned', 'pending_contact', 'contacted',
            'consulting', 'follow_up', 'rejected', 'contracted',
            'long_hold', 'unreachable', 'wrong_number', 'closed',
            'permanently_excluded'
        )
    ),
    constraint oasis_company_sales_assignments_contact_count_check check (contact_count >= 0),
    constraint oasis_company_sales_assignments_current_contact_count_check check (current_assignment_contact_count >= 0),
    constraint oasis_company_sales_assignments_uid_check check (public.oasis_is_valid_company_uid(company_uid))
);

alter table public.oasis_company_sales_assignments
    add column if not exists current_assignment_contact_count integer not null default 0,
    add column if not exists current_assignment_first_contacted_at timestamptz,
    add column if not exists wrong_number_phone_fingerprint text;

create table if not exists public.oasis_sales_assignment_settings (
    user_id text primary key,
    max_uncontacted integer not null default 30,
    assignment_hours integer not null default 24,
    rejected_reactivation_days integer not null default 180,
    unreachable_reactivation_days integer not null default 30,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint oasis_sales_assignment_settings_max_check check (max_uncontacted between 1 and 10000),
    constraint oasis_sales_assignment_settings_hours_check check (assignment_hours between 1 and 720),
    constraint oasis_sales_assignment_settings_rejected_check check (rejected_reactivation_days between 1 and 3650),
    constraint oasis_sales_assignment_settings_unreachable_check check (unreachable_reactivation_days between 1 and 3650)
);

insert into public.oasis_sales_assignment_settings (user_id)
values ('__default__')
on conflict (user_id) do nothing;

create table if not exists public.oasis_user_prospect_notes (
    id uuid primary key default extensions.gen_random_uuid(),
    company_uid text not null,
    user_id text not null,
    memo text not null default '',
    source_prospect_id uuid references public.oasis_prospect_companies(id) on delete set null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint oasis_user_prospect_notes_uid_check check (public.oasis_is_valid_company_uid(company_uid)),
    constraint oasis_user_prospect_notes_unique unique (company_uid, user_id)
);

create table if not exists public.oasis_company_sales_contact_logs (
    id uuid primary key default extensions.gen_random_uuid(),
    assignment_id uuid references public.oasis_company_sales_assignments(id) on delete set null,
    company_id uuid references public.oasis_prospect_companies(id) on delete set null,
    company_uid text not null,
    assigned_user_id text not null,
    created_by_user_id text not null,
    contact_method text not null,
    contact_result text not null,
    notes text not null default '',
    contacted_at timestamptz not null default now(),
    next_contact_at timestamptz,
    legacy_source_type text,
    legacy_source_id text,
    legacy_source_data jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    constraint oasis_company_sales_contact_logs_uid_check check (public.oasis_is_valid_company_uid(company_uid))
);

alter table public.oasis_company_sales_contact_logs
    add column if not exists legacy_source_type text,
    add column if not exists legacy_source_id text,
    add column if not exists legacy_source_data jsonb not null default '{}'::jsonb;

create table if not exists public.oasis_company_view_history (
    id bigint generated by default as identity primary key,
    company_id uuid references public.oasis_prospect_companies(id) on delete set null,
    company_uid text not null,
    viewed_by_user_id text not null,
    viewed_at timestamptz not null default now(),
    session_fingerprint text,
    constraint oasis_company_view_history_uid_check check (public.oasis_is_valid_company_uid(company_uid))
);

create table if not exists public.oasis_company_assignment_audit_logs (
    id bigint generated by default as identity primary key,
    user_id text,
    company_id uuid references public.oasis_prospect_companies(id) on delete set null,
    company_uid text,
    action text not null,
    previous_value jsonb not null default '{}'::jsonb,
    new_value jsonb not null default '{}'::jsonb,
    session_fingerprint text,
    created_at timestamptz not null default now()
);

create table if not exists public.oasis_company_assignment_conflicts (
    id uuid primary key default extensions.gen_random_uuid(),
    company_uid text not null unique,
    conflicting_user_ids text[] not null default '{}'::text[],
    prospect_ids uuid[] not null default '{}'::uuid[],
    conflict_details jsonb not null default '{}'::jsonb,
    resolution_status text not null default 'pending',
    resolved_by_user_id text,
    resolved_at timestamptz,
    resolution_reason text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint oasis_company_assignment_conflicts_uid_check check (public.oasis_is_valid_company_uid(company_uid)),
    constraint oasis_company_assignment_conflicts_resolution_check check (
        resolution_status in ('pending', 'assigned', 'reactivated', 'permanently_excluded')
    )
);

create index if not exists oasis_company_sales_assignments_assignee_status_idx
    on public.oasis_company_sales_assignments (assigned_user_id, status, assignment_expires_at);
create index if not exists oasis_company_sales_assignments_blocking_idx
    on public.oasis_company_sales_assignments (status, reactivate_at, permanently_excluded, migration_conflict);
create index if not exists oasis_company_sales_assignments_next_contact_idx
    on public.oasis_company_sales_assignments (assigned_user_id, next_contact_at)
    where next_contact_at is not null;
create index if not exists oasis_company_sales_contact_logs_company_idx
    on public.oasis_company_sales_contact_logs (company_uid, contacted_at desc);
create index if not exists oasis_company_sales_contact_logs_user_idx
    on public.oasis_company_sales_contact_logs (assigned_user_id, contacted_at desc);
create unique index if not exists oasis_company_sales_contact_logs_legacy_source_uidx
    on public.oasis_company_sales_contact_logs (legacy_source_type, legacy_source_id)
    where legacy_source_type is not null and legacy_source_id is not null;
create index if not exists oasis_company_view_history_company_idx
    on public.oasis_company_view_history (company_uid, viewed_at asc);
create index if not exists oasis_company_view_history_user_idx
    on public.oasis_company_view_history (viewed_by_user_id, viewed_at desc);
create index if not exists oasis_company_assignment_audit_company_idx
    on public.oasis_company_assignment_audit_logs (company_uid, created_at desc);
create index if not exists oasis_company_assignment_audit_user_idx
    on public.oasis_company_assignment_audit_logs (user_id, created_at desc);

drop trigger if exists oasis_company_sales_assignments_updated_at
    on public.oasis_company_sales_assignments;
create trigger oasis_company_sales_assignments_updated_at
before update on public.oasis_company_sales_assignments
for each row execute function public.set_oasis_updated_at();

drop trigger if exists oasis_sales_assignment_settings_updated_at
    on public.oasis_sales_assignment_settings;
create trigger oasis_sales_assignment_settings_updated_at
before update on public.oasis_sales_assignment_settings
for each row execute function public.set_oasis_updated_at();

drop trigger if exists oasis_user_prospect_notes_updated_at
    on public.oasis_user_prospect_notes;
create trigger oasis_user_prospect_notes_updated_at
before update on public.oasis_user_prospect_notes
for each row execute function public.set_oasis_updated_at();

drop trigger if exists oasis_company_assignment_conflicts_updated_at
    on public.oasis_company_assignment_conflicts;
create trigger oasis_company_assignment_conflicts_updated_at
before update on public.oasis_company_assignment_conflicts
for each row execute function public.set_oasis_updated_at();

create or replace function public.oasis_company_sales_phone_fingerprint(
    p_company_uid text
)
returns text
language plpgsql
stable
set search_path = public, extensions, pg_temp
as $$
declare
    v_uid text := nullif(btrim(p_company_uid), '');
    v_phones text[] := '{}'::text[];
    v_employment_phones text[] := '{}'::text[];
    v_fingerprint text;
begin
    select coalesce(array_agg(pc.phone), '{}'::text[])
    into v_phones
    from (
        select public.oasis_normalize_sales_phone(v.phone_value) as phone
        from public.oasis_prospect_companies p
        cross join lateral (
            values
                (p.source_data ->> 'mobile_phone'),
                (p.source_data ->> 'landline_phone'),
                (p.source_data ->> 'phone'),
                (p.source_data ->> 'phone_number'),
                (p.source_data ->> 'representative_phone'),
                (p.source_data ->> 'tel')
        ) as v(phone_value)
        where p.company_uid = v_uid

        union all

        select public.oasis_normalize_sales_phone(c.contact_value)
        from public.oasis_prospect_companies p
        join public.oasis_prospect_contacts c on c.prospect_id = p.id
        where p.company_uid = v_uid
          and c.do_not_contact is not true
          and c.contact_type in (
              'mobile', 'mobile_phone', 'phone', 'landline', 'landline_phone'
          )
    ) pc
    where pc.phone ~ '^0[0-9]{8,10}$';

    -- v1032 may be installed before the employment-contact cache migration.
    -- Resolve the optional table at runtime so this helper remains installable
    -- in either migration order, while still seeing newly enriched phones.
    if to_regclass('public.oasis_employment_contacts') is not null then
        execute $employment$
            select coalesce(array_agg(ep.phone), '{}'::text[])
            from (
                select public.oasis_normalize_sales_phone(v.phone_value) as phone
                from public.oasis_prospect_companies p
                join public.oasis_employment_contacts c
                  on (
                      (
                          length(public.oasis_sales_digits(p.business_no)) = 10
                          and public.oasis_sales_digits(c.business_no)
                              = public.oasis_sales_digits(p.business_no)
                      )
                      or c.contact_key = p.company_uid
                      or (
                          lower(btrim(c.source_type)) = lower(btrim(p.source))
                          and (
                              normalize(btrim(c.source_record_key), NFKC)
                                  = normalize(btrim(p.source_key), NFKC)
                              or lower(btrim(c.source_type)) || ':'
                                  || normalize(btrim(c.source_record_key), NFKC)
                                  = lower(btrim(p.source_key))
                          )
                      )
                  )
                cross join lateral (
                    values (c.mobile_phone), (c.landline_phone)
                ) as v(phone_value)
                where p.company_uid = $1
            ) ep
            where ep.phone ~ '^0[0-9]{8,10}$'
        $employment$
        into v_employment_phones
        using v_uid;

        v_phones := v_phones || coalesce(v_employment_phones, '{}'::text[]);
    end if;

    select encode(
        extensions.digest(string_agg(p.phone, '|' order by p.phone), 'sha256'),
        'hex'
    )
    into v_fingerprint
    from (
        select distinct u.phone
        from unnest(v_phones) as u(phone)
        where u.phone ~ '^0[0-9]{8,10}$'
    ) p;

    return v_fingerprint;
end;
$$;

-- ---------------------------------------------------------------------------
-- 3. Server-side authorization, identity resolution, and audit helpers
-- ---------------------------------------------------------------------------

create or replace function public.oasis_sales_actor_is_active(p_user_id text)
returns boolean
language sql
stable
set search_path = public, pg_temp
as $$
    select exists (
        select 1
        from public.oasis_users u
        where u.user_id = nullif(btrim(p_user_id), '')
          and u.status = 'approved'
    );
$$;

create or replace function public.oasis_sales_actor_is_admin(p_user_id text)
returns boolean
language sql
stable
set search_path = public, pg_temp
as $$
    select exists (
        select 1
        from public.oasis_users u
        where u.user_id = nullif(btrim(p_user_id), '')
          and u.status = 'approved'
          and u.role = 'admin'
    );
$$;

create or replace function public.oasis_sales_session_fingerprint(p_session_id text)
returns text
language sql
immutable
set search_path = public, extensions, pg_temp
as $$
    select case
        when nullif(btrim(p_session_id), '') is null then null
        else encode(extensions.digest(btrim(p_session_id), 'sha256'), 'hex')
    end;
$$;

create or replace function public.oasis_resolve_company_sales_uid(
    p_company_id uuid,
    p_company_uid text
)
returns text
language plpgsql
stable
set search_path = public, pg_temp
as $$
declare
    v_uid text;
begin
    if p_company_id is not null then
        select coalesce(
            case when public.oasis_is_valid_company_uid(p.company_uid) then p.company_uid end,
            public.oasis_make_company_uid(
                p.business_no,
                p.corporate_registration_no,
                p.nps_workplace_management_no,
                p.company_name,
                p.address,
                coalesce(
                    nullif(p.source_data ->> 'mobile_phone', ''),
                    nullif(p.source_data ->> 'landline_phone', ''),
                    nullif(p.source_data ->> 'phone', ''),
                    (
                        select c.contact_value
                        from public.oasis_prospect_contacts c
                        where c.prospect_id = p.id
                          and c.do_not_contact is not true
                          and c.contact_type in (
                              'mobile', 'mobile_phone', 'phone',
                              'landline', 'landline_phone'
                          )
                        order by c.is_primary desc, c.confidence desc, c.created_at asc
                        limit 1
                    )
                ),
                p.source,
                p.source_key
            )
        )
        into v_uid
        from public.oasis_prospect_companies p
        where p.id = p_company_id;
    end if;

    if v_uid is null and public.oasis_is_valid_company_uid(p_company_uid) then
        v_uid := btrim(p_company_uid);
    end if;

    return v_uid;
end;
$$;

create or replace function public.oasis_resolve_candidate_company_uids(
    p_current_user_id text,
    p_candidates jsonb
)
returns table (
    candidate_index integer,
    input_company_uid text,
    canonical_company_uid text,
    resolution_code text
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

    if jsonb_typeof(coalesce(p_candidates, '[]'::jsonb)) <> 'array' then
        return;
    end if;

    return query
    with candidate_input as (
        select
            (entry.ordinality - 1)::integer as candidate_index,
            nullif(btrim(entry.item ->> 'company_uid'), '') as input_company_uid,
            nullif(lower(normalize(btrim(entry.item ->> 'source'), NFKC)), '') as source,
            nullif(normalize(btrim(entry.item ->> 'source_key'), NFKC), '') as source_key,
            public.oasis_sales_digits(entry.item ->> 'business_no') as business_no,
            public.oasis_sales_digits(entry.item ->> 'corporate_registration_no') as corporate_no,
            regexp_replace(
                upper(normalize(coalesce(entry.item ->> 'nps_workplace_management_no', ''), NFKC)),
                '[^0-9A-Z]', '', 'g'
            ) as nps_no
        from jsonb_array_elements(p_candidates) with ordinality as entry(item, ordinality)
        where entry.ordinality <= 1000
    ), resolved as (
        select
            i.*,
            strong_match.uids as strong_uids,
            source_match.company_uid as source_company_uid
        from candidate_input i
        left join lateral (
            select array_agg(distinct matches.company_uid order by matches.company_uid) as uids
            from (
                select p.company_uid
                from public.oasis_prospect_companies p
                where length(i.business_no) = 10
                  and public.oasis_is_valid_company_uid(p.company_uid)
                  and (
                      p.company_uid = 'business:' || i.business_no
                      or p.business_no in (
                          i.business_no,
                          substr(i.business_no, 1, 3) || '-'
                              || substr(i.business_no, 4, 2) || '-'
                              || substr(i.business_no, 6, 5)
                      )
                  )

                union

                select p.company_uid
                from public.oasis_prospect_companies p
                where length(i.corporate_no) = 13
                  and public.oasis_is_valid_company_uid(p.company_uid)
                  and (
                      p.company_uid = 'corporate:' || i.corporate_no
                      or p.corporate_registration_no = i.corporate_no
                  )

                union

                select p.company_uid
                from public.oasis_prospect_companies p
                where nullif(i.nps_no, '') is not null
                  and public.oasis_is_valid_company_uid(p.company_uid)
                  and (
                      p.company_uid = 'nps:' || i.nps_no
                      or p.nps_workplace_management_no = i.nps_no
                  )
            ) matches
        ) strong_match on true
        left join lateral (
            select p.company_uid
            from public.oasis_prospect_companies p
            where i.source is not null
              and i.source_key is not null
              and p.source = i.source
              and p.source_key = i.source_key
              and public.oasis_is_valid_company_uid(p.company_uid)
            order by p.created_at asc nulls last, p.id
            limit 1
        ) source_match on true
    )
    select
        r.candidate_index,
        r.input_company_uid,
        case
            when cardinality(r.strong_uids) > 1 then r.input_company_uid
            when cardinality(r.strong_uids) = 1
                 and r.source_company_uid is not null
                 and r.strong_uids[1] <> r.source_company_uid
                then r.input_company_uid
            when cardinality(r.strong_uids) = 1 then r.strong_uids[1]
            when r.source_company_uid is not null then r.source_company_uid
            else r.input_company_uid
        end as canonical_company_uid,
        case
            when cardinality(r.strong_uids) > 1 then 'strong_identifier_conflict'
            when cardinality(r.strong_uids) = 1
                 and r.source_company_uid is not null
                 and r.strong_uids[1] <> r.source_company_uid
                then 'source_strong_identifier_conflict'
            when cardinality(r.strong_uids) = 1 then 'strong_identifier'
            when r.source_company_uid is not null then 'source_identity'
            when public.oasis_is_valid_company_uid(r.input_company_uid) then 'input_uid'
            else 'unresolved'
        end as resolution_code
    from resolved r
    order by r.candidate_index;
end;
$$;

create or replace function public.oasis_write_company_assignment_audit(
    p_user_id text,
    p_company_id uuid,
    p_company_uid text,
    p_action text,
    p_previous_value jsonb,
    p_new_value jsonb,
    p_session_id text
)
returns void
language sql
volatile
set search_path = public, pg_temp
as $$
    insert into public.oasis_company_assignment_audit_logs (
        user_id,
        company_id,
        company_uid,
        action,
        previous_value,
        new_value,
        session_fingerprint
    ) values (
        nullif(btrim(p_user_id), ''),
        p_company_id,
        nullif(btrim(p_company_uid), ''),
        nullif(btrim(p_action), ''),
        coalesce(p_previous_value, '{}'::jsonb),
        coalesce(p_new_value, '{}'::jsonb),
        public.oasis_sales_session_fingerprint(p_session_id)
    );
$$;

-- ---------------------------------------------------------------------------
-- 4. Preserve legacy user notes and migrate legacy saved prospects
-- ---------------------------------------------------------------------------

with legacy_notes as (
    select
        p.company_uid,
        p.owner_user_id,
        case
            when count(*) = 1 then (array_agg(p.memo order by p.created_at asc nulls last, p.id))[1]
            else string_agg(
                '[' || coalesce(nullif(p.source, ''), 'legacy') || ':' || coalesce(nullif(p.source_key, ''), p.id::text) || '] ' || p.memo,
                E'\n\n'
                order by p.created_at asc nulls last, p.id
            )
        end as merged_memo,
        (array_agg(p.id order by p.created_at asc nulls last, p.id))[1] as source_prospect_id,
        min(coalesce(p.created_at, now())) as created_at,
        max(coalesce(p.updated_at, p.created_at, now())) as updated_at
    from public.oasis_prospect_companies p
    where public.oasis_is_valid_company_uid(p.company_uid)
      and nullif(btrim(p.owner_user_id), '') is not null
      and nullif(p.memo, '') is not null
    group by p.company_uid, p.owner_user_id
)
insert into public.oasis_user_prospect_notes (
    company_uid,
    user_id,
    memo,
    source_prospect_id,
    created_at,
    updated_at
)
select
    n.company_uid,
    n.owner_user_id,
    n.merged_memo,
    n.source_prospect_id,
    n.created_at,
    n.updated_at
from legacy_notes n
on conflict (company_uid, user_id) do nothing;

with duplicated as (
    select
        p.company_uid,
        array_agg(distinct p.owner_user_id) filter (
            where nullif(btrim(p.owner_user_id), '') is not null
        ) as user_ids,
        array_agg(distinct p.id) as prospect_ids
    from public.oasis_prospect_companies p
    where public.oasis_is_valid_company_uid(p.company_uid)
      and nullif(btrim(p.owner_user_id), '') is not null
    group by p.company_uid
    having count(distinct p.owner_user_id) > 1
)
insert into public.oasis_company_assignment_conflicts (
    company_uid,
    conflicting_user_ids,
    prospect_ids,
    conflict_details
)
select
    d.company_uid,
    d.user_ids,
    d.prospect_ids,
    jsonb_build_object(
        'type', 'legacy_multiple_owners',
        'migrated_at', now(),
        'owner_count', cardinality(d.user_ids),
        'prospect_count', cardinality(d.prospect_ids)
    )
from duplicated d
on conflict (company_uid) do nothing;

with legacy_candidates as (
    select distinct on (p.company_uid)
        p.company_uid,
        p.id as company_id,
        p.owner_user_id,
        coalesce(p.created_at, now()) as saved_at,
        lower(concat_ws(
            ' ',
            p.status,
            crm.crm_data ->> 'status',
            crm.crm_data -> '_v44_profile' ->> 'pipeline_stage'
        )) as legacy_status
    from public.oasis_prospect_companies p
    left join lateral (
        select c.crm_data
        from public.oasis_crm c
        where c.owner_user_id = p.owner_user_id
          and nullif(p.business_no, '') is not null
          and c.business_no = p.business_no
        order by c.updated_at desc nulls last
        limit 1
    ) crm on true
    where public.oasis_is_valid_company_uid(p.company_uid)
      and nullif(btrim(p.owner_user_id), '') is not null
      and not exists (
          select 1
          from public.oasis_company_assignment_conflicts cf
          where cf.company_uid = p.company_uid
            and cf.resolution_status = 'pending'
      )
    order by p.company_uid, p.created_at asc nulls last, p.id
)
insert into public.oasis_company_sales_assignments (
    company_id,
    company_uid,
    assigned_user_id,
    status,
    assigned_at,
    assignment_expires_at,
    first_assigned_by_user_id,
    first_assigned_at,
    legacy_hold,
    last_status_changed_at,
    created_at,
    updated_at
)
select
    lc.company_id,
    lc.company_uid,
    lc.owner_user_id,
    case
        when lc.legacy_status like '%폐업%' then 'closed'
        when lc.legacy_status like '%계약완료%' then 'contracted'
        when lc.legacy_status like '%계약진행%'
          or lc.legacy_status like '%상담%' then 'consulting'
        when lc.legacy_status like '%재연락%' then 'follow_up'
        when lc.legacy_status like '%관심없음%'
          or lc.legacy_status like '%거절%' then 'rejected'
        when lc.legacy_status like '%번호오류%' then 'wrong_number'
        when lc.legacy_status like '%연락불가%' then 'unreachable'
        when lc.legacy_status like '%장기보류%'
          or lc.legacy_status like '%보류%' then 'long_hold'
        when lc.legacy_status like '%연락완료%' then 'contacted'
        else 'assigned'
    end,
    lc.saved_at,
    null,
    lc.owner_user_id,
    lc.saved_at,
    true,
    lc.saved_at,
    lc.saved_at,
    lc.saved_at
from legacy_candidates lc
on conflict (company_uid) do nothing;

insert into public.oasis_company_sales_assignments (
    company_id,
    company_uid,
    status,
    legacy_hold,
    migration_conflict
)
select
    cf.prospect_ids[1],
    cf.company_uid,
    'unassigned',
    true,
    true
from public.oasis_company_assignment_conflicts cf
where cf.resolution_status = 'pending'
on conflict (company_uid) do nothing;

-- Consultation journals are evidence of real contact. Preserve every journal
-- as an idempotent structured contact log. If journals or an existing saved
-- prospect disagree about the owner, keep every source row and create an
-- administrator-resolvable conflict instead of choosing a winner.
with journal_base as (
    select
        j.journal_id,
        lower(btrim(j.owner_user_id)) as owner_user_id,
        'business:' || public.oasis_sales_digits(j.business_no) as company_uid,
        prospect.company_id
    from public.oasis_consultation_journals j
    join public.oasis_users u
      on u.user_id = lower(btrim(j.owner_user_id))
    left join lateral (
        select p.id as company_id
        from public.oasis_prospect_companies p
        where public.oasis_sales_digits(p.business_no)
              = public.oasis_sales_digits(j.business_no)
        order by p.created_at asc nulls last, p.id
        limit 1
    ) prospect on true
    where length(public.oasis_sales_digits(j.business_no)) = 10
), owner_candidates as (
    select jb.company_uid, jb.owner_user_id, jb.company_id
    from journal_base jb
    union all
    select a.company_uid, a.assigned_user_id, a.company_id
    from public.oasis_company_sales_assignments a
    where a.company_uid in (select distinct jb.company_uid from journal_base jb)
      and a.assigned_user_id is not null
), journal_conflicts as (
    select
        oc.company_uid,
        array_agg(distinct oc.owner_user_id order by oc.owner_user_id) as user_ids,
        array_agg(distinct oc.company_id) filter (where oc.company_id is not null) as prospect_ids
    from owner_candidates oc
    group by oc.company_uid
    having count(distinct oc.owner_user_id) > 1
)
insert into public.oasis_company_assignment_conflicts (
    company_uid,
    conflicting_user_ids,
    prospect_ids,
    conflict_details
)
select
    jc.company_uid,
    jc.user_ids,
    coalesce(jc.prospect_ids, '{}'::uuid[]),
    jsonb_build_object(
        'type', 'legacy_consultation_multiple_owners',
        'migrated_at', now(),
        'owner_count', cardinality(jc.user_ids)
    )
from journal_conflicts jc
on conflict (company_uid) do update
set
    conflicting_user_ids = array(
        select distinct x
        from unnest(
            public.oasis_company_assignment_conflicts.conflicting_user_ids
            || excluded.conflicting_user_ids
        ) as x
        order by x
    ),
    prospect_ids = array(
        select distinct x
        from unnest(
            public.oasis_company_assignment_conflicts.prospect_ids
            || excluded.prospect_ids
        ) as x
        order by x
    ),
    conflict_details = public.oasis_company_assignment_conflicts.conflict_details
        || excluded.conflict_details,
    updated_at = now()
where public.oasis_company_assignment_conflicts.resolution_status = 'pending';

with journal_uids as (
    select
        'business:' || public.oasis_sales_digits(j.business_no) as company_uid,
        min(p.id::text)::uuid as company_id
    from public.oasis_consultation_journals j
    left join public.oasis_prospect_companies p
      on public.oasis_sales_digits(p.business_no)
         = public.oasis_sales_digits(j.business_no)
    where length(public.oasis_sales_digits(j.business_no)) = 10
    group by 'business:' || public.oasis_sales_digits(j.business_no)
)
insert into public.oasis_company_sales_assignments (
    company_id,
    company_uid,
    status,
    legacy_hold,
    migration_conflict
)
select
    ju.company_id,
    ju.company_uid,
    'unassigned',
    true,
    true
from journal_uids ju
join public.oasis_company_assignment_conflicts cf
  on cf.company_uid = ju.company_uid
 and cf.resolution_status = 'pending'
on conflict (company_uid) do update
set
    company_id = coalesce(public.oasis_company_sales_assignments.company_id, excluded.company_id),
    legacy_hold = true,
    migration_conflict = true,
    updated_at = now();

with journal_single_owner as (
    select
        'business:' || public.oasis_sales_digits(j.business_no) as company_uid,
        lower(btrim(min(j.owner_user_id))) as owner_user_id,
        min(j.saved_at) as first_contacted_at,
        max(j.saved_at) as last_contacted_at,
        min(p.id::text)::uuid as company_id
    from public.oasis_consultation_journals j
    join public.oasis_users u
      on u.user_id = lower(btrim(j.owner_user_id))
    left join public.oasis_prospect_companies p
      on public.oasis_sales_digits(p.business_no)
         = public.oasis_sales_digits(j.business_no)
    where length(public.oasis_sales_digits(j.business_no)) = 10
    group by 'business:' || public.oasis_sales_digits(j.business_no)
    having count(distinct lower(btrim(j.owner_user_id))) = 1
), eligible as (
    select jso.*
    from journal_single_owner jso
    where not exists (
        select 1
        from public.oasis_company_assignment_conflicts cf
        where cf.company_uid = jso.company_uid
          and cf.resolution_status = 'pending'
    )
)
insert into public.oasis_company_sales_assignments (
    company_id,
    company_uid,
    assigned_user_id,
    status,
    assigned_at,
    assignment_expires_at,
    first_assigned_by_user_id,
    first_assigned_at,
    first_contacted_by_user_id,
    first_contacted_at,
    last_contacted_at,
    legacy_hold,
    last_status_changed_at
)
select
    e.company_id,
    e.company_uid,
    e.owner_user_id,
    'consulting',
    e.first_contacted_at,
    null,
    e.owner_user_id,
    e.first_contacted_at,
    e.owner_user_id,
    e.first_contacted_at,
    e.last_contacted_at,
    true,
    e.last_contacted_at
from eligible e
on conflict (company_uid) do update
set
    company_id = coalesce(public.oasis_company_sales_assignments.company_id, excluded.company_id),
    assigned_user_id = excluded.assigned_user_id,
    status = case
        when public.oasis_company_sales_assignments.status in (
            'contracted', 'closed', 'permanently_excluded'
        ) then public.oasis_company_sales_assignments.status
        else 'consulting'
    end,
    assignment_expires_at = null,
    first_assigned_by_user_id = coalesce(
        public.oasis_company_sales_assignments.first_assigned_by_user_id,
        excluded.first_assigned_by_user_id
    ),
    first_assigned_at = least(
        coalesce(public.oasis_company_sales_assignments.first_assigned_at, excluded.first_assigned_at),
        excluded.first_assigned_at
    ),
    first_contacted_by_user_id = coalesce(
        public.oasis_company_sales_assignments.first_contacted_by_user_id,
        excluded.first_contacted_by_user_id
    ),
    first_contacted_at = least(
        coalesce(public.oasis_company_sales_assignments.first_contacted_at, excluded.first_contacted_at),
        excluded.first_contacted_at
    ),
    last_contacted_at = greatest(
        coalesce(public.oasis_company_sales_assignments.last_contacted_at, excluded.last_contacted_at),
        excluded.last_contacted_at
    ),
    legacy_hold = true,
    updated_at = now()
where public.oasis_company_sales_assignments.migration_conflict is false
  and (
      public.oasis_company_sales_assignments.assigned_user_id is null
      or public.oasis_company_sales_assignments.assigned_user_id = excluded.assigned_user_id
  );

insert into public.oasis_company_sales_contact_logs (
    assignment_id,
    company_id,
    company_uid,
    assigned_user_id,
    created_by_user_id,
    contact_method,
    contact_result,
    notes,
    contacted_at,
    legacy_source_type,
    legacy_source_id,
    legacy_source_data
)
select
    a.id,
    a.company_id,
    'business:' || public.oasis_sales_digits(j.business_no),
    lower(btrim(j.owner_user_id)),
    lower(btrim(j.owner_user_id)),
    'consultation',
    'connected',
    coalesce(j.summary, ''),
    j.saved_at,
    'consultation_journal',
    j.journal_id,
    coalesce(j.journal_data, '{}'::jsonb)
from public.oasis_consultation_journals j
join public.oasis_users u
  on u.user_id = lower(btrim(j.owner_user_id))
join public.oasis_company_sales_assignments a
  on a.company_uid = 'business:' || public.oasis_sales_digits(j.business_no)
where length(public.oasis_sales_digits(j.business_no)) = 10
on conflict (legacy_source_type, legacy_source_id)
where legacy_source_type is not null and legacy_source_id is not null
do nothing;

with log_rollup as (
    select
        l.company_uid,
        min(l.assigned_user_id) as owner_user_id,
        count(*)::integer as log_count,
        min(l.contacted_at) as first_contacted_at,
        max(l.contacted_at) as last_contacted_at
    from public.oasis_company_sales_contact_logs l
    where exists (
        select 1
        from public.oasis_company_sales_contact_logs legacy
        where legacy.company_uid = l.company_uid
          and legacy.legacy_source_type = 'consultation_journal'
    )
    group by l.company_uid
    having count(distinct l.assigned_user_id) = 1
)
update public.oasis_company_sales_assignments a
set
    assigned_user_id = lr.owner_user_id,
    status = case
        when a.status in ('contracted', 'closed', 'permanently_excluded') then a.status
        else 'consulting'
    end,
    assignment_expires_at = null,
    first_contacted_by_user_id = coalesce(a.first_contacted_by_user_id, lr.owner_user_id),
    first_contacted_at = least(
        coalesce(a.first_contacted_at, lr.first_contacted_at),
        lr.first_contacted_at
    ),
    last_contacted_at = greatest(
        coalesce(a.last_contacted_at, lr.last_contacted_at),
        lr.last_contacted_at
    ),
    contact_count = greatest(a.contact_count, lr.log_count),
    current_assignment_contact_count = greatest(
        a.current_assignment_contact_count,
        lr.log_count
    ),
    current_assignment_first_contacted_at = least(
        coalesce(a.current_assignment_first_contacted_at, lr.first_contacted_at),
        lr.first_contacted_at
    ),
    legacy_hold = true,
    last_status_changed_at = greatest(a.last_status_changed_at, lr.last_contacted_at)
from log_rollup lr
where a.company_uid = lr.company_uid
  and a.migration_conflict is false
  and (a.assigned_user_id is null or a.assigned_user_id = lr.owner_user_id);

update public.oasis_prospect_companies p
set
    owner_user_id = a.assigned_user_id,
    updated_at = now()
from public.oasis_company_sales_assignments a
where p.company_uid = a.company_uid
  and a.assigned_user_id is not null
  and a.migration_conflict is false
  and exists (
      select 1
      from public.oasis_company_sales_contact_logs l
      where l.company_uid = a.company_uid
        and l.legacy_source_type = 'consultation_journal'
  );

-- Establish the baseline contact set for legacy/partially-applied wrong-number
-- rows. A formatting-only change cannot reactivate a company because the
-- fingerprint is calculated from normalized, distinct valid phone numbers.
update public.oasis_company_sales_assignments a
set wrong_number_phone_fingerprint = public.oasis_company_sales_phone_fingerprint(a.company_uid)
where a.status = 'wrong_number'
  and a.wrong_number_phone_fingerprint is null;

-- ---------------------------------------------------------------------------
-- 5. Assignment lifecycle RPCs
-- ---------------------------------------------------------------------------

create or replace function public.oasis_company_sales_assignment_feature_ready()
returns boolean
language sql
stable
set search_path = public, pg_temp
as $$
    select to_regclass('public.oasis_company_sales_assignments') is not null
       and to_regclass('public.oasis_company_sales_contact_logs') is not null
       and to_regclass('public.oasis_company_assignment_audit_logs') is not null
       and to_regprocedure(
           'public.oasis_resolve_candidate_company_uids(text,jsonb)'
       ) is not null
       and to_regprocedure(
           'public.oasis_claim_and_save_company_sales_assignment(text,text,jsonb,text)'
       ) is not null;
$$;

create or replace function public.oasis_release_expired_company_assignments(
    p_current_user_id text,
    p_session_id text default null
)
returns integer
language plpgsql
volatile
set search_path = public, pg_temp
as $$
declare
    v_row public.oasis_company_sales_assignments%rowtype;
    v_count integer := 0;
    v_actor constant text := '__system__';
    v_triggered_by text := nullif(btrim(p_current_user_id), '');
begin
    if nullif(btrim(p_current_user_id), '') is not null
       and not public.oasis_sales_actor_is_active(p_current_user_id) then
        raise exception using
            errcode = '42501',
            message = 'OASIS_SALES_USER_NOT_AUTHORIZED';
    end if;

    for v_row in
        select a.*
        from public.oasis_company_sales_assignments a
        where a.status in ('assigned', 'pending_contact')
          and a.assigned_user_id is not null
          and a.assignment_expires_at is not null
          and a.assignment_expires_at <= now()
          and a.current_assignment_contact_count = 0
          and a.current_assignment_first_contacted_at is null
          and a.legacy_hold is false
          and a.permanently_excluded is false
          and a.migration_conflict is false
        for update skip locked
    loop
        update public.oasis_company_sales_assignments a
        set
            assigned_user_id = null,
            status = 'unassigned',
            assigned_at = null,
            assignment_expires_at = null,
            next_contact_at = null,
            released_at = now(),
            released_reason = 'assignment_expired',
            last_status_changed_at = now()
        where a.id = v_row.id;

        update public.oasis_prospect_companies p
        set
            owner_user_id = '',
            updated_at = now()
        where p.company_uid = v_row.company_uid
          and p.owner_user_id = v_row.assigned_user_id;

        perform public.oasis_write_company_assignment_audit(
            v_actor,
            v_row.company_id,
            v_row.company_uid,
            'assignment_expired',
            jsonb_build_object(
                'status', v_row.status,
                'assigned_user_id', v_row.assigned_user_id,
                'assignment_expires_at', v_row.assignment_expires_at
            ),
            jsonb_build_object(
                'status', 'unassigned',
                'assigned_user_id', null,
                'triggered_by_user_id', v_triggered_by
            ),
            p_session_id
        );
        v_count := v_count + 1;
    end loop;

    for v_row in
        select a.*
        from public.oasis_company_sales_assignments a
        where a.status in ('rejected', 'unreachable')
          and a.reactivate_at is not null
          and a.reactivate_at <= now()
          and a.permanently_excluded is false
          and a.migration_conflict is false
        for update skip locked
    loop
        update public.oasis_company_sales_assignments a
        set
            assigned_user_id = null,
            status = 'unassigned',
            assigned_at = null,
            assignment_expires_at = null,
            next_contact_at = null,
            reactivate_at = null,
            released_at = now(),
            released_reason = 'reactivation_period_elapsed',
            last_status_changed_at = now()
        where a.id = v_row.id;

        update public.oasis_prospect_companies p
        set
            owner_user_id = '',
            updated_at = now()
        where p.company_uid = v_row.company_uid
          and p.owner_user_id = v_row.assigned_user_id;

        perform public.oasis_write_company_assignment_audit(
            v_actor,
            v_row.company_id,
            v_row.company_uid,
            'assignment_reactivated_automatically',
            jsonb_build_object(
                'status', v_row.status,
                'assigned_user_id', v_row.assigned_user_id,
                'reactivate_at', v_row.reactivate_at
            ),
            jsonb_build_object(
                'status', 'unassigned',
                'assigned_user_id', null,
                'triggered_by_user_id', v_triggered_by
            ),
            p_session_id
        );
        v_count := v_count + 1;
    end loop;

    -- A wrong-number company stays blocked indefinitely unless the normalized
    -- valid phone set has actually changed.  Formatting-only edits produce the
    -- same fingerprint and do not release the assignment.
    for v_row in
        select a.*
        from public.oasis_company_sales_assignments a
        where a.status = 'wrong_number'
          and public.oasis_company_sales_phone_fingerprint(a.company_uid) is not null
          and public.oasis_company_sales_phone_fingerprint(a.company_uid)
              is distinct from a.wrong_number_phone_fingerprint
          and a.permanently_excluded is false
          and a.migration_conflict is false
        for update skip locked
    loop
        update public.oasis_company_sales_assignments a
        set
            assigned_user_id = null,
            status = 'unassigned',
            assigned_at = null,
            assignment_expires_at = null,
            next_contact_at = null,
            reactivate_at = null,
            wrong_number_phone_fingerprint = null,
            released_at = now(),
            released_reason = 'valid_phone_changed_after_wrong_number',
            last_status_changed_at = now()
        where a.id = v_row.id;

        update public.oasis_prospect_companies p
        set
            owner_user_id = '',
            updated_at = now()
        where p.company_uid = v_row.company_uid
          and p.owner_user_id = v_row.assigned_user_id;

        perform public.oasis_write_company_assignment_audit(
            v_actor,
            v_row.company_id,
            v_row.company_uid,
            'wrong_number_reactivated_after_phone_change',
            jsonb_build_object(
                'status', v_row.status,
                'assigned_user_id', v_row.assigned_user_id,
                'phone_fingerprint', v_row.wrong_number_phone_fingerprint
            ),
            jsonb_build_object(
                'status', 'unassigned',
                'assigned_user_id', null,
                'phone_fingerprint', public.oasis_company_sales_phone_fingerprint(v_row.company_uid),
                'triggered_by_user_id', v_triggered_by
            ),
            p_session_id
        );
        v_count := v_count + 1;
    end loop;

    return v_count;
end;
$$;

create or replace function public.oasis_claim_company_sales_assignment(
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
    v_uid text;
    v_company_id uuid := p_company_id;
    v_existing public.oasis_company_sales_assignments%rowtype;
    v_saved public.oasis_company_sales_assignments%rowtype;
    v_limit integer;
    v_hours integer;
    v_uncontacted integer;
begin
    if not public.oasis_sales_actor_is_active(p_current_user_id) then
        raise exception using
            errcode = '42501',
            message = 'OASIS_SALES_USER_NOT_AUTHORIZED';
    end if;

    perform public.oasis_release_expired_company_assignments(
        p_current_user_id,
        p_session_id
    );

    v_uid := public.oasis_resolve_company_sales_uid(p_company_id, p_company_uid);
    if v_uid is null then
        return query select
            false, 'invalid_company_uid',
            '업체 공통 식별키를 생성할 수 없습니다.',
            null::uuid, null::text, null::text,
            null::timestamptz, null::timestamptz;
        return;
    end if;

    if v_company_id is null then
        select p.id
        into v_company_id
        from public.oasis_prospect_companies p
        where p.company_uid = v_uid
        order by p.created_at asc nulls last, p.id
        limit 1;
    else
        update public.oasis_prospect_companies p
        set company_uid = v_uid
        where p.id = v_company_id
          and nullif(p.company_uid, '') is null;
    end if;

    perform pg_advisory_xact_lock(hashtextextended('oasis-company:' || v_uid, 0));
    perform pg_advisory_xact_lock(hashtextextended('oasis-user:' || p_current_user_id, 0));

    select a.*
    into v_existing
    from public.oasis_company_sales_assignments a
    where a.company_uid = v_uid
    for update;

    if v_existing.id is not null
       and v_existing.assigned_user_id = p_current_user_id
       and v_existing.status <> 'unassigned'
       and v_existing.permanently_excluded is false then
        return query select
            true, 'already_owned',
            '내 영업DB에 이미 저장된 업체입니다.',
            v_existing.id, v_existing.company_uid, v_existing.status,
            v_existing.assigned_at, v_existing.assignment_expires_at;
        return;
    end if;

    if v_existing.id is not null and (
        v_existing.permanently_excluded
        or v_existing.migration_conflict
        or v_existing.status <> 'unassigned'
        or v_existing.assigned_user_id is not null
    ) then
        perform public.oasis_write_company_assignment_audit(
            p_current_user_id,
            coalesce(v_existing.company_id, v_company_id),
            v_uid,
            'duplicate_assignment_attempt',
            jsonb_build_object('status', v_existing.status),
            jsonb_build_object('result', 'blocked'),
            p_session_id
        );

        return query select
            false, 'already_assigned',
            '다른 담당자가 먼저 배정받은 업체입니다. 검색 결과를 새로고침합니다.',
            null::uuid, v_uid, null::text,
            null::timestamptz, null::timestamptz;
        return;
    end if;

    select coalesce(
        (select s.max_uncontacted from public.oasis_sales_assignment_settings s where s.user_id = p_current_user_id),
        (select s.max_uncontacted from public.oasis_sales_assignment_settings s where s.user_id = '__default__'),
        30
    ), coalesce(
        (select s.assignment_hours from public.oasis_sales_assignment_settings s where s.user_id = p_current_user_id),
        (select s.assignment_hours from public.oasis_sales_assignment_settings s where s.user_id = '__default__'),
        24
    )
    into v_limit, v_hours;

    select count(*)::integer
    into v_uncontacted
    from public.oasis_company_sales_assignments a
    where a.assigned_user_id = p_current_user_id
      and a.status in ('assigned', 'pending_contact')
      and a.current_assignment_first_contacted_at is null
      and a.current_assignment_contact_count = 0
      and a.released_at is null;

    if v_uncontacted >= v_limit then
        perform public.oasis_write_company_assignment_audit(
            p_current_user_id,
            v_company_id,
            v_uid,
            'assignment_failed_limit',
            jsonb_build_object('uncontacted_count', v_uncontacted),
            jsonb_build_object('max_uncontacted', v_limit),
            p_session_id
        );

        return query select
            false, 'uncontacted_limit_reached',
            '미접촉 배정 DB는 최대 ' || v_limit::text || '개까지 보유할 수 있습니다. 기존 DB의 연락결과를 기록하거나 배정을 해제한 후 다시 시도해 주세요.',
            null::uuid, v_uid, null::text,
            null::timestamptz, null::timestamptz;
        return;
    end if;

    if v_existing.id is null then
        insert into public.oasis_company_sales_assignments (
            company_id,
            company_uid,
            assigned_user_id,
            status,
            assigned_at,
            assignment_expires_at,
            first_assigned_by_user_id,
            first_assigned_at,
            current_assignment_contact_count,
            current_assignment_first_contacted_at,
            released_at,
            released_reason,
            legacy_hold,
            migration_conflict,
            last_status_changed_at
        ) values (
            v_company_id,
            v_uid,
            p_current_user_id,
            'assigned',
            now(),
            now() + make_interval(hours => v_hours),
            p_current_user_id,
            now(),
            0,
            null,
            null,
            null,
            false,
            false,
            now()
        )
        on conflict on constraint oasis_company_sales_assignments_company_uid_key
        do nothing
        returning * into v_saved;

        if v_saved.id is null then
            perform public.oasis_write_company_assignment_audit(
                p_current_user_id,
                v_company_id,
                v_uid,
                'duplicate_assignment_attempt',
                '{}'::jsonb,
                jsonb_build_object('result', 'conflict'),
                p_session_id
            );
            return query select
                false, 'already_assigned',
                '다른 담당자가 먼저 배정받은 업체입니다. 검색 결과를 새로고침합니다.',
                null::uuid, v_uid, null::text,
                null::timestamptz, null::timestamptz;
            return;
        end if;
    else
        update public.oasis_company_sales_assignments a
        set
            company_id = coalesce(a.company_id, v_company_id),
            assigned_user_id = p_current_user_id,
            status = 'assigned',
            assigned_at = now(),
            assignment_expires_at = now() + make_interval(hours => v_hours),
            first_assigned_by_user_id = coalesce(a.first_assigned_by_user_id, p_current_user_id),
            first_assigned_at = coalesce(a.first_assigned_at, now()),
            next_contact_at = null,
            current_assignment_contact_count = 0,
            current_assignment_first_contacted_at = null,
            wrong_number_phone_fingerprint = null,
            reactivate_at = null,
            permanently_excluded = false,
            legacy_hold = false,
            migration_conflict = false,
            released_at = null,
            released_reason = null,
            last_status_changed_at = now()
        where a.id = v_existing.id
        returning * into v_saved;
    end if;

    update public.oasis_prospect_companies p
    set owner_user_id = p_current_user_id, updated_at = now()
    where p.company_uid = v_saved.company_uid;

    perform public.oasis_write_company_assignment_audit(
        p_current_user_id,
        v_saved.company_id,
        v_saved.company_uid,
        'assignment_created',
        case
            when v_existing.id is null then '{}'::jsonb
            else jsonb_build_object(
                'status', v_existing.status,
                'assigned_user_id', v_existing.assigned_user_id
            )
        end,
        jsonb_build_object(
            'status', v_saved.status,
            'assigned_user_id', p_current_user_id,
            'assignment_expires_at', v_saved.assignment_expires_at
        ),
        p_session_id
    );

    return query select
        true, 'assigned', '내 영업DB에 담았습니다.',
        v_saved.id, v_saved.company_uid, v_saved.status,
        v_saved.assigned_at, v_saved.assignment_expires_at;
end;
$$;

create or replace function public.oasis_claim_and_save_company_sales_assignment(
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
set search_path = public, extensions, pg_temp
as $$
declare
    v_payload jsonb := case
        when jsonb_typeof(coalesce(p_company_payload, '{}'::jsonb)) = 'object'
            then coalesce(p_company_payload, '{}'::jsonb)
        else '{}'::jsonb
    end;
    v_uid text := nullif(btrim(p_company_uid), '');
    v_computed_uid text;
    v_source text;
    v_source_key text;
    v_existing_uid text;
    v_business_no text;
    v_corporate_no text;
    v_nps_no text;
    v_existing_business_no text;
    v_existing_corporate_no text;
    v_existing_nps_no text;
    v_strong_identity_conflict boolean := false;
    v_uid_matches_strong_identity boolean := false;
    v_prospect_id uuid;
    v_claim record;
    v_source_data jsonb;
    v_priority_reasons jsonb;
    v_employee_count integer;
    v_new_employee_count integer;
    v_lost_employee_count integer;
    v_monthly_notice_amount bigint;
    v_priority_score integer;
begin
    if not public.oasis_sales_actor_is_active(p_current_user_id) then
        raise exception using errcode = '42501', message = 'OASIS_SALES_USER_NOT_AUTHORIZED';
    end if;
    if not public.oasis_is_valid_company_uid(v_uid) then
        return query select
            false, 'invalid_company_uid', '업체 공통 식별키를 생성할 수 없습니다.',
            null::uuid, null::text, null::text,
            null::timestamptz, null::timestamptz, null::uuid;
        return;
    end if;

    -- Cleanup must precede any prospect-row lock in this atomic path.
    perform public.oasis_release_expired_company_assignments(
        p_current_user_id,
        p_session_id
    );

    v_source := nullif(
        lower(
            replace(
                replace(
                    normalize(btrim(coalesce(v_payload ->> 'source', '')), NFKC),
                    'ẞ', 'ss'
                ),
                'ß', 'ss'
            )
        ),
        ''
    );
    v_source_key := nullif(
        normalize(btrim(coalesce(v_payload ->> 'source_key', '')), NFKC),
        ''
    );
    v_business_no := public.oasis_sales_digits(v_payload ->> 'business_no');
    v_corporate_no := public.oasis_sales_digits(
        v_payload ->> 'corporate_registration_no'
    );
    v_nps_no := regexp_replace(
        upper(normalize(coalesce(v_payload ->> 'nps_workplace_management_no', ''), NFKC)),
        '[^0-9A-Z]', '', 'g'
    );
    v_computed_uid := public.oasis_make_company_uid(
        v_payload ->> 'business_no',
        v_payload ->> 'corporate_registration_no',
        v_payload ->> 'nps_workplace_management_no',
        v_payload ->> 'company_name',
        v_payload ->> 'address',
        coalesce(
            nullif(v_payload ->> 'phone', ''),
            nullif(v_payload -> 'source_data' ->> 'mobile_phone', ''),
            nullif(v_payload -> 'source_data' ->> 'landline_phone', '')
        ),
        v_source,
        v_source_key
    );
    -- Python build_company_uid keeps an already-valid UID stable after the
    -- three strong identifiers and before a mutable phone-based fallback.
    -- Mirror that order here so contact enrichment cannot change identity.
    if length(public.oasis_sales_digits(v_payload ->> 'business_no')) <> 10
       and length(public.oasis_sales_digits(v_payload ->> 'corporate_registration_no')) <> 13
       and nullif(
            regexp_replace(
                upper(normalize(coalesce(v_payload ->> 'nps_workplace_management_no', ''), NFKC)),
                '[^0-9A-Z]', '', 'g'
            ),
            ''
       ) is null
       and public.oasis_is_valid_company_uid(v_payload ->> 'company_uid') then
        v_computed_uid := btrim(v_payload ->> 'company_uid');
    end if;
    v_source := coalesce(v_source, 'assignment_v1032');
    v_source_key := coalesce(v_source_key, v_uid);
    perform pg_advisory_xact_lock(
        hashtextextended('oasis-source:' || v_source || ':' || v_source_key, 0)
    );

    -- The source advisory lock serializes this source identity. Do not lock a
    -- prospect row before the claim RPC performs its defensive expiry cleanup;
    -- the later UPSERT acquires that row lock in a consistent order.
    select
        p.company_uid,
        p.id,
        coalesce(
            nullif(public.oasis_sales_digits(p.business_no), ''),
            case when p.company_uid like 'business:%' then substr(p.company_uid, 10) end
        ),
        coalesce(
            nullif(public.oasis_sales_digits(p.corporate_registration_no), ''),
            case when p.company_uid like 'corporate:%' then substr(p.company_uid, 11) end
        ),
        coalesce(
            nullif(
                regexp_replace(
                    upper(coalesce(p.nps_workplace_management_no, '')),
                    '[^0-9A-Z]', '', 'g'
                ),
                ''
            ),
            case when p.company_uid like 'nps:%' then substr(p.company_uid, 5) end
        )
    into
        v_existing_uid,
        v_prospect_id,
        v_existing_business_no,
        v_existing_corporate_no,
        v_existing_nps_no
    from public.oasis_prospect_companies p
    where p.source = v_source
      and p.source_key = v_source_key;

    v_strong_identity_conflict :=
        (
            length(v_business_no) = 10
            and length(coalesce(v_existing_business_no, '')) = 10
            and v_business_no <> v_existing_business_no
        )
        or (
            length(v_corporate_no) = 13
            and length(coalesce(v_existing_corporate_no, '')) = 13
            and v_corporate_no <> v_existing_corporate_no
        )
        or (
            nullif(v_nps_no, '') is not null
            and nullif(v_existing_nps_no, '') is not null
            and v_nps_no <> v_existing_nps_no
        )
        or (
            v_uid like 'business:%'
            and v_existing_uid like 'business:%'
            and v_uid <> v_existing_uid
        )
        or (
            v_uid like 'corporate:%'
            and v_existing_uid like 'corporate:%'
            and v_uid <> v_existing_uid
        )
        or (
            v_uid like 'nps:%'
            and v_existing_uid like 'nps:%'
            and v_uid <> v_existing_uid
        );

    if public.oasis_is_valid_company_uid(v_existing_uid)
       and v_strong_identity_conflict then
        return query select
            false, 'source_identity_conflict', '기존 원천 업체 식별정보와 일치하지 않습니다.',
            null::uuid, v_uid, null::text,
            null::timestamptz, null::timestamptz, v_prospect_id;
        return;
    end if;

    if public.oasis_is_valid_company_uid(v_existing_uid) then
        -- A mutable fallback hash may change when a phone is enriched. The
        -- existing source identity remains canonical unless strong IDs conflict.
        v_uid := v_existing_uid;
    elsif v_computed_uid is not null and v_computed_uid <> v_uid then
        -- The batch resolver may have selected a canonical UID from another
        -- source row through a matching strong identifier. Accept only that
        -- verified case; all other input/computed UID mismatches remain blocked.
        select exists (
            select 1
            from public.oasis_prospect_companies p
            where p.company_uid = v_uid
              and public.oasis_is_valid_company_uid(p.company_uid)
              and (
                  (
                      length(v_business_no) = 10
                      and public.oasis_sales_digits(p.business_no) = v_business_no
                  )
                  or (
                      length(v_corporate_no) = 13
                      and public.oasis_sales_digits(p.corporate_registration_no) = v_corporate_no
                  )
                  or (
                      nullif(v_nps_no, '') is not null
                      and regexp_replace(
                          upper(coalesce(p.nps_workplace_management_no, '')),
                          '[^0-9A-Z]', '', 'g'
                      ) = v_nps_no
                  )
              )
        )
        into v_uid_matches_strong_identity;

        if not v_uid_matches_strong_identity then
            return query select
                false, 'company_uid_mismatch', '업체 식별정보가 일치하지 않습니다.',
                null::uuid, v_uid, null::text,
                null::timestamptz, null::timestamptz, null::uuid;
            return;
        end if;
    end if;

    select *
    into v_claim
    from public.oasis_claim_company_sales_assignment(
        p_current_user_id,
        null::uuid,
        v_uid,
        p_session_id
    );
    if v_claim.success is not true then
        return query select
            v_claim.success,
            v_claim.code,
            v_claim.message,
            v_claim.assignment_id,
            v_claim.company_uid,
            v_claim.status,
            v_claim.assigned_at,
            v_claim.assignment_expires_at,
            v_prospect_id;
        return;
    end if;

    v_source_data := case
        when jsonb_typeof(v_payload -> 'source_data') = 'object'
            then v_payload -> 'source_data'
        else '{}'::jsonb
    end;
    v_priority_reasons := case
        when jsonb_typeof(v_payload -> 'priority_reasons') = 'array'
            then v_payload -> 'priority_reasons'
        else '[]'::jsonb
    end;
    v_employee_count := case
        when coalesce(v_payload ->> 'employee_count', '') ~ '^[0-9]{1,9}$'
            then (v_payload ->> 'employee_count')::integer
        else 0
    end;
    v_new_employee_count := case
        when coalesce(v_payload ->> 'new_employee_count', '') ~ '^[0-9]{1,9}$'
            then (v_payload ->> 'new_employee_count')::integer
        else 0
    end;
    v_lost_employee_count := case
        when coalesce(v_payload ->> 'lost_employee_count', '') ~ '^[0-9]{1,9}$'
            then (v_payload ->> 'lost_employee_count')::integer
        else 0
    end;
    v_monthly_notice_amount := case
        when coalesce(v_payload ->> 'monthly_notice_amount', '') ~ '^[0-9]{1,18}$'
            then (v_payload ->> 'monthly_notice_amount')::bigint
        else 0
    end;
    v_priority_score := case
        when coalesce(v_payload ->> 'priority_score', '') ~ '^[0-9]{1,9}$'
            then (v_payload ->> 'priority_score')::integer
        else 0
    end;

    insert into public.oasis_prospect_companies (
        source,
        source_key,
        business_no,
        corporate_registration_no,
        nps_workplace_management_no,
        company_uid,
        company_name,
        address,
        region,
        industry_code,
        industry_name,
        employee_count,
        new_employee_count,
        lost_employee_count,
        monthly_notice_amount,
        data_created_ym,
        priority_score,
        priority_reasons,
        status,
        owner_user_id,
        source_data,
        collected_at,
        updated_at
    ) values (
        left(v_source, 200),
        left(v_source_key, 500),
        case
            when length(public.oasis_sales_digits(v_payload ->> 'business_no')) = 10
                then substr(public.oasis_sales_digits(v_payload ->> 'business_no'), 1, 3)
                     || '-'
                     || substr(public.oasis_sales_digits(v_payload ->> 'business_no'), 4, 2)
                     || '-'
                     || substr(public.oasis_sales_digits(v_payload ->> 'business_no'), 6, 5)
            else nullif(public.oasis_sales_digits(v_payload ->> 'business_no'), '')
        end,
        nullif(public.oasis_sales_digits(v_payload ->> 'corporate_registration_no'), ''),
        nullif(
            regexp_replace(
                upper(normalize(coalesce(v_payload ->> 'nps_workplace_management_no', ''), NFKC)),
                '[^0-9A-Z]', '', 'g'
            ),
            ''
        ),
        v_uid,
        left(coalesce(v_payload ->> 'company_name', ''), 500),
        left(coalesce(v_payload ->> 'address', ''), 2000),
        left(coalesce(v_payload ->> 'region', ''), 200),
        left(coalesce(v_payload ->> 'industry_code', ''), 100),
        left(coalesce(v_payload ->> 'industry_name', ''), 500),
        v_employee_count,
        v_new_employee_count,
        v_lost_employee_count,
        v_monthly_notice_amount,
        left(coalesce(v_payload ->> 'data_created_ym', ''), 20),
        v_priority_score,
        v_priority_reasons,
        left(coalesce(nullif(v_payload ->> 'status', ''), 'candidate'), 100),
        p_current_user_id,
        v_source_data,
        now(),
        now()
    )
    on conflict (source, source_key) do update
    set
        business_no = coalesce(excluded.business_no, public.oasis_prospect_companies.business_no),
        corporate_registration_no = coalesce(
            excluded.corporate_registration_no,
            public.oasis_prospect_companies.corporate_registration_no
        ),
        nps_workplace_management_no = coalesce(
            excluded.nps_workplace_management_no,
            public.oasis_prospect_companies.nps_workplace_management_no
        ),
        company_uid = excluded.company_uid,
        company_name = coalesce(
            nullif(excluded.company_name, ''),
            public.oasis_prospect_companies.company_name
        ),
        address = coalesce(
            nullif(excluded.address, ''),
            public.oasis_prospect_companies.address
        ),
        region = coalesce(
            nullif(excluded.region, ''),
            public.oasis_prospect_companies.region
        ),
        industry_code = coalesce(
            nullif(excluded.industry_code, ''),
            public.oasis_prospect_companies.industry_code
        ),
        industry_name = coalesce(
            nullif(excluded.industry_name, ''),
            public.oasis_prospect_companies.industry_name
        ),
        employee_count = case
            when v_payload ? 'employee_count' then excluded.employee_count
            else public.oasis_prospect_companies.employee_count
        end,
        new_employee_count = case
            when v_payload ? 'new_employee_count' then excluded.new_employee_count
            else public.oasis_prospect_companies.new_employee_count
        end,
        lost_employee_count = case
            when v_payload ? 'lost_employee_count' then excluded.lost_employee_count
            else public.oasis_prospect_companies.lost_employee_count
        end,
        monthly_notice_amount = case
            when v_payload ? 'monthly_notice_amount' then excluded.monthly_notice_amount
            else public.oasis_prospect_companies.monthly_notice_amount
        end,
        data_created_ym = coalesce(
            nullif(excluded.data_created_ym, ''),
            public.oasis_prospect_companies.data_created_ym
        ),
        priority_score = case
            when v_payload ? 'priority_score' then excluded.priority_score
            else public.oasis_prospect_companies.priority_score
        end,
        priority_reasons = case
            when v_payload ? 'priority_reasons' then excluded.priority_reasons
            else public.oasis_prospect_companies.priority_reasons
        end,
        status = case
            when nullif(v_payload ->> 'status', '') is not null then excluded.status
            else public.oasis_prospect_companies.status
        end,
        owner_user_id = p_current_user_id,
        source_data = public.oasis_prospect_companies.source_data || excluded.source_data,
        collected_at = excluded.collected_at,
        updated_at = now()
    returning id into v_prospect_id;

    update public.oasis_company_sales_assignments a
    set
        company_id = coalesce(a.company_id, v_prospect_id),
        updated_at = now()
    where a.id = v_claim.assignment_id;

    return query select
        v_claim.success,
        v_claim.code,
        v_claim.message,
        v_claim.assignment_id,
        v_claim.company_uid,
        v_claim.status,
        v_claim.assigned_at,
        v_claim.assignment_expires_at,
        v_prospect_id;
end;
$$;

-- ---------------------------------------------------------------------------
-- 6. Administrator assignment controls and reporting RPCs
-- ---------------------------------------------------------------------------

create or replace function public.oasis_admin_change_company_assignee(
    p_current_user_id text,
    p_company_uid text,
    p_new_assigned_user_id text,
    p_reason text,
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
    v_uid text := nullif(btrim(p_company_uid), '');
    v_assignment public.oasis_company_sales_assignments%rowtype;
    v_saved public.oasis_company_sales_assignments%rowtype;
    v_limit integer;
    v_hours integer;
    v_uncontacted integer;
begin
    if not public.oasis_sales_actor_is_admin(p_current_user_id) then
        raise exception using errcode = '42501', message = 'OASIS_SALES_ADMIN_REQUIRED';
    end if;
    if not public.oasis_sales_actor_is_active(p_new_assigned_user_id) then
        return query select false, 'invalid_assignee', '승인된 사용자만 담당자로 지정할 수 있습니다.',
            null::uuid, v_uid, null::text, null::timestamptz, null::timestamptz;
        return;
    end if;
    if not public.oasis_is_valid_company_uid(v_uid) then
        return query select false, 'invalid_company_uid', '업체 공통 식별키를 확인할 수 없습니다.',
            null::uuid, null::text, null::text, null::timestamptz, null::timestamptz;
        return;
    end if;
    if nullif(btrim(p_reason), '') is null then
        return query select false, 'reason_required', '담당자 변경 사유를 입력해 주세요.',
            null::uuid, v_uid, null::text, null::timestamptz, null::timestamptz;
        return;
    end if;

    perform pg_advisory_xact_lock(hashtextextended('oasis-company:' || v_uid, 0));
    perform pg_advisory_xact_lock(hashtextextended('oasis-user:' || p_new_assigned_user_id, 0));

    select a.* into v_assignment
    from public.oasis_company_sales_assignments a
    where a.company_uid = v_uid
    for update;

    if v_assignment.id is null then
        return query select false, 'assignment_not_found', '배정 정보를 찾을 수 없습니다.',
            null::uuid, v_uid, null::text, null::timestamptz, null::timestamptz;
        return;
    end if;

    select coalesce(
        (select s.max_uncontacted from public.oasis_sales_assignment_settings s where s.user_id = p_new_assigned_user_id),
        (select s.max_uncontacted from public.oasis_sales_assignment_settings s where s.user_id = '__default__'),
        30
    ), coalesce(
        (select s.assignment_hours from public.oasis_sales_assignment_settings s where s.user_id = p_new_assigned_user_id),
        (select s.assignment_hours from public.oasis_sales_assignment_settings s where s.user_id = '__default__'),
        24
    ) into v_limit, v_hours;

    if v_assignment.assigned_user_id is distinct from p_new_assigned_user_id
       and v_assignment.status in ('unassigned', 'assigned', 'pending_contact') then
        select count(*)::integer into v_uncontacted
        from public.oasis_company_sales_assignments a
        where a.assigned_user_id = p_new_assigned_user_id
          and a.status in ('assigned', 'pending_contact')
          and a.current_assignment_contact_count = 0;
        if v_uncontacted >= v_limit then
            return query select false, 'uncontacted_limit_reached',
                '대상 사용자의 미접촉 배정 한도를 초과합니다.',
                v_assignment.id, v_uid, v_assignment.status,
                v_assignment.assigned_at, v_assignment.assignment_expires_at;
            return;
        end if;
    end if;

    update public.oasis_company_sales_assignments a
    set
        assigned_user_id = p_new_assigned_user_id,
        status = case when a.status = 'unassigned' then 'assigned' else a.status end,
        assigned_at = now(),
        assignment_expires_at = case
            when a.status in ('unassigned', 'assigned', 'pending_contact')
            then now() + make_interval(hours => v_hours)
            else null
        end,
        first_assigned_by_user_id = coalesce(a.first_assigned_by_user_id, p_new_assigned_user_id),
        first_assigned_at = coalesce(a.first_assigned_at, now()),
        current_assignment_contact_count = 0,
        current_assignment_first_contacted_at = null,
        permanently_excluded = false,
        legacy_hold = false,
        migration_conflict = false,
        released_at = null,
        released_reason = null,
        last_status_changed_at = now()
    where a.id = v_assignment.id
    returning * into v_saved;

    update public.oasis_prospect_companies p
    set owner_user_id = p_new_assigned_user_id, updated_at = now()
    where p.company_uid = v_uid;

    update public.oasis_company_assignment_conflicts c
    set
        resolution_status = 'assigned',
        resolved_by_user_id = p_current_user_id,
        resolved_at = now(),
        resolution_reason = p_reason
    where c.company_uid = v_uid
      and c.resolution_status = 'pending';

    perform public.oasis_write_company_assignment_audit(
        p_current_user_id,
        v_saved.company_id,
        v_uid,
        'assignee_changed',
        jsonb_build_object(
            'assigned_user_id', v_assignment.assigned_user_id,
            'status', v_assignment.status
        ),
        jsonb_build_object(
            'assigned_user_id', p_new_assigned_user_id,
            'status', v_saved.status,
            'reason', p_reason
        ),
        p_session_id
    );

    return query select true, 'assignee_changed', '담당자를 변경했습니다.',
        v_saved.id, v_uid, v_saved.status,
        v_saved.assigned_at, v_saved.assignment_expires_at;
end;
$$;

create or replace function public.oasis_admin_release_company_assignment(
    p_current_user_id text,
    p_company_uid text,
    p_reason text,
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
begin
    if not public.oasis_sales_actor_is_admin(p_current_user_id) then
        raise exception using errcode = '42501', message = 'OASIS_SALES_ADMIN_REQUIRED';
    end if;
    if nullif(btrim(p_reason), '') is null then
        return query select false, 'reason_required', '강제 회수 사유를 입력해 주세요.',
            null::uuid, p_company_uid, null::text, null::timestamptz, null::timestamptz;
        return;
    end if;

    return query
    select r.success, r.code, r.message, r.assignment_id, r.company_uid,
           r.status, r.assigned_at, r.assignment_expires_at
    from public.oasis_release_company_sales_assignment(
        p_current_user_id,
        p_company_uid,
        'admin_recall:' || btrim(p_reason),
        p_session_id
    ) r;
end;
$$;

create or replace function public.oasis_admin_reactivate_company_assignment(
    p_current_user_id text,
    p_company_uid text,
    p_reason text,
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
    v_uid text := nullif(btrim(p_company_uid), '');
    v_previous public.oasis_company_sales_assignments%rowtype;
    v_saved public.oasis_company_sales_assignments%rowtype;
begin
    if not public.oasis_sales_actor_is_admin(p_current_user_id) then
        raise exception using errcode = '42501', message = 'OASIS_SALES_ADMIN_REQUIRED';
    end if;
    if not public.oasis_is_valid_company_uid(v_uid) then
        return query select false, 'invalid_company_uid', '업체 공통 식별키를 확인할 수 없습니다.',
            null::uuid, null::text, null::text, null::timestamptz, null::timestamptz;
        return;
    end if;
    if nullif(btrim(p_reason), '') is null then
        return query select false, 'reason_required', '재활성화 사유를 입력해 주세요.',
            null::uuid, v_uid, null::text, null::timestamptz, null::timestamptz;
        return;
    end if;

    perform pg_advisory_xact_lock(hashtextextended('oasis-company:' || v_uid, 0));
    select a.* into v_previous
    from public.oasis_company_sales_assignments a
    where a.company_uid = v_uid
    for update;

    if v_previous.id is null then
        return query select false, 'assignment_not_found', '배정 정보를 찾을 수 없습니다.',
            null::uuid, v_uid, null::text, null::timestamptz, null::timestamptz;
        return;
    end if;

    update public.oasis_company_sales_assignments a
    set
        assigned_user_id = null,
        status = 'unassigned',
        assigned_at = null,
        assignment_expires_at = null,
        next_contact_at = null,
        current_assignment_contact_count = 0,
        current_assignment_first_contacted_at = null,
        wrong_number_phone_fingerprint = null,
        reactivate_at = null,
        permanently_excluded = false,
        legacy_hold = false,
        migration_conflict = false,
        released_at = now(),
        released_reason = 'admin_reactivated:' || btrim(p_reason),
        last_status_changed_at = now()
    where a.id = v_previous.id
    returning * into v_saved;

    update public.oasis_prospect_companies p
    set owner_user_id = '', updated_at = now()
    where p.company_uid = v_uid;

    update public.oasis_company_assignment_conflicts c
    set
        resolution_status = 'reactivated',
        resolved_by_user_id = p_current_user_id,
        resolved_at = now(),
        resolution_reason = p_reason
    where c.company_uid = v_uid
      and c.resolution_status = 'pending';

    perform public.oasis_write_company_assignment_audit(
        p_current_user_id,
        v_saved.company_id,
        v_uid,
        'assignment_reactivated',
        jsonb_build_object(
            'assigned_user_id', v_previous.assigned_user_id,
            'status', v_previous.status,
            'permanently_excluded', v_previous.permanently_excluded
        ),
        jsonb_build_object('status', 'unassigned', 'reason', p_reason),
        p_session_id
    );

    return query select true, 'reactivated', '업체를 재활성화했습니다.',
        v_saved.id, v_uid, v_saved.status,
        v_saved.assigned_at, v_saved.assignment_expires_at;
end;
$$;

create or replace function public.oasis_admin_permanent_exclude_company(
    p_current_user_id text,
    p_company_uid text,
    p_reason text,
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
    v_uid text := nullif(btrim(p_company_uid), '');
    v_previous public.oasis_company_sales_assignments%rowtype;
    v_saved public.oasis_company_sales_assignments%rowtype;
begin
    if not public.oasis_sales_actor_is_admin(p_current_user_id) then
        raise exception using errcode = '42501', message = 'OASIS_SALES_ADMIN_REQUIRED';
    end if;
    if not public.oasis_is_valid_company_uid(v_uid) then
        return query select false, 'invalid_company_uid', '업체 공통 식별키를 확인할 수 없습니다.',
            null::uuid, null::text, null::text, null::timestamptz, null::timestamptz;
        return;
    end if;
    if nullif(btrim(p_reason), '') is null then
        return query select false, 'reason_required', '영구 제외 사유를 입력해 주세요.',
            null::uuid, v_uid, null::text, null::timestamptz, null::timestamptz;
        return;
    end if;

    perform pg_advisory_xact_lock(hashtextextended('oasis-company:' || v_uid, 0));
    select a.* into v_previous
    from public.oasis_company_sales_assignments a
    where a.company_uid = v_uid
    for update;

    if v_previous.id is null then
        return query select false, 'assignment_not_found', '배정 정보를 찾을 수 없습니다.',
            null::uuid, v_uid, null::text, null::timestamptz, null::timestamptz;
        return;
    end if;

    update public.oasis_company_sales_assignments a
    set
        assigned_user_id = null,
        status = 'permanently_excluded',
        assigned_at = null,
        assignment_expires_at = null,
        next_contact_at = null,
        reactivate_at = null,
        permanently_excluded = true,
        legacy_hold = false,
        migration_conflict = false,
        released_at = now(),
        released_reason = 'admin_permanent_exclude:' || btrim(p_reason),
        last_status_changed_at = now()
    where a.id = v_previous.id
    returning * into v_saved;

    update public.oasis_prospect_companies p
    set owner_user_id = '', updated_at = now()
    where p.company_uid = v_uid;

    update public.oasis_company_assignment_conflicts c
    set
        resolution_status = 'permanently_excluded',
        resolved_by_user_id = p_current_user_id,
        resolved_at = now(),
        resolution_reason = p_reason
    where c.company_uid = v_uid
      and c.resolution_status = 'pending';

    perform public.oasis_write_company_assignment_audit(
        p_current_user_id,
        v_saved.company_id,
        v_uid,
        'company_permanently_excluded',
        jsonb_build_object(
            'assigned_user_id', v_previous.assigned_user_id,
            'status', v_previous.status
        ),
        jsonb_build_object('status', 'permanently_excluded', 'reason', p_reason),
        p_session_id
    );

    return query select true, 'permanently_excluded', '업체를 영구 제외했습니다.',
        v_saved.id, v_uid, v_saved.status,
        v_saved.assigned_at, v_saved.assignment_expires_at;
end;
$$;

create or replace function public.oasis_admin_set_sales_user_limit(
    p_admin_user_id text,
    p_target_user_id text,
    p_max_uncontacted integer,
    p_reason text,
    p_session_id text default null
)
returns boolean
language plpgsql
volatile
set search_path = public, pg_temp
as $$
declare
    v_previous integer;
begin
    if not public.oasis_sales_actor_is_admin(p_admin_user_id) then
        raise exception using errcode = '42501', message = 'OASIS_SALES_ADMIN_REQUIRED';
    end if;
    if not public.oasis_sales_actor_is_active(p_target_user_id) then
        raise exception using errcode = '22023', message = 'OASIS_SALES_TARGET_USER_INVALID';
    end if;
    if p_max_uncontacted not between 1 and 1000 then
        raise exception using errcode = '22023', message = 'OASIS_SALES_LIMIT_OUT_OF_RANGE';
    end if;
    if nullif(btrim(p_reason), '') is null then
        raise exception using errcode = '22023', message = 'OASIS_SALES_REASON_REQUIRED';
    end if;

    select coalesce(
        (select s.max_uncontacted from public.oasis_sales_assignment_settings s where s.user_id = p_target_user_id),
        (select s.max_uncontacted from public.oasis_sales_assignment_settings s where s.user_id = '__default__'),
        30
    ) into v_previous;

    insert into public.oasis_sales_assignment_settings (user_id, max_uncontacted)
    values (p_target_user_id, p_max_uncontacted)
    on conflict (user_id) do update
    set max_uncontacted = excluded.max_uncontacted, updated_at = now();

    perform public.oasis_write_company_assignment_audit(
        p_admin_user_id,
        null,
        null,
        'user_assignment_limit_changed',
        jsonb_build_object(
            'target_user_id', p_target_user_id,
            'max_uncontacted', v_previous
        ),
        jsonb_build_object(
            'target_user_id', p_target_user_id,
            'max_uncontacted', p_max_uncontacted,
            'reason', p_reason
        ),
        p_session_id
    );
    return true;
end;
$$;

create or replace function public.oasis_list_admin_company_assignments(
    p_current_user_id text,
    p_statuses text[] default null,
    p_assigned_user_id text default null,
    p_limit integer default 500,
    p_offset integer default 0
)
returns table (
    assignment_id uuid,
    company_id uuid,
    company_uid text,
    company_name text,
    business_no text,
    address text,
    assigned_user_id text,
    assigned_user_name text,
    status text,
    first_viewed_by_user_id text,
    first_viewed_by_user_name text,
    first_viewed_at timestamptz,
    first_assigned_by_user_id text,
    first_assigned_by_user_name text,
    first_assigned_at timestamptz,
    first_contacted_by_user_id text,
    first_contacted_by_user_name text,
    first_contacted_at timestamptz,
    last_contacted_at timestamptz,
    next_contact_at timestamptz,
    contact_count integer,
    assignment_expires_at timestamptz,
    effective_max_uncontacted integer,
    assignee_uncontacted_count integer,
    legacy_hold boolean,
    migration_conflict boolean,
    conflicting_user_ids text[],
    conflict_details jsonb,
    conflict_resolution_status text,
    permanently_excluded boolean,
    released_at timestamptz,
    released_reason text,
    updated_at timestamptz,
    total_count bigint
)
language plpgsql
volatile
set search_path = public, pg_temp
as $$
begin
    if not public.oasis_sales_actor_is_admin(p_current_user_id) then
        raise exception using errcode = '42501', message = 'OASIS_SALES_ADMIN_REQUIRED';
    end if;
    perform public.oasis_release_expired_company_assignments(p_current_user_id, null);

    return query
    select
        a.id,
        p.id,
        a.company_uid,
        p.company_name,
        p.business_no,
        p.address,
        a.assigned_user_id,
        assigned_user.name,
        a.status,
        first_view.viewed_by_user_id,
        first_view_user.name,
        first_view.viewed_at,
        a.first_assigned_by_user_id,
        first_assign_user.name,
        a.first_assigned_at,
        a.first_contacted_by_user_id,
        first_contact_user.name,
        a.first_contacted_at,
        a.last_contacted_at,
        a.next_contact_at,
        a.contact_count,
        a.assignment_expires_at,
        coalesce(user_setting.max_uncontacted, default_setting.max_uncontacted, 30),
        coalesce(user_counts.uncontacted_count, 0),
        a.legacy_hold,
        a.migration_conflict,
        cf.conflicting_user_ids,
        cf.conflict_details,
        cf.resolution_status,
        a.permanently_excluded,
        a.released_at,
        a.released_reason,
        a.updated_at,
        count(*) over()::bigint
    from public.oasis_company_sales_assignments a
    left join lateral (
        select candidate.*
        from public.oasis_prospect_companies candidate
        where candidate.company_uid = a.company_uid
        order by (candidate.id = a.company_id) desc,
                 candidate.updated_at desc nulls last,
                 candidate.id
        limit 1
    ) p on true
    left join public.oasis_users assigned_user
      on assigned_user.user_id = a.assigned_user_id
    left join public.oasis_users first_assign_user
      on first_assign_user.user_id = a.first_assigned_by_user_id
    left join public.oasis_users first_contact_user
      on first_contact_user.user_id = a.first_contacted_by_user_id
    left join public.oasis_company_assignment_conflicts cf
      on cf.company_uid = a.company_uid
    left join lateral (
        select h.viewed_by_user_id, h.viewed_at
        from public.oasis_company_view_history h
        where h.company_uid = a.company_uid
        order by h.viewed_at asc, h.id asc
        limit 1
    ) first_view on true
    left join public.oasis_users first_view_user
      on first_view_user.user_id = first_view.viewed_by_user_id
    left join public.oasis_sales_assignment_settings user_setting
      on user_setting.user_id = a.assigned_user_id
    left join public.oasis_sales_assignment_settings default_setting
      on default_setting.user_id = '__default__'
    left join lateral (
        select count(*)::integer as uncontacted_count
        from public.oasis_company_sales_assignments own
        where own.assigned_user_id = a.assigned_user_id
          and own.status in ('assigned', 'pending_contact')
          and own.current_assignment_contact_count = 0
    ) user_counts on true
    where (p_statuses is null or cardinality(p_statuses) = 0 or a.status = any(p_statuses))
      and (
          nullif(btrim(p_assigned_user_id), '') is null
          or a.assigned_user_id = btrim(p_assigned_user_id)
      )
    order by a.updated_at desc
    limit greatest(1, least(coalesce(p_limit, 500), 1000))
    offset greatest(0, coalesce(p_offset, 0));
end;
$$;

create or replace function public.oasis_list_company_assignment_admin_metrics(
    p_current_user_id text
)
returns table (
    user_id text,
    user_name text,
    uncontacted_assignment_count bigint,
    contacted_assignment_count bigint,
    long_unprocessed_assignment_count bigint,
    total_assigned_count bigint,
    duplicate_assignment_attempt_count bigint,
    global_assignment_count bigint,
    global_duplicate_assignment_attempt_count bigint,
    global_migration_conflict_count bigint
)
language plpgsql
volatile
set search_path = public, pg_temp
as $$
begin
    if not public.oasis_sales_actor_is_admin(p_current_user_id) then
        raise exception using errcode = '42501', message = 'OASIS_SALES_ADMIN_REQUIRED';
    end if;
    perform public.oasis_release_expired_company_assignments(p_current_user_id, null);

    return query
    with assignment_counts as (
        select
            a.assigned_user_id,
            count(*) filter (
                where a.status in ('assigned', 'pending_contact')
                  and a.current_assignment_contact_count = 0
            )::bigint as uncontacted_count,
            count(*) filter (
                where a.current_assignment_contact_count > 0
                   or a.status in (
                       'contacted', 'consulting', 'follow_up', 'rejected',
                       'contracted', 'unreachable', 'wrong_number'
                   )
            )::bigint as contacted_count,
            count(*) filter (
                where a.status in ('assigned', 'pending_contact')
                  and a.current_assignment_contact_count = 0
                  and a.assigned_at <= now() - interval '12 hours'
            )::bigint as long_unprocessed_count,
            count(*)::bigint as total_count
        from public.oasis_company_sales_assignments a
        where a.assigned_user_id is not null
        group by a.assigned_user_id
    ), duplicate_counts as (
        select
            l.user_id,
            count(*)::bigint as duplicate_count
        from public.oasis_company_assignment_audit_logs l
        where l.action = 'duplicate_assignment_attempt'
          and l.user_id is not null
        group by l.user_id
    ), global_counts as (
        select
            (select count(*)::bigint from public.oasis_company_sales_assignments)
                as assignment_count,
            (select count(*)::bigint
             from public.oasis_company_assignment_audit_logs l
             where l.action = 'duplicate_assignment_attempt')
                as duplicate_count,
            (select count(*)::bigint
             from public.oasis_company_sales_assignments a
             where a.migration_conflict is true)
                as migration_conflict_count
    )
    select
        u.user_id,
        u.name,
        coalesce(ac.uncontacted_count, 0)::bigint,
        coalesce(ac.contacted_count, 0)::bigint,
        coalesce(ac.long_unprocessed_count, 0)::bigint,
        coalesce(ac.total_count, 0)::bigint,
        coalesce(dc.duplicate_count, 0)::bigint,
        gc.assignment_count,
        gc.duplicate_count,
        gc.migration_conflict_count
    from public.oasis_users u
    cross join global_counts gc
    left join assignment_counts ac on ac.assigned_user_id = u.user_id
    left join duplicate_counts dc on dc.user_id = u.user_id
    where u.status = 'approved'
    order by coalesce(ac.uncontacted_count, 0) desc, u.user_id;
end;
$$;

create or replace function public.oasis_list_company_assignment_audit(
    p_current_user_id text,
    p_company_uid text default null,
    p_limit integer default 200,
    p_offset integer default 0
)
returns table (
    id bigint,
    user_id text,
    user_name text,
    company_id uuid,
    company_uid text,
    action text,
    previous_value jsonb,
    new_value jsonb,
    session_fingerprint text,
    created_at timestamptz
)
language plpgsql
stable
set search_path = public, pg_temp
as $$
begin
    if not public.oasis_sales_actor_is_admin(p_current_user_id) then
        raise exception using errcode = '42501', message = 'OASIS_SALES_ADMIN_REQUIRED';
    end if;

    return query
    select
        l.id,
        l.user_id,
        u.name,
        l.company_id,
        l.company_uid,
        l.action,
        l.previous_value,
        l.new_value,
        l.session_fingerprint,
        l.created_at
    from public.oasis_company_assignment_audit_logs l
    left join public.oasis_users u on u.user_id = l.user_id
    where (nullif(btrim(p_company_uid), '') is null or l.company_uid = btrim(p_company_uid))
    order by l.created_at desc, l.id desc
    limit greatest(1, least(coalesce(p_limit, 200), 1000))
    offset greatest(0, coalesce(p_offset, 0));
end;
$$;

-- ---------------------------------------------------------------------------
-- 7. RLS lockout and service_role-only execution
-- ---------------------------------------------------------------------------

alter table public.oasis_company_sales_assignments enable row level security;
alter table public.oasis_sales_assignment_settings enable row level security;
alter table public.oasis_user_prospect_notes enable row level security;
alter table public.oasis_company_sales_contact_logs enable row level security;
alter table public.oasis_company_view_history enable row level security;
alter table public.oasis_company_assignment_audit_logs enable row level security;
alter table public.oasis_company_assignment_conflicts enable row level security;

revoke all on table public.oasis_company_sales_assignments from PUBLIC, anon, authenticated;
revoke all on table public.oasis_sales_assignment_settings from PUBLIC, anon, authenticated;
revoke all on table public.oasis_user_prospect_notes from PUBLIC, anon, authenticated;
revoke all on table public.oasis_company_sales_contact_logs from PUBLIC, anon, authenticated;
revoke all on table public.oasis_company_view_history from PUBLIC, anon, authenticated;
revoke all on table public.oasis_company_assignment_audit_logs from PUBLIC, anon, authenticated;
revoke all on table public.oasis_company_assignment_conflicts from PUBLIC, anon, authenticated;

grant select, insert, update, delete on table public.oasis_company_sales_assignments to service_role;
grant select, insert, update, delete on table public.oasis_sales_assignment_settings to service_role;
grant select, insert, update, delete on table public.oasis_user_prospect_notes to service_role;
grant select, insert, update, delete on table public.oasis_company_sales_contact_logs to service_role;
grant select, insert, update, delete on table public.oasis_company_view_history to service_role;
grant select, insert, update, delete on table public.oasis_company_assignment_audit_logs to service_role;
grant select, insert, update, delete on table public.oasis_company_assignment_conflicts to service_role;

revoke all on sequence public.oasis_company_view_history_id_seq
    from PUBLIC, anon, authenticated;
revoke all on sequence public.oasis_company_assignment_audit_logs_id_seq
    from PUBLIC, anon, authenticated;
grant usage, select on sequence public.oasis_company_view_history_id_seq
    to service_role;
grant usage, select on sequence public.oasis_company_assignment_audit_logs_id_seq
    to service_role;

do $$
declare
    fn record;
begin
    for fn in
        select
            p.proname,
            pg_get_function_identity_arguments(p.oid) as identity_arguments
        from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'public'
          and p.proname in (
              'oasis_sales_digits',
              'oasis_normalize_sales_company_name',
              'oasis_normalize_sales_address',
              'oasis_normalize_sales_phone',
              'oasis_company_sales_phone_fingerprint',
              'oasis_make_company_uid',
              'oasis_is_valid_company_uid',
              'oasis_sales_actor_is_active',
              'oasis_sales_actor_is_admin',
              'oasis_sales_session_fingerprint',
              'oasis_resolve_company_sales_uid',
              'oasis_resolve_candidate_company_uids',
              'oasis_write_company_assignment_audit',
              'oasis_company_sales_assignment_feature_ready',
              'oasis_release_expired_company_assignments',
              'oasis_claim_company_sales_assignment',
              'oasis_claim_and_save_company_sales_assignment',
              'oasis_record_company_sales_contact',
              'oasis_release_company_sales_assignment',
              'oasis_save_user_prospect_note',
              'oasis_record_company_views',
              'oasis_list_user_company_assignments',
              'oasis_filter_blocked_company_uids',
              'oasis_list_company_sales_contacts',
              'oasis_admin_change_company_assignee',
              'oasis_admin_release_company_assignment',
              'oasis_admin_reactivate_company_assignment',
              'oasis_admin_permanent_exclude_company',
              'oasis_admin_set_sales_user_limit',
              'oasis_list_admin_company_assignments',
              'oasis_list_company_assignment_admin_metrics',
              'oasis_list_company_assignment_audit'
          )
    loop
        execute format(
            'revoke all on function public.%I(%s) from PUBLIC, anon, authenticated',
            fn.proname,
            fn.identity_arguments
        );
        execute format(
            'grant execute on function public.%I(%s) to service_role',
            fn.proname,
            fn.identity_arguments
        );
    end loop;
end;
$$;

comment on table public.oasis_company_sales_assignments is
    'Company-wide source of truth for sales assignment and lifecycle state.';
comment on table public.oasis_user_prospect_notes is
    'Private per-user prospect memo storage; never returned to another ordinary user.';
comment on table public.oasis_company_sales_contact_logs is
    'Structured contact-attempt history. Consultation notes remain private to the owner/admin.';
comment on table public.oasis_company_assignment_conflicts is
    'Legacy duplicate-owner conflicts retained for explicit administrator resolution.';

commit;
