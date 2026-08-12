-- OASIS CRM direct sales customer registry
-- Keeps employee-owned customers outside the central DB assignment pool while
-- reusing oasis_customers and oasis_crm as the canonical customer/contract data.

create table if not exists public.oasis_direct_sales_customers (
    id uuid primary key default gen_random_uuid(),
    owner_user_id text not null,
    customer_id uuid not null,
    business_no text not null,
    company_name text not null,
    representative_name text not null default '',
    business_type text not null,
    discovery_type text not null default 'direct_registration',
    landline_phone text not null default '',
    mobile_phone text not null default '',
    mobile_phone_hash text,
    industry_name text not null default '',
    employee_count integer not null default 0,
    acquisition_source text not null default '',
    registration_memo text not null default '',
    marketing_consent_confirmed boolean not null default false,
    marketing_consent_at timestamptz,
    marketing_consent_method text not null default '',
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint oasis_direct_sales_customer_owner_customer_unique
        unique (owner_user_id, customer_id),
    constraint oasis_direct_sales_customer_business_unique
        unique (business_no),
    constraint oasis_direct_sales_customer_owner_fkey
        foreign key (customer_id, owner_user_id)
        references public.oasis_customers(id, owner_user_id),
    constraint oasis_direct_sales_customer_business_no_check
        check (business_no ~ '^[0-9]{10}$'),
    constraint oasis_direct_sales_customer_company_check
        check (length(btrim(company_name)) between 1 and 200),
    constraint oasis_direct_sales_customer_business_type_check
        check (business_type in ('individual', 'corporate')),
    constraint oasis_direct_sales_customer_discovery_check
        check (discovery_type = 'direct_registration'),
    constraint oasis_direct_sales_customer_phone_check
        check (
            (landline_phone = '' or landline_phone ~ '^0[0-9]{8,10}$')
            and (mobile_phone = '' or mobile_phone ~ '^01[016789][0-9]{7,8}$')
            and (
                (mobile_phone = '' and mobile_phone_hash is null)
                or (
                    mobile_phone <> ''
                    and mobile_phone_hash ~ '^[0-9a-f]{64}$'
                )
            )
        ),
    constraint oasis_direct_sales_customer_employee_check
        check (employee_count between 0 and 1000000),
    constraint oasis_direct_sales_customer_consent_check
        check (
            (
                marketing_consent_confirmed
                and marketing_consent_at is not null
                and length(btrim(marketing_consent_method)) between 1 and 100
            )
            or (
                not marketing_consent_confirmed
                and marketing_consent_at is null
                and marketing_consent_method = ''
            )
        )
);

create table if not exists public.oasis_direct_sales_claim_conflicts (
    id uuid primary key default gen_random_uuid(),
    requested_user_id text not null,
    business_no text not null,
    requested_company_name text not null default '',
    review_status text not null default 'pending',
    created_at timestamptz not null default now(),
    reviewed_at timestamptz,
    reviewed_by_user_id text,
    constraint oasis_direct_sales_claim_conflict_business_check
        check (business_no ~ '^[0-9]{10}$'),
    constraint oasis_direct_sales_claim_conflict_status_check
        check (review_status in ('pending', 'approved', 'rejected', 'resolved'))
);

create unique index if not exists idx_oasis_direct_claim_conflict_pending
    on public.oasis_direct_sales_claim_conflicts (
        requested_user_id, business_no
    ) where review_status = 'pending';

create index if not exists idx_oasis_direct_sales_owner_active_updated
    on public.oasis_direct_sales_customers (
        owner_user_id, is_active, updated_at desc
    );

create index if not exists idx_oasis_direct_sales_customer_owner
    on public.oasis_direct_sales_customers (customer_id, owner_user_id);

drop trigger if exists trg_oasis_direct_sales_customer_updated_at
    on public.oasis_direct_sales_customers;
create trigger trg_oasis_direct_sales_customer_updated_at
before update on public.oasis_direct_sales_customers
for each row execute function public.set_oasis_updated_at();


create or replace function public.oasis_register_direct_sales_customer(
    p_current_user_id text,
    p_company_name text,
    p_business_no text,
    p_business_type text,
    p_representative_name text default '',
    p_landline_phone text default '',
    p_mobile_phone text default '',
    p_mobile_phone_hash text default '',
    p_industry_name text default '',
    p_employee_count integer default 0,
    p_acquisition_source text default '',
    p_registration_memo text default '',
    p_marketing_consent_confirmed boolean default false,
    p_marketing_consent_method text default '',
    p_manager_name text default ''
)
returns table (
    success boolean,
    code text,
    message text,
    direct_customer_id uuid,
    customer_id uuid
)
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
    v_actor text := lower(btrim(coalesce(p_current_user_id, '')));
    v_company_name text := left(btrim(coalesce(p_company_name, '')), 200);
    v_business_no text := public.oasis_v911_normalize_business_no(p_business_no);
    v_business_type text := lower(btrim(coalesce(p_business_type, '')));
    v_representative_name text := left(btrim(coalesce(p_representative_name, '')), 100);
    v_landline text := pg_catalog.regexp_replace(coalesce(p_landline_phone, ''), '[^0-9]', '', 'g');
    v_mobile text := pg_catalog.regexp_replace(coalesce(p_mobile_phone, ''), '[^0-9]', '', 'g');
    v_mobile_hash text := lower(btrim(coalesce(p_mobile_phone_hash, '')));
    v_industry text := left(btrim(coalesce(p_industry_name, '')), 200);
    v_acquisition text := left(btrim(coalesce(p_acquisition_source, '')), 200);
    v_memo text := left(btrim(coalesce(p_registration_memo, '')), 2000);
    v_consent_method text := left(btrim(coalesce(p_marketing_consent_method, '')), 100);
    v_existing public.oasis_direct_sales_customers%rowtype;
    v_profile record;
    v_saved public.oasis_direct_sales_customers%rowtype;
begin
    if not public.oasis_sales_actor_is_active(v_actor) then
        raise exception using errcode = '42501', message = 'PERMISSION_DENIED';
    end if;
    if v_company_name = '' or v_business_no is null
       or v_business_type not in ('individual', 'corporate')
       or coalesce(p_employee_count, 0) < 0
       or coalesce(p_employee_count, 0) > 1000000
       or (v_landline <> '' and v_landline !~ '^0[0-9]{8,10}$')
       or (v_mobile <> '' and v_mobile !~ '^01[016789][0-9]{7,8}$')
       or (v_mobile <> '' and v_mobile_hash !~ '^[0-9a-f]{64}$')
       or (v_mobile = '' and v_mobile_hash <> '')
       or (
            coalesce(p_marketing_consent_confirmed, false)
            and v_consent_method = ''
       ) then
        return query select false, 'INVALID_REQUEST',
            '입력한 업체정보와 수신동의 내용을 확인해 주세요.',
            null::uuid, null::uuid;
        return;
    end if;

    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended('oasis-direct-sales:' || v_business_no, 0)
    );

    select d.* into v_existing
    from public.oasis_direct_sales_customers d
    where d.business_no = v_business_no
    for update;

    if v_existing.id is not null
       and v_existing.owner_user_id is distinct from v_actor then
        insert into public.oasis_direct_sales_claim_conflicts (
            requested_user_id, business_no, requested_company_name
        ) values (
            v_actor, v_business_no, v_company_name
        ) on conflict (requested_user_id, business_no)
          where review_status = 'pending'
          do update set
              requested_company_name = excluded.requested_company_name,
              created_at = now();
        return query select false, 'REVIEW_REQUIRED',
            '다른 담당자가 먼저 등록한 사업자번호여서 관리자 검토를 요청했습니다.',
            null::uuid, null::uuid;
        return;
    end if;

    select * into v_profile
    from public.oasis_upsert_customer_profile(
        v_actor,
        v_business_no,
        v_company_name,
        nullif(v_representative_name, ''),
        nullif(v_industry, ''),
        null,
        nullif(left(btrim(coalesce(p_manager_name, '')), 100), ''),
        'direct_sales_registration',
        pg_catalog.jsonb_build_object(
            '업체명', v_company_name,
            '사업자등록번호', v_business_no,
            '대표자명', v_representative_name,
            '사업자유형', case v_business_type
                when 'corporate' then '법인사업자'
                else '개인사업자'
            end,
            '일반전화', v_landline,
            '휴대전화', v_mobile,
            '업종명', v_industry,
            '종업원수', coalesce(p_employee_count, 0),
            '발굴유형', '직접등록',
            '직접등록경로', v_acquisition
        ),
        case when v_existing.id is not null then v_existing.customer_id else null end,
        null
    );

    if v_profile.customer_id is null then
        return query select false, 'CUSTOMER_SAVE_FAILED',
            '고객 원장에 안전하게 연결하지 못했습니다.',
            null::uuid, null::uuid;
        return;
    end if;

    insert into public.oasis_direct_sales_customers (
        owner_user_id, customer_id, business_no, company_name,
        representative_name, business_type, landline_phone, mobile_phone,
        mobile_phone_hash, industry_name, employee_count,
        acquisition_source, registration_memo,
        marketing_consent_confirmed, marketing_consent_at,
        marketing_consent_method, is_active
    ) values (
        v_actor, v_profile.customer_id, v_business_no, v_company_name,
        v_representative_name, v_business_type, v_landline, v_mobile,
        nullif(v_mobile_hash, ''), v_industry, coalesce(p_employee_count, 0),
        v_acquisition, v_memo,
        coalesce(p_marketing_consent_confirmed, false),
        case when coalesce(p_marketing_consent_confirmed, false)
            then now() else null end,
        case when coalesce(p_marketing_consent_confirmed, false)
            then v_consent_method else '' end,
        true
    ) on conflict (business_no) do update set
        company_name = excluded.company_name,
        representative_name = excluded.representative_name,
        business_type = excluded.business_type,
        landline_phone = excluded.landline_phone,
        mobile_phone = excluded.mobile_phone,
        mobile_phone_hash = excluded.mobile_phone_hash,
        industry_name = excluded.industry_name,
        employee_count = excluded.employee_count,
        acquisition_source = excluded.acquisition_source,
        registration_memo = excluded.registration_memo,
        marketing_consent_confirmed = excluded.marketing_consent_confirmed,
        marketing_consent_at = excluded.marketing_consent_at,
        marketing_consent_method = excluded.marketing_consent_method,
        is_active = true,
        updated_at = now()
    returning * into v_saved;

    return query select true,
        case when v_existing.id is null then 'REGISTERED' else 'UPDATED' end,
        case when v_existing.id is null
            then '등록 DB에 업체를 추가했습니다.'
            else '기존 등록 DB 업체정보를 갱신했습니다.'
        end,
        v_saved.id, v_saved.customer_id;
end;
$$;


create or replace function public.oasis_get_direct_sales_customer_summary(
    p_current_user_id text
)
returns table (
    total_count bigint,
    registered_count bigint,
    contracted_count bigint
)
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    v_actor text := lower(btrim(coalesce(p_current_user_id, '')));
begin
    if not public.oasis_sales_actor_is_active(v_actor) then
        raise exception using errcode = '42501', message = 'PERMISSION_DENIED';
    end if;
    return query
    select
        count(*)::bigint,
        count(*) filter (
            where coalesce(crm.crm_data ->> 'status', '') <> '계약완료'
        )::bigint,
        count(*) filter (
            where coalesce(crm.crm_data ->> 'status', '') = '계약완료'
        )::bigint
    from public.oasis_direct_sales_customers d
    left join public.oasis_crm crm
      on crm.owner_user_id = d.owner_user_id
     and crm.business_no = d.business_no
    where d.owner_user_id = v_actor
      and d.is_active;
end;
$$;


create or replace function public.oasis_list_direct_sales_customers(
    p_current_user_id text,
    p_filter text default 'all',
    p_direct_customer_id uuid default null,
    p_limit integer default 500,
    p_offset integer default 0
)
returns table (
    direct_customer_id uuid,
    customer_id uuid,
    company_uid text,
    company_name text,
    business_no text,
    representative_name text,
    business_type text,
    discovery_type text,
    landline_phone text,
    mobile_phone text,
    industry_name text,
    employee_count integer,
    acquisition_source text,
    registration_memo text,
    marketing_consent_confirmed boolean,
    marketing_consent_at timestamptz,
    marketing_consent_method text,
    crm_status text,
    sales_category text,
    created_at timestamptz,
    updated_at timestamptz,
    total_count bigint
)
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    v_actor text := lower(btrim(coalesce(p_current_user_id, '')));
    v_filter text := lower(btrim(coalesce(p_filter, 'all')));
begin
    if not public.oasis_sales_actor_is_active(v_actor) then
        raise exception using errcode = '42501', message = 'PERMISSION_DENIED';
    end if;
    if v_filter not in ('all', 'registered', 'contracted') then
        v_filter := 'all';
    end if;

    return query
    select
        d.id,
        d.customer_id,
        coalesce(link.company_uid, 'customer:' || d.customer_id::text),
        d.company_name,
        d.business_no,
        d.representative_name,
        d.business_type,
        d.discovery_type,
        d.landline_phone,
        d.mobile_phone,
        d.industry_name,
        d.employee_count,
        d.acquisition_source,
        d.registration_memo,
        d.marketing_consent_confirmed,
        d.marketing_consent_at,
        d.marketing_consent_method,
        coalesce(crm.crm_data ->> 'status', '신규'),
        case when coalesce(crm.crm_data ->> 'status', '') = '계약완료'
            then 'contracted' else 'registered' end,
        d.created_at,
        d.updated_at,
        count(*) over()
    from public.oasis_direct_sales_customers d
    left join public.oasis_customer_company_links link
      on link.owner_user_id = d.owner_user_id
     and link.customer_id = d.customer_id
    left join public.oasis_crm crm
      on crm.owner_user_id = d.owner_user_id
     and crm.business_no = d.business_no
    where d.owner_user_id = v_actor
      and d.is_active
      and (p_direct_customer_id is null or d.id = p_direct_customer_id)
      and (
          v_filter = 'all'
          or (
              v_filter = 'contracted'
              and coalesce(crm.crm_data ->> 'status', '') = '계약완료'
          )
          or (
              v_filter = 'registered'
              and coalesce(crm.crm_data ->> 'status', '') <> '계약완료'
          )
      )
    order by d.updated_at desc, d.id
    limit greatest(1, least(coalesce(p_limit, 500), 5000))
    offset greatest(0, coalesce(p_offset, 0));
end;
$$;


create table if not exists public.oasis_direct_customer_outreach_outbox (
    id uuid primary key default gen_random_uuid(),
    requested_by_user_id text not null,
    request_id text not null,
    content_hmac text not null,
    recipient_hmac text not null,
    recipient_phone_hash text not null,
    direct_customer_id uuid not null
        references public.oasis_direct_sales_customers(id) on delete restrict,
    direct_customer_updated_at timestamptz not null,
    channel text not null,
    status text not null default 'reserved',
    reservation_token uuid not null default gen_random_uuid(),
    safe_result_code text not null default '',
    reserved_at timestamptz not null default now(),
    dispatch_started_at timestamptz,
    finalized_at timestamptz,
    unknown_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint oasis_direct_outreach_owner_request_unique
        unique (requested_by_user_id, request_id),
    constraint oasis_direct_outreach_token_unique unique (reservation_token),
    constraint oasis_direct_outreach_request_check
        check (request_id ~ '^[A-Za-z0-9._:-]{8,200}$'),
    constraint oasis_direct_outreach_hmac_check check (
        content_hmac ~ '^[0-9a-f]{64}$'
        and recipient_hmac ~ '^[0-9a-f]{64}$'
        and recipient_phone_hash ~ '^[0-9a-f]{64}$'
    ),
    constraint oasis_direct_outreach_channel_check
        check (channel in ('sms', 'kakao')),
    constraint oasis_direct_outreach_status_check check (status in (
        'reserved', 'dispatching', 'provider_accepted',
        'provider_rejected', 'delivery_unknown',
        'cancelled_dnc', 'cancelled_changed', 'cancelled_stale'
    )),
    constraint oasis_direct_outreach_code_check check (
        safe_result_code = ''
        or safe_result_code ~ '^[A-Z0-9_-]{1,80}$'
    )
);

create index if not exists idx_oasis_direct_outreach_customer_history
    on public.oasis_direct_customer_outreach_outbox (
        requested_by_user_id, direct_customer_id, reserved_at desc
    );

create index if not exists idx_oasis_direct_outreach_duplicate_guard
    on public.oasis_direct_customer_outreach_outbox (
        recipient_hmac, channel, content_hmac, status, reserved_at desc
    );

create index if not exists idx_oasis_direct_outreach_open_dispatch
    on public.oasis_direct_customer_outreach_outbox (dispatch_started_at)
    where status = 'dispatching';


create or replace function public.oasis_reserve_direct_customer_outreach(
    p_current_user_id text,
    p_request_id text,
    p_content_hmac text,
    p_recipient_hmac text,
    p_recipient_phone_hash text,
    p_direct_customer_id uuid,
    p_direct_customer_updated_at timestamptz,
    p_channel text
)
returns table (
    success boolean, code text, message text, outbox_id uuid,
    status text, acquired boolean, reservation_token uuid,
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
    v_phone_hash text := lower(btrim(coalesce(p_recipient_phone_hash, '')));
    v_channel text := lower(btrim(coalesce(p_channel, '')));
    v_direct public.oasis_direct_sales_customers%rowtype;
    v_existing public.oasis_direct_customer_outreach_outbox%rowtype;
    v_saved public.oasis_direct_customer_outreach_outbox%rowtype;
begin
    if not public.oasis_sales_actor_is_active(v_actor) then
        raise exception using errcode = '42501', message = 'PERMISSION_DENIED';
    end if;
    if v_request !~ '^[A-Za-z0-9._:-]{8,200}$'
       or v_content_hmac !~ '^[0-9a-f]{64}$'
       or v_recipient_hmac !~ '^[0-9a-f]{64}$'
       or v_phone_hash !~ '^[0-9a-f]{64}$'
       or v_channel not in ('sms', 'kakao')
       or p_direct_customer_id is null
       or p_direct_customer_updated_at is null then
        return query select false, 'INVALID_REQUEST',
            '발송 요청값을 다시 확인해 주세요.',
            null::uuid, null::text, false, null::uuid, null::timestamptz;
        return;
    end if;

    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            'oasis-direct-outreach:' || v_actor || ':' || v_request, 0
        )
    );

    select o.* into v_existing
    from public.oasis_direct_customer_outreach_outbox o
    where o.requested_by_user_id = v_actor and o.request_id = v_request
    for update;
    if v_existing.id is not null then
        if v_existing.content_hmac is distinct from v_content_hmac
           or v_existing.recipient_hmac is distinct from v_recipient_hmac
           or v_existing.recipient_phone_hash is distinct from v_phone_hash
           or v_existing.direct_customer_id is distinct from p_direct_customer_id
           or v_existing.direct_customer_updated_at is distinct from p_direct_customer_updated_at
           or v_existing.channel is distinct from v_channel then
            return query select false, 'IDEMPOTENCY_CONFLICT',
                '같은 요청번호의 발송 대상이 달라 중단했습니다.',
                v_existing.id, v_existing.status, false, null::uuid,
                v_existing.reserved_at;
            return;
        end if;
        return query select true, 'ALREADY_RESERVED',
            '이미 접수된 요청입니다. 중복 발송하지 않았습니다.',
            v_existing.id, v_existing.status, false, null::uuid,
            v_existing.reserved_at;
        return;
    end if;

    select d.* into v_direct
    from public.oasis_direct_sales_customers d
    where d.id = p_direct_customer_id
      and d.owner_user_id = v_actor
    for update;
    if v_direct.id is null or not v_direct.is_active
       or not v_direct.marketing_consent_confirmed
       or v_direct.marketing_consent_at is null
       or v_direct.mobile_phone = ''
       or v_direct.mobile_phone_hash is distinct from v_phone_hash
       or v_direct.updated_at is distinct from p_direct_customer_updated_at then
        return query select false, 'TARGET_CHANGED',
            '업체 연락처 또는 수신동의 상태가 변경되어 발송을 중단했습니다.',
            null::uuid, null::text, false, null::uuid, null::timestamptz;
        return;
    end if;

    if exists (
        select 1 from public.oasis_company_kakao_contact_controls c
        where c.status in ('opted_out', 'admin_blocked')
          and c.recipient_phone_hash = v_phone_hash
    ) then
        return query select false, 'DO_NOT_CONTACT',
            '수신거부 또는 연락제외 업체라 발송할 수 없습니다.',
            null::uuid, null::text, false, null::uuid, null::timestamptz;
        return;
    end if;

    update public.oasis_direct_customer_outreach_outbox o
    set status = 'delivery_unknown', safe_result_code = 'STALE_DISPATCH',
        finalized_at = now(), unknown_at = now(), updated_at = now()
    where o.status = 'dispatching'
      and o.dispatch_started_at < now() - interval '10 minutes';

    if exists (
        select 1 from public.oasis_direct_customer_outreach_outbox o
        where o.recipient_hmac = v_recipient_hmac
          and o.channel = v_channel
          and (
              o.status in ('reserved', 'dispatching', 'delivery_unknown')
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
          )
    ) then
        return query select false, 'DUPLICATE_OUTREACH',
            '같은 연락처와 채널의 요청이 처리 중이거나 최근 처리되었습니다.',
            null::uuid, null::text, false, null::uuid, null::timestamptz;
        return;
    end if;

    insert into public.oasis_direct_customer_outreach_outbox (
        requested_by_user_id, request_id, content_hmac, recipient_hmac,
        recipient_phone_hash, direct_customer_id,
        direct_customer_updated_at, channel
    ) values (
        v_actor, v_request, v_content_hmac, v_recipient_hmac,
        v_phone_hash, p_direct_customer_id,
        p_direct_customer_updated_at, v_channel
    ) returning * into v_saved;

    return query select true, 'RESERVED',
        '발송 요청을 안전하게 예약했습니다.',
        v_saved.id, v_saved.status, true, v_saved.reservation_token,
        v_saved.reserved_at;
end;
$$;


create or replace function public.oasis_begin_direct_customer_outreach(
    p_current_user_id text,
    p_outbox_id uuid,
    p_reservation_token uuid,
    p_recipient_hmac text,
    p_recipient_phone_hash text
)
returns table (
    success boolean, code text, message text, outbox_id uuid,
    status text, dispatch_started boolean
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
    v_outbox public.oasis_direct_customer_outreach_outbox%rowtype;
    v_direct public.oasis_direct_sales_customers%rowtype;
begin
    if not public.oasis_sales_actor_is_active(v_actor) then
        raise exception using errcode = '42501', message = 'PERMISSION_DENIED';
    end if;
    select o.* into v_outbox
    from public.oasis_direct_customer_outreach_outbox o
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
        return query select false, upper(v_outbox.status),
            '이미 처리된 요청이라 다시 발송하지 않았습니다.',
            v_outbox.id, v_outbox.status, false;
        return;
    end if;
    if v_outbox.reserved_at < now() - interval '10 minutes' then
        update public.oasis_direct_customer_outreach_outbox
        set status = 'cancelled_stale', safe_result_code = 'RESERVATION_EXPIRED',
            finalized_at = now(), updated_at = now()
        where id = v_outbox.id;
        return query select false, 'RESERVATION_EXPIRED',
            '발송 예약 시간이 지나 새 요청이 필요합니다.',
            v_outbox.id, 'cancelled_stale', false;
        return;
    end if;
    if v_outbox.recipient_hmac is distinct from v_recipient_hmac
       or v_outbox.recipient_phone_hash is distinct from v_phone_hash then
        return query select false, 'RECIPIENT_BINDING_CHANGED',
            '발송 수신처 확인정보가 변경되어 중단했습니다.',
            v_outbox.id, v_outbox.status, false;
        return;
    end if;

    select d.* into v_direct
    from public.oasis_direct_sales_customers d
    where d.id = v_outbox.direct_customer_id
      and d.owner_user_id = v_actor
    for update;
    if v_direct.id is null or not v_direct.is_active
       or not v_direct.marketing_consent_confirmed
       or v_direct.mobile_phone = ''
       or v_direct.mobile_phone_hash is distinct from v_phone_hash
       or v_direct.updated_at is distinct from v_outbox.direct_customer_updated_at then
        update public.oasis_direct_customer_outreach_outbox
        set status = 'cancelled_changed', safe_result_code = 'TARGET_CHANGED',
            finalized_at = now(), updated_at = now()
        where id = v_outbox.id;
        return query select false, 'TARGET_CHANGED',
            '업체 연락처 또는 수신동의 상태가 변경되어 발송을 취소했습니다.',
            v_outbox.id, 'cancelled_changed', false;
        return;
    end if;
    if exists (
        select 1 from public.oasis_company_kakao_contact_controls c
        where c.status in ('opted_out', 'admin_blocked')
          and c.recipient_phone_hash = v_phone_hash
    ) then
        update public.oasis_direct_customer_outreach_outbox
        set status = 'cancelled_dnc', safe_result_code = 'DO_NOT_CONTACT',
            finalized_at = now(), updated_at = now()
        where id = v_outbox.id;
        return query select false, 'DNC_CANCELLED',
            '발송 직전 수신거부가 확인되어 자동 취소했습니다.',
            v_outbox.id, 'cancelled_dnc', false;
        return;
    end if;

    update public.oasis_direct_customer_outreach_outbox
    set status = 'dispatching', dispatch_started_at = now(), updated_at = now()
    where id = v_outbox.id
    returning * into v_outbox;
    return query select true, 'DISPATCH_STARTED',
        '발송 직전 안전 확인을 완료했습니다.',
        v_outbox.id, v_outbox.status, true;
end;
$$;


create or replace function public.oasis_finalize_direct_customer_outreach(
    p_current_user_id text,
    p_outbox_id uuid,
    p_reservation_token uuid,
    p_status text,
    p_safe_result_code text default ''
)
returns table (
    success boolean, code text, message text, outbox_id uuid,
    status text, finalized_at timestamptz
)
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
    v_actor text := lower(btrim(coalesce(p_current_user_id, '')));
    v_status text := lower(btrim(coalesce(p_status, '')));
    v_code text := left(pg_catalog.regexp_replace(
        upper(coalesce(p_safe_result_code, '')), '[^A-Z0-9_-]', '_', 'g'
    ), 80);
    v_outbox public.oasis_direct_customer_outreach_outbox%rowtype;
begin
    if not public.oasis_sales_actor_is_active(v_actor) then
        raise exception using errcode = '42501', message = 'PERMISSION_DENIED';
    end if;
    if v_status not in ('provider_accepted', 'provider_rejected', 'delivery_unknown') then
        return query select false, 'INVALID_STATUS',
            '발송 결과 상태를 확인할 수 없습니다.',
            p_outbox_id, null::text, null::timestamptz;
        return;
    end if;
    select o.* into v_outbox
    from public.oasis_direct_customer_outreach_outbox o
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
            '이미 종료된 요청이라 결과를 변경하지 않았습니다.',
            v_outbox.id, v_outbox.status, v_outbox.finalized_at;
        return;
    end if;
    update public.oasis_direct_customer_outreach_outbox
    set status = v_status,
        safe_result_code = coalesce(nullif(v_code, ''), upper(v_status)),
        finalized_at = now(),
        unknown_at = case when v_status = 'delivery_unknown' then now() else unknown_at end,
        updated_at = now()
    where id = v_outbox.id
    returning * into v_outbox;
    return query select true, 'FINALIZED',
        '발송 결과를 자동 이력에 저장했습니다.',
        v_outbox.id, v_outbox.status, v_outbox.finalized_at;
end;
$$;


create or replace function public.oasis_list_direct_customer_outreach_history(
    p_current_user_id text,
    p_direct_customer_id uuid,
    p_limit integer default 100
)
returns table (
    outbox_id uuid, channel text, status text, safe_result_code text,
    reserved_at timestamptz, dispatch_started_at timestamptz,
    finalized_at timestamptz
)
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
    v_actor text := lower(btrim(coalesce(p_current_user_id, '')));
begin
    if not public.oasis_sales_actor_is_active(v_actor) then
        raise exception using errcode = '42501', message = 'PERMISSION_DENIED';
    end if;
    if not exists (
        select 1 from public.oasis_direct_sales_customers d
        where d.id = p_direct_customer_id and d.owner_user_id = v_actor
    ) then
        return;
    end if;
    return query
    select o.id, o.channel, o.status, o.safe_result_code,
        o.reserved_at, o.dispatch_started_at, o.finalized_at
    from public.oasis_direct_customer_outreach_outbox o
    where o.requested_by_user_id = v_actor
      and o.direct_customer_id = p_direct_customer_id
    order by o.reserved_at desc
    limit greatest(1, least(coalesce(p_limit, 100), 500));
end;
$$;


alter table public.oasis_direct_sales_customers enable row level security;
alter table public.oasis_direct_sales_customers force row level security;
alter table public.oasis_direct_sales_claim_conflicts enable row level security;
alter table public.oasis_direct_sales_claim_conflicts force row level security;
alter table public.oasis_direct_customer_outreach_outbox enable row level security;
alter table public.oasis_direct_customer_outreach_outbox force row level security;

revoke all on table public.oasis_direct_sales_customers
    from PUBLIC, anon, authenticated, service_role;
revoke all on table public.oasis_direct_sales_claim_conflicts
    from PUBLIC, anon, authenticated, service_role;
revoke all on table public.oasis_direct_customer_outreach_outbox
    from PUBLIC, anon, authenticated, service_role;

revoke all on function public.oasis_register_direct_sales_customer(
    text, text, text, text, text, text, text, text,
    text, integer, text, text, boolean, text, text
) from PUBLIC, anon, authenticated, service_role;
grant execute on function public.oasis_register_direct_sales_customer(
    text, text, text, text, text, text, text, text,
    text, integer, text, text, boolean, text, text
) to service_role;

revoke all on function public.oasis_get_direct_sales_customer_summary(text)
    from PUBLIC, anon, authenticated, service_role;
grant execute on function public.oasis_get_direct_sales_customer_summary(text)
    to service_role;

revoke all on function public.oasis_list_direct_sales_customers(
    text, text, uuid, integer, integer
) from PUBLIC, anon, authenticated, service_role;
grant execute on function public.oasis_list_direct_sales_customers(
    text, text, uuid, integer, integer
) to service_role;

revoke all on function public.oasis_reserve_direct_customer_outreach(
    text, text, text, text, text, uuid, timestamptz, text
) from PUBLIC, anon, authenticated, service_role;
grant execute on function public.oasis_reserve_direct_customer_outreach(
    text, text, text, text, text, uuid, timestamptz, text
) to service_role;

revoke all on function public.oasis_begin_direct_customer_outreach(
    text, uuid, uuid, text, text
) from PUBLIC, anon, authenticated, service_role;
grant execute on function public.oasis_begin_direct_customer_outreach(
    text, uuid, uuid, text, text
) to service_role;

revoke all on function public.oasis_finalize_direct_customer_outreach(
    text, uuid, uuid, text, text
) from PUBLIC, anon, authenticated, service_role;
grant execute on function public.oasis_finalize_direct_customer_outreach(
    text, uuid, uuid, text, text
) to service_role;

revoke all on function public.oasis_list_direct_customer_outreach_history(
    text, uuid, integer
) from PUBLIC, anon, authenticated, service_role;
grant execute on function public.oasis_list_direct_customer_outreach_history(
    text, uuid, integer
) to service_role;

comment on table public.oasis_direct_sales_customers is
    'Employee-owned direct registrations; excluded from central DB allocation and return limits.';
comment on table public.oasis_direct_customer_outreach_outbox is
    'Metadata-only no-auto-retry ledger for direct customer SMS/Kakao outreach.';
