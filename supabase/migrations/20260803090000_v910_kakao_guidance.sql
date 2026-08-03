-- OASIS CRM v9.10.0 - 개인사업자 카카오톡 검토신청 안내
-- 공개 DB 휴대전화는 안내 발송 전용이다. 원문 번호는 이 스키마에 저장하지 않으며,
-- 고객이 검토신청 화면에서 직접 입력한 인증정보와 비교·병합하지 않는다.

begin;

-- Encrypted customer self-input must be purged by the database even when the
-- Railway worker is stopped.  Hosted Supabase supports pg_cron; fail the
-- migration rather than silently deploying without an independent sweeper.
create extension if not exists pg_cron;

-- 기존 연락제외 플래그와 별도로 명시적인 수신거부 시각을 보존한다.
-- 재실행 가능한 변경이며, 안내 자격검사는 두 값을 모두 차단한다.
alter table public.oasis_prospect_contacts
    add column if not exists opt_out_at timestamptz;

create table if not exists public.oasis_company_kakao_guidance_settings (
    singleton boolean primary key default true check (singleton),
    send_enabled boolean not null default false,
    daily_limit integer not null default 100 check (daily_limit between 0 and 100000),
    changed_by_user_id text,
    change_reason text not null default '',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

insert into public.oasis_company_kakao_guidance_settings (singleton)
values (true)
on conflict (singleton) do nothing;

create table if not exists public.oasis_company_kakao_guidance_messages (
    id uuid primary key default extensions.gen_random_uuid(),
    company_id uuid references public.oasis_prospect_companies(id) on delete set null,
    company_uid text not null,
    assignment_id uuid references public.oasis_company_sales_assignments(id) on delete set null,
    recipient_contact_id uuid,
    recipient_contact_updated_at timestamptz,
    recipient_phone_hash text not null,
    message_type text not null,
    template_key text not null,
    template_version text not null default 'v1',
    delivery_mode text not null default 'mock',
    status text not null default 'queued',
    requested_by_user_id text not null,
    provider_message_id text not null default '',
    provider_group_id text not null default '',
    secure_review_link_id uuid,
    idempotency_key text not null,
    dedupe_until timestamptz,
    sent_at timestamptz,
    delivered_at timestamptz,
    failure_code text not null default '',
    failure_summary text not null default '',
    cancelled_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint oasis_company_kakao_guidance_messages_uid_check
        check (public.oasis_is_valid_company_uid(company_uid)),
    constraint oasis_company_kakao_guidance_messages_phone_hash_check
        check (recipient_phone_hash ~ '^[0-9a-f]{64}$'),
    constraint oasis_company_kakao_guidance_messages_type_check
        check (message_type in ('employment_support', 'policy_funding', 'tax_credit')),
    constraint oasis_company_kakao_guidance_messages_template_check
        check (template_key = message_type and template_version ~ '^[A-Za-z0-9._-]{1,40}$'),
    constraint oasis_company_kakao_guidance_messages_mode_check
        check (delivery_mode in ('mock', 'live')),
    constraint oasis_company_kakao_guidance_messages_live_contact_check
        check (delivery_mode <> 'live' or recipient_contact_id is not null),
    constraint oasis_company_kakao_guidance_messages_live_contact_version_check
        check (
            delivery_mode <> 'live'
            or recipient_contact_updated_at is not null
        ),
    constraint oasis_company_kakao_guidance_messages_status_check
        check (status in ('queued', 'sending', 'sent', 'delivered', 'failed', 'blocked', 'cancelled', 'simulated')),
    constraint oasis_company_kakao_guidance_messages_actor_check
        check (requested_by_user_id = lower(btrim(requested_by_user_id)) and length(requested_by_user_id) between 1 and 200),
    constraint oasis_company_kakao_guidance_messages_idempotency_check
        check (length(btrim(idempotency_key)) between 1 and 200),
    constraint oasis_company_kakao_guidance_messages_idempotency_unique
        unique (requested_by_user_id, idempotency_key)
);

-- Replay-safe contract upgrade for a database that briefly received an older
-- draft of this migration.  Live sends must be bound to one canonical public
-- contact row; an arbitrary caller-supplied phone/hash is never sufficient.
alter table public.oasis_company_kakao_guidance_messages
    add column if not exists recipient_contact_id uuid;
alter table public.oasis_company_kakao_guidance_messages
    add column if not exists recipient_contact_updated_at timestamptz;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'oasis_company_kakao_guidance_messages_contact_fkey'
          and conrelid = 'public.oasis_company_kakao_guidance_messages'::regclass
    ) then
        alter table public.oasis_company_kakao_guidance_messages
            add constraint oasis_company_kakao_guidance_messages_contact_fkey
            foreign key (recipient_contact_id)
            references public.oasis_prospect_contacts(id)
            on delete restrict;
    end if;
    if not exists (
        select 1
        from pg_constraint
        where conname = 'oasis_company_kakao_guidance_messages_live_contact_check'
          and conrelid = 'public.oasis_company_kakao_guidance_messages'::regclass
    ) then
        alter table public.oasis_company_kakao_guidance_messages
            add constraint oasis_company_kakao_guidance_messages_live_contact_check
            check (delivery_mode <> 'live' or recipient_contact_id is not null)
            not valid;
    end if;
    if not exists (
        select 1
        from pg_constraint
        where conname = 'oasis_company_kakao_guidance_messages_live_contact_version_check'
          and conrelid = 'public.oasis_company_kakao_guidance_messages'::regclass
    ) then
        alter table public.oasis_company_kakao_guidance_messages
            add constraint oasis_company_kakao_guidance_messages_live_contact_version_check
            check (
                delivery_mode <> 'live'
                or recipient_contact_updated_at is not null
            )
            not valid;
    end if;
end;
$$;

-- An older draft allowed live rows to predate the canonical-contact binding.
-- Keep terminal history intact, but fail any still-sendable legacy row closed.
-- The validated constraints below require the binding for every state from
-- which the provider can still be invoked.
update public.oasis_company_kakao_guidance_messages m
set status = 'blocked',
    failure_code = 'CANONICAL_CONTACT_BINDING_REQUIRED',
    failure_summary = '기존 안내의 발송 연락처 결속을 확인할 수 없어 발송을 차단했습니다.',
    updated_at = clock_timestamp()
where m.delivery_mode = 'live'
  and m.status in ('queued', 'sending')
  and (
      m.recipient_contact_id is null
      or m.recipient_contact_updated_at is null
  );

alter table public.oasis_company_kakao_guidance_messages
    drop constraint if exists oasis_company_kakao_guidance_messages_live_contact_check;
alter table public.oasis_company_kakao_guidance_messages
    add constraint oasis_company_kakao_guidance_messages_live_contact_check
    check (
        delivery_mode <> 'live'
        or status not in ('queued', 'sending')
        or recipient_contact_id is not null
    ) not valid;
alter table public.oasis_company_kakao_guidance_messages
    validate constraint oasis_company_kakao_guidance_messages_live_contact_check;

alter table public.oasis_company_kakao_guidance_messages
    drop constraint if exists oasis_company_kakao_guidance_messages_live_contact_version_check;
alter table public.oasis_company_kakao_guidance_messages
    add constraint oasis_company_kakao_guidance_messages_live_contact_version_check
    check (
        delivery_mode <> 'live'
        or status not in ('queued', 'sending')
        or recipient_contact_updated_at is not null
    ) not valid;
alter table public.oasis_company_kakao_guidance_messages
    validate constraint oasis_company_kakao_guidance_messages_live_contact_version_check;

create table if not exists public.oasis_company_kakao_contact_controls (
    id uuid primary key default extensions.gen_random_uuid(),
    company_uid text not null unique,
    recipient_phone_hash text,
    status text not null default 'allowed',
    reason text not null default '',
    set_by_user_id text,
    set_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint oasis_company_kakao_contact_controls_uid_check
        check (public.oasis_is_valid_company_uid(company_uid)),
    constraint oasis_company_kakao_contact_controls_hash_check
        check (recipient_phone_hash is null or recipient_phone_hash ~ '^[0-9a-f]{64}$'),
    constraint oasis_company_kakao_contact_controls_status_check
        check (status in ('allowed', 'opted_out', 'admin_blocked'))
);

create table if not exists public.oasis_company_kakao_guidance_history (
    id bigint generated by default as identity primary key,
    guidance_message_id uuid references public.oasis_company_kakao_guidance_messages(id) on delete restrict,
    company_uid text,
    actor_user_id text,
    action text not null,
    previous_status text,
    new_status text,
    safe_summary jsonb not null default '{}'::jsonb,
    session_fingerprint text,
    created_at timestamptz not null default now(),
    constraint oasis_company_kakao_guidance_history_summary_object_check
        check (jsonb_typeof(safe_summary) = 'object')
);

create table if not exists public.oasis_company_kakao_followup_outbox (
    id uuid primary key default extensions.gen_random_uuid(),
    guidance_message_id uuid not null unique
        references public.oasis_company_kakao_guidance_messages(id) on delete restrict,
    company_uid text not null,
    assigned_user_id text not null,
    due_at timestamptz not null,
    status text not null default 'pending',
    idempotency_key text not null unique,
    attempt_count integer not null default 0 check (attempt_count >= 0),
    max_attempts integer not null default 8 check (max_attempts between 1 and 100),
    next_retry_at timestamptz not null default now(),
    lease_owner text,
    lease_until timestamptz,
    task_id uuid,
    safe_error_code text not null default '',
    safe_error_summary text not null default '',
    completed_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint oasis_company_kakao_followup_outbox_uid_check
        check (public.oasis_is_valid_company_uid(company_uid)),
    constraint oasis_company_kakao_followup_outbox_status_check
        check (status in ('pending', 'running', 'retry', 'created', 'cancelled', 'failed', 'dead_letter')),
    constraint oasis_company_kakao_followup_outbox_lease_check
        check ((lease_owner is null and lease_until is null) or (nullif(btrim(lease_owner), '') is not null and lease_until is not null)),
    constraint oasis_company_kakao_followup_outbox_error_code_check
        check (safe_error_code = '' or safe_error_code ~ '^[A-Z0-9_-]{1,80}$')
);

-- Guidance delivery outbox rows carry only a non-PII foreign key to the
-- reserved guidance message.  This binds the exact leased row to the
-- canonical contact snapshot that send-ready revalidates without exposing
-- either the public delivery number or the customer's authentication number.
alter table public.oasis_claim_remote_outbox
    add column if not exists guidance_message_id uuid;
alter table public.oasis_claim_remote_outbox
    add column if not exists guidance_dispatch_started_at timestamptz;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'oasis_claim_remote_outbox_guidance_message_fkey'
          and conrelid = 'public.oasis_claim_remote_outbox'::regclass
    ) then
        alter table public.oasis_claim_remote_outbox
            add constraint oasis_claim_remote_outbox_guidance_message_fkey
            foreign key (guidance_message_id)
            references public.oasis_company_kakao_guidance_messages(id)
            on delete restrict;
    end if;
end;
$$;

-- Replay upgrade for an earlier draft: recover only an exact, existing UUID
-- encoded by the established guidance idempotency key.  Any remaining active
-- unbound row is cancelled and its encrypted destination is erased.
update public.oasis_claim_remote_outbox o
set guidance_message_id = substring(o.idempotency_key from 10)::uuid
where upper(o.event_type) like 'GUIDANCE\_%' escape '\'
  and o.guidance_message_id is null
  and o.idempotency_key ~ '^guidance:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$'
  and exists (
      select 1
      from public.oasis_company_kakao_guidance_messages m
      where m.id = substring(o.idempotency_key from 10)::uuid
        and m.secure_review_link_id = o.invite_id
        and m.requested_by_user_id = o.owner_user_id
  );

update public.oasis_claim_remote_outbox o
set status = 'cancelled',
    secure_payload_ciphertext = '',
    lease_owner = null,
    lease_until = null,
    safe_error_code = 'GUIDANCE_BINDING_REQUIRED',
    updated_at = clock_timestamp()
where upper(o.event_type) like 'GUIDANCE\_%' escape '\'
  and o.status in ('pending', 'running', 'retry')
  and (
      o.guidance_message_id is null
      or not exists (
          select 1
          from public.oasis_company_kakao_guidance_messages m
          where m.id = o.guidance_message_id
            and m.delivery_mode = 'live'
            and m.status in ('queued', 'sending')
            and m.recipient_contact_id is not null
            and m.recipient_contact_updated_at is not null
      )
  );

alter table public.oasis_claim_remote_outbox
    drop constraint if exists oasis_claim_remote_outbox_guidance_binding_check;
alter table public.oasis_claim_remote_outbox
    add constraint oasis_claim_remote_outbox_guidance_binding_check
    check (
        (
            upper(event_type) like 'GUIDANCE\_%' escape '\'
            and (
                guidance_message_id is not null
                or status in ('sent', 'delivered', 'failed', 'expired', 'cancelled')
            )
        )
        or (
            upper(event_type) not like 'GUIDANCE\_%' escape '\'
            and guidance_message_id is null
        )
    );

-- Once a live guidance row crosses the provider-call boundary, its public
-- delivery number is erased before the network call.  A crashed worker can no
-- longer re-lease a decryptable destination and accidentally send twice.
alter table public.oasis_claim_remote_outbox
    drop constraint if exists oasis_claim_remote_outbox_ciphertext_lifecycle;
alter table public.oasis_claim_remote_outbox
    add constraint oasis_claim_remote_outbox_ciphertext_lifecycle
    check (
        (
            status in (
                'sent', 'delivered', 'failed', 'expired', 'cancelled'
            )
            and secure_payload_ciphertext = ''
        )
        or (
            status in ('pending', 'running', 'retry')
            and length(secure_payload_ciphertext) >= 40
        )
        or (
            status = 'running'
            and guidance_message_id is not null
            and guidance_dispatch_started_at is not null
            and secure_payload_ciphertext = ''
        )
    );

alter table public.oasis_claim_remote_outbox
    drop constraint if exists oasis_claim_remote_outbox_guidance_dispatch_check;
alter table public.oasis_claim_remote_outbox
    add constraint oasis_claim_remote_outbox_guidance_dispatch_check
    check (
        guidance_dispatch_started_at is null
        or (
            guidance_message_id is not null
            and upper(event_type) like 'GUIDANCE\_%' escape '\'
            and status in (
                'running', 'sent', 'delivered', 'failed', 'expired', 'cancelled'
            )
        )
    );

-- The representative-owned phone in NEXT_AUTH is an authentication channel,
-- never a durable notification destination.  Normalize an earlier draft and
-- make the ten-minute maximum independently enforceable by the database.
update public.oasis_claim_remote_outbox o
set expires_at = least(o.expires_at, o.created_at + interval '10 minutes')
where o.event_type = 'NEXT_AUTH'
  and o.expires_at > o.created_at + interval '10 minutes';

update public.oasis_claim_remote_outbox o
set status = 'expired',
    secure_payload_ciphertext = '',
    lease_owner = null,
    lease_until = null,
    safe_error_code = 'MESSAGE_TTL_EXPIRED',
    updated_at = clock_timestamp()
where o.event_type = 'NEXT_AUTH'
  and o.status in ('pending', 'running', 'retry')
  and o.expires_at <= clock_timestamp();

alter table public.oasis_claim_remote_outbox
    drop constraint if exists oasis_claim_remote_outbox_next_auth_ttl;
alter table public.oasis_claim_remote_outbox
    add constraint oasis_claim_remote_outbox_next_auth_ttl
    check (
        event_type <> 'NEXT_AUTH'
        or expires_at <= created_at + interval '10 minutes'
    );

-- Customer-entered simple-auth data is held only in the encrypted remote-job
-- payload.  A separate idle deadline keeps that payload for at most ten
-- minutes without an authenticated worker heartbeat, while hard_expires_at
-- remains the absolute forty-five-minute ceiling supplied by the service.
alter table public.oasis_claim_remote_jobs
    add column if not exists sensitive_expires_at timestamptz;

-- Do not backfill an already-expired encrypted payload with a fresh idle
-- window.  Purge it before assigning deadlines to the remaining active jobs.
update public.oasis_claim_remote_jobs j
set status = 'expired',
    secure_payload_ciphertext = '',
    sensitive_expires_at = null,
    lease_owner = null,
    lease_until = null,
    safe_error_code = case
        when j.hard_expires_at <= clock_timestamp()
            then 'JOB_TTL_EXPIRED'
        else 'SENSITIVE_TTL_EXPIRED'
    end,
    updated_at = clock_timestamp()
where j.status in ('queued', 'running', 'waiting', 'retry')
  and (
      j.hard_expires_at <= clock_timestamp()
      or (
          j.sensitive_expires_at is not null
          and j.sensitive_expires_at <= clock_timestamp()
      )
  );

update public.oasis_claim_remote_jobs j
set sensitive_expires_at = case
        when j.status in ('complete', 'partial', 'failed', 'expired', 'cancelled')
            then null
        else least(j.hard_expires_at, clock_timestamp() + interval '10 minutes')
    end
where j.sensitive_expires_at is null
   or j.sensitive_expires_at > j.hard_expires_at
   or (
       j.status in ('queued', 'running', 'waiting', 'retry')
       and j.sensitive_expires_at <= j.created_at
   )
   or (
       j.status in ('complete', 'partial', 'failed', 'expired', 'cancelled')
       and j.sensitive_expires_at is not null
   );

-- The database, not only the Railway caller, owns the absolute collection
-- ceiling.  Normalize any legacy row before enforcing the invariant.
update public.oasis_claim_remote_jobs j
set hard_expires_at = j.created_at + interval '45 minutes',
    sensitive_expires_at = case
        when j.sensitive_expires_at is null then null
        else least(j.sensitive_expires_at, j.created_at + interval '45 minutes')
    end,
    lease_until = case
        when j.lease_until is null then null
        else least(j.lease_until, j.created_at + interval '45 minutes')
    end,
    updated_at = clock_timestamp()
where j.hard_expires_at > j.created_at + interval '45 minutes';

-- A reserved job may legitimately start its ten-minute authentication window
-- long after the invite row was created.  Normalize the activity timestamp and
-- deadline together before validating the idle ceiling.
update public.oasis_claim_remote_jobs j
set sensitive_expires_at = least(
        j.hard_expires_at,
        clock_timestamp() + interval '10 minutes'
    ),
    updated_at = clock_timestamp()
where j.status in ('queued', 'running', 'waiting', 'retry')
  and j.stage <> 'collecting'
  and left(j.stage, 11) <> 'collection_'
  and j.sensitive_expires_at > j.updated_at + interval '10 minutes';

update public.oasis_claim_remote_jobs j
set status = 'expired',
    secure_payload_ciphertext = '',
    sensitive_expires_at = null,
    lease_owner = null,
    lease_until = null,
    safe_error_code = case
        when j.hard_expires_at <= clock_timestamp()
            then 'JOB_TTL_EXPIRED'
        else 'SENSITIVE_TTL_EXPIRED'
    end,
    updated_at = clock_timestamp()
where j.status in ('queued', 'running', 'waiting', 'retry')
  and (
      j.hard_expires_at <= clock_timestamp()
      or j.sensitive_expires_at is null
      or j.sensitive_expires_at <= clock_timestamp()
  );

alter table public.oasis_claim_remote_jobs
    drop constraint if exists oasis_claim_remote_jobs_hard_expiry_ceiling;
alter table public.oasis_claim_remote_jobs
    add constraint oasis_claim_remote_jobs_hard_expiry_ceiling
    check (hard_expires_at <= created_at + interval '45 minutes');

alter table public.oasis_claim_remote_jobs
    drop constraint if exists oasis_claim_remote_jobs_sensitive_expiry_lifecycle;
alter table public.oasis_claim_remote_jobs
    add constraint oasis_claim_remote_jobs_sensitive_expiry_lifecycle
    check (
        (
            status in ('queued', 'running', 'waiting', 'retry')
            and sensitive_expires_at is not null
            and sensitive_expires_at > created_at
            and sensitive_expires_at <= hard_expires_at
        )
        or (
            status in ('complete', 'partial', 'failed', 'expired', 'cancelled')
            and sensitive_expires_at is null
        )
    );

alter table public.oasis_claim_remote_jobs
    drop constraint if exists oasis_claim_remote_jobs_auth_idle_ceiling;
alter table public.oasis_claim_remote_jobs
    add constraint oasis_claim_remote_jobs_auth_idle_ceiling
    check (
        status in ('complete', 'partial', 'failed', 'expired', 'cancelled')
        or stage = 'collecting'
        or left(stage, 11) = 'collection_'
        or sensitive_expires_at <= updated_at + interval '10 minutes'
    );

create index if not exists oasis_claim_remote_jobs_sensitive_expiry_idx
    on public.oasis_claim_remote_jobs (sensitive_expires_at)
    where status in ('queued', 'running', 'waiting', 'retry');
create index if not exists oasis_claim_remote_jobs_invite_owner_idx
    on public.oasis_claim_remote_jobs (invite_id, owner_user_id);
create index if not exists oasis_claim_remote_outbox_invite_owner_idx
    on public.oasis_claim_remote_outbox (invite_id, owner_user_id)
    where invite_id is not null;

create index if not exists oasis_guidance_message_company_type_created_idx
    on public.oasis_company_kakao_guidance_messages (company_uid, message_type, created_at desc);
create index if not exists oasis_guidance_message_active_idx
    on public.oasis_company_kakao_guidance_messages (company_uid, message_type, status, updated_at desc)
    where status in ('queued', 'sending', 'sent', 'delivered');
create index if not exists oasis_guidance_message_owner_idx
    on public.oasis_company_kakao_guidance_messages (requested_by_user_id, created_at desc);
create index if not exists oasis_guidance_message_company_fk_idx
    on public.oasis_company_kakao_guidance_messages (company_id)
    where company_id is not null;
create index if not exists oasis_guidance_message_assignment_fk_idx
    on public.oasis_company_kakao_guidance_messages (assignment_id)
    where assignment_id is not null;
create index if not exists oasis_guidance_message_contact_fk_idx
    on public.oasis_company_kakao_guidance_messages (recipient_contact_id)
    where recipient_contact_id is not null;
create index if not exists oasis_guidance_message_status_idx
    on public.oasis_company_kakao_guidance_messages (status, created_at desc);
drop index if exists public.oasis_guidance_message_invite_idx;
create unique index oasis_guidance_message_invite_idx
    on public.oasis_company_kakao_guidance_messages (secure_review_link_id)
    where secure_review_link_id is not null;
create unique index if not exists oasis_guidance_message_provider_unique_idx
    on public.oasis_company_kakao_guidance_messages (provider_message_id)
    where provider_message_id <> '';
create index if not exists oasis_guidance_control_phone_idx
    on public.oasis_company_kakao_contact_controls (recipient_phone_hash, status)
    where recipient_phone_hash is not null;
create index if not exists oasis_guidance_history_message_idx
    on public.oasis_company_kakao_guidance_history (guidance_message_id, created_at desc);
create index if not exists oasis_guidance_followup_due_idx
    on public.oasis_company_kakao_followup_outbox (next_retry_at, due_at, created_at)
    where status in ('pending', 'running', 'retry');
create index if not exists oasis_guidance_followup_lease_idx
    on public.oasis_company_kakao_followup_outbox (lease_until)
    where status = 'running';
create unique index if not exists oasis_claim_remote_outbox_guidance_message_uidx
    on public.oasis_claim_remote_outbox (guidance_message_id)
    where guidance_message_id is not null;

-- 재실행 시에도 모의처리 상태 계약이 동일하게 유지되도록 명시적으로
-- 교체한다. simulated는 성공 발송·7일 중복·후속업무 집계에 포함하지 않는다.
alter table public.oasis_company_kakao_guidance_messages
    drop constraint if exists oasis_company_kakao_guidance_messages_status_check;
alter table public.oasis_company_kakao_guidance_messages
    add constraint oasis_company_kakao_guidance_messages_status_check
    check (status in ('queued', 'sending', 'sent', 'delivered', 'failed', 'blocked', 'cancelled', 'simulated'));

create or replace function public.oasis_company_kakao_touch_updated_at()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
    new.updated_at := clock_timestamp();
    return new;
end;
$$;

-- 아래 helper들은 migration을 빈 환경에 순서대로 적용할 때 caller가
-- 아직 존재하지 않는 함수를 참조하지 않도록 모든 caller보다 먼저 정의한다.
create or replace function public.oasis_company_kakao_add_business_days(p_start timestamptz, p_days integer)
returns timestamptz
language plpgsql
stable
set search_path = ''
as $$
declare
    v_result timestamptz := coalesce(p_start, now());
    v_remaining integer := greatest(coalesce(p_days, 0), 0);
begin
    while v_remaining > 0 loop
        v_result := v_result + interval '1 day';
        if extract(isodow from v_result) between 1 and 5 then
            v_remaining := v_remaining - 1;
        end if;
    end loop;
    return v_result;
end;
$$;

create or replace function public.oasis_company_kakao_write_history(
    p_message_id uuid,
    p_company_uid text,
    p_actor_user_id text,
    p_action text,
    p_previous_status text,
    p_new_status text,
    p_safe_summary jsonb default '{}'::jsonb,
    p_session_id text default null
)
returns void
language sql
volatile
set search_path = public, pg_temp
as $$
    insert into public.oasis_company_kakao_guidance_history (
        guidance_message_id, company_uid, actor_user_id, action,
        previous_status, new_status, safe_summary, session_fingerprint
    ) values (
        p_message_id, nullif(btrim(p_company_uid), ''), nullif(btrim(p_actor_user_id), ''),
        left(coalesce(nullif(btrim(p_action), ''), 'unknown'), 80),
        nullif(btrim(p_previous_status), ''), nullif(btrim(p_new_status), ''),
        case when jsonb_typeof(coalesce(p_safe_summary, '{}'::jsonb)) = 'object'
             then coalesce(p_safe_summary, '{}'::jsonb) else '{}'::jsonb end,
        public.oasis_sales_session_fingerprint(p_session_id)
    );
$$;

-- Trusted delivery-number boundary.  The service asks for this row first and
-- uses the returned mobile only to build the encrypted provider outbox.  The
-- contact id is then supplied to reserve/final-send checks, so a phone copied
-- into a UI/company mapping cannot be substituted for another destination.
create or replace function public.oasis_resolve_company_kakao_guidance_mobile(
    p_current_user_id text,
    p_company_uid text,
    p_contact_id uuid default null
)
returns table (
    success boolean,
    code text,
    message text,
    company_id uuid,
    assignment_id uuid,
    contact_id uuid,
    mobile_phone text,
    contact_updated_at timestamptz
)
language plpgsql
stable
security definer
set search_path = public, pg_temp
as $$
declare
    v_actor text := lower(btrim(coalesce(p_current_user_id, '')));
    v_uid text := btrim(coalesce(p_company_uid, ''));
    v_assignment public.oasis_company_sales_assignments%rowtype;
    v_contact public.oasis_prospect_contacts%rowtype;
    v_mobile text;
begin
    if not public.oasis_sales_actor_is_active(v_actor) then
        raise exception using errcode = '42501', message = 'PERMISSION_DENIED';
    end if;

    select a.* into v_assignment
    from public.oasis_company_sales_assignments a
    where a.company_uid = v_uid;

    if v_assignment.id is null
       or v_assignment.assigned_user_id is distinct from v_actor
       or v_assignment.permanently_excluded
       or v_assignment.status in (
            'unassigned', 'wrong_number', 'closed', 'long_hold',
            'permanently_excluded'
       ) then
        return query select false, 'ASSIGNMENT_REQUIRED',
            '내 영업DB의 발송 가능한 업체 연락처를 확인해 주세요.',
            null::uuid, null::uuid, null::uuid, null::text,
            null::timestamptz;
        return;
    end if;

    if exists (
        select 1
        from public.oasis_company_kakao_contact_controls c
        where c.company_uid = v_uid
          and c.status in ('opted_out', 'admin_blocked')
    ) or exists (
        select 1
        from public.oasis_prospect_contacts c
        where c.prospect_id = v_assignment.company_id
          and (c.do_not_contact is true or c.opt_out_at is not null)
    ) then
        return query select false, 'DO_NOT_CONTACT',
            '수신거부 또는 연락제외 업체입니다.',
            v_assignment.company_id, v_assignment.id, null::uuid, null::text,
            null::timestamptz;
        return;
    end if;

    select c.* into v_contact
    from public.oasis_prospect_contacts c
    where c.prospect_id = v_assignment.company_id
      and (p_contact_id is null or c.id = p_contact_id)
      and c.contact_type in ('phone', 'mobile', 'mobile_phone')
      and c.do_not_contact is not true
      and c.opt_out_at is null
      and c.verification_status <> 'rejected'
      and regexp_replace(coalesce(c.contact_value, ''), '[^0-9]', '', 'g')
          ~ '^01(0[0-9]{8}|[16789][0-9]{7,8})$'
    order by
        case when c.id = p_contact_id then 0 else 1 end,
        c.is_primary desc,
        case c.verification_status
            when 'manual_verified' then 0
            when 'auto_verified' then 1
            else 2
        end,
        c.confidence desc,
        c.created_at asc,
        c.id asc
    limit 1;

    if v_contact.id is null then
        return query select false, 'CANONICAL_MOBILE_REQUIRED',
            '검증된 공개 휴대전화 연락처를 확인해 주세요.',
            v_assignment.company_id, v_assignment.id, null::uuid, null::text,
            null::timestamptz;
        return;
    end if;

    v_mobile := regexp_replace(v_contact.contact_value, '[^0-9]', '', 'g');
    return query select true, 'RESOLVED', '발송 연락처를 확인했습니다.',
        v_assignment.company_id, v_assignment.id, v_contact.id, v_mobile,
        v_contact.updated_at;
end;
$$;

create or replace function public.oasis_check_company_kakao_guidance_eligibility(
    p_current_user_id text,
    p_company_uid text,
    p_message_type text,
    p_recipient_phone_hash text,
    p_contact_id uuid default null
)
returns table (
    eligible boolean,
    code text,
    message text,
    assignment_id uuid,
    retry_at timestamptz,
    admin_enabled boolean,
    daily_limit integer
)
language plpgsql
stable
security definer
set search_path = public, pg_temp
as $$
declare
    v_actor text := lower(btrim(coalesce(p_current_user_id, '')));
    v_uid text := btrim(coalesce(p_company_uid, ''));
    v_type text := lower(btrim(coalesce(p_message_type, '')));
    v_hash text := lower(btrim(coalesce(p_recipient_phone_hash, '')));
    v_assignment public.oasis_company_sales_assignments%rowtype;
    v_retry timestamptz;
    v_enabled boolean;
    v_limit integer;
begin
    if not public.oasis_sales_actor_is_active(v_actor) then
        raise exception using errcode = '42501', message = 'PERMISSION_DENIED';
    end if;
    if not public.oasis_is_valid_company_uid(v_uid)
       or v_type not in ('employment_support', 'policy_funding', 'tax_credit')
       or v_hash !~ '^[0-9a-f]{64}$' then
        return query select false, 'INVALID_REQUEST', '발송 조건을 확인할 수 없습니다.', null::uuid, null::timestamptz, false, 0;
        return;
    end if;

    select s.send_enabled, s.daily_limit into v_enabled, v_limit
    from public.oasis_company_kakao_guidance_settings s where s.singleton;

    select a.* into v_assignment
    from public.oasis_company_sales_assignments a
    where a.company_uid = v_uid;

    if v_assignment.id is null or v_assignment.assigned_user_id is null then
        return query select false, 'ASSIGNMENT_REQUIRED', '내 영업DB에 배정된 업체만 안내할 수 있습니다.', null::uuid, null::timestamptz, coalesce(v_enabled, false), coalesce(v_limit, 0);
        return;
    end if;
    if v_assignment.assigned_user_id <> v_actor then
        return query select false, 'ASSIGNED_TO_OTHER', '다른 담당자에게 배정된 업체입니다.', v_assignment.id, null::timestamptz, coalesce(v_enabled, false), coalesce(v_limit, 0);
        return;
    end if;
    if v_assignment.permanently_excluded
       or v_assignment.status in ('unassigned', 'wrong_number', 'closed', 'long_hold', 'permanently_excluded') then
        return query select false, 'ASSIGNMENT_BLOCKED', '현재 배정 상태에서는 안내할 수 없습니다.', v_assignment.id, null::timestamptz, coalesce(v_enabled, false), coalesce(v_limit, 0);
        return;
    end if;

    -- 화면의 업체명 기반 추정값은 신뢰하지 않는다. 저장된 원천자료가
    -- 개인사업자로 명시된 업체만 서버에서 다시 허용한다.
    if not exists (
        select 1
        from public.oasis_prospect_companies p
        where p.id = v_assignment.company_id
          and lower(btrim(coalesce(p.source_data ->> 'business_type', '')))
              in ('individual', 'sole', 'sole_proprietor', '개인', '개인사업자')
    ) then
        return query select false, 'INDIVIDUAL_ONLY', '확인된 개인사업자만 안내할 수 있습니다.', v_assignment.id, null::timestamptz, coalesce(v_enabled, false), coalesce(v_limit, 0);
        return;
    end if;

    -- Live callers bind the send to one canonical contact id returned by the
    -- resolver.  A null id remains usable only for mock/legacy eligibility;
    -- reserve rejects it for live delivery below.
    if p_contact_id is not null and not exists (
        select 1
        from public.oasis_prospect_contacts c
        where c.prospect_id = v_assignment.company_id
          and c.id = p_contact_id
          and c.contact_type in ('phone', 'mobile', 'mobile_phone')
          and c.do_not_contact is not true
          and c.opt_out_at is null
          and c.verification_status <> 'rejected'
          and regexp_replace(coalesce(c.contact_value, ''), '[^0-9]', '', 'g')
              ~ '^01(0[0-9]{8}|[16789][0-9]{7,8})$'
    ) then
        return query select false, 'CANONICAL_MOBILE_MISMATCH', '발송 연락처가 현재 업체의 검증된 휴대전화와 일치하지 않습니다.', v_assignment.id, null::timestamptz, coalesce(v_enabled, false), coalesce(v_limit, 0);
        return;
    end if;
    if p_contact_id is null and not exists (
        select 1
        from public.oasis_prospect_contacts c
        where c.prospect_id = v_assignment.company_id
          and c.contact_type in ('phone', 'mobile', 'mobile_phone')
          and c.do_not_contact is not true
          and c.opt_out_at is null
          and c.verification_status <> 'rejected'
          and regexp_replace(coalesce(c.contact_value, ''), '[^0-9]', '', 'g')
              ~ '^01(0[0-9]{8}|[16789][0-9]{7,8})$'
    ) then
        return query select false, 'CANONICAL_MOBILE_REQUIRED', '검증된 공개 휴대전화 연락처가 필요합니다.', v_assignment.id, null::timestamptz, coalesce(v_enabled, false), coalesce(v_limit, 0);
        return;
    end if;

    if exists (
        select 1 from public.oasis_company_kakao_contact_controls c
        where (c.company_uid = v_uid or c.recipient_phone_hash = v_hash)
          and c.status in ('opted_out', 'admin_blocked')
    ) or exists (
        select 1 from public.oasis_prospect_contacts c
        where c.prospect_id = v_assignment.company_id
          and (c.do_not_contact is true or c.opt_out_at is not null)
    ) then
        return query select false, 'DO_NOT_CONTACT', '수신거부 또는 연락제외 업체입니다.', v_assignment.id, null::timestamptz, coalesce(v_enabled, false), coalesce(v_limit, 0);
        return;
    end if;

    if exists (
        select 1 from public.oasis_company_kakao_guidance_messages m
        where m.company_uid = v_uid and m.message_type = v_type
          and m.status in ('queued', 'sending')
    ) then
        return query select false, 'DUPLICATE_IN_PROGRESS', '같은 안내가 이미 처리 중입니다.', v_assignment.id, null::timestamptz, coalesce(v_enabled, false), coalesce(v_limit, 0);
        return;
    end if;

    select max(coalesce(m.sent_at, m.delivered_at, m.updated_at) + interval '7 days')
      into v_retry
    from public.oasis_company_kakao_guidance_messages m
    where m.company_uid = v_uid and m.message_type = v_type
      and m.status in ('sent', 'delivered')
      and coalesce(m.sent_at, m.delivered_at, m.updated_at) > now() - interval '7 days';
    if v_retry is not null then
        return query select false, 'DUPLICATE_WITHIN_7_DAYS', '최근 7일 이내 같은 안내가 발송되어 중복 발송할 수 없습니다.', v_assignment.id, v_retry, coalesce(v_enabled, false), coalesce(v_limit, 0);
        return;
    end if;

    return query select true, 'ELIGIBLE', '안내 발송이 가능합니다.', v_assignment.id, null::timestamptz, coalesce(v_enabled, false), coalesce(v_limit, 0);
end;
$$;

create or replace function public.oasis_cancel_company_kakao_guidance(
    p_current_user_id text,
    p_message_id uuid,
    p_opt_out boolean default false,
    p_reason text default 'user_cancelled'
)
returns table (success boolean, code text, message text, message_id uuid, status text)
language plpgsql
volatile
security definer
set search_path = public, pg_temp
as $$
declare
    v_actor text := lower(btrim(coalesce(p_current_user_id, '')));
    v_message public.oasis_company_kakao_guidance_messages%rowtype;
    v_previous_status text;
    v_reason_code text := coalesce(
        nullif(
            btrim(
                left(regexp_replace(lower(coalesce(p_reason, '')), '[^a-z0-9_-]+', '_', 'g'), 80),
                '_'
            ),
            ''
        ),
        'user_cancelled'
    );
begin
    -- Read and authorize without taking the guidance lock.  Opt-out writers
    -- must acquire the contact-control row first; its AFTER trigger then uses
    -- the single global control -> guidance -> delivery-outbox lock order.
    select m.* into v_message
    from public.oasis_company_kakao_guidance_messages m
    where m.id = p_message_id;
    if v_message.id is null then raise exception using message = 'MESSAGE_NOT_FOUND'; end if;
    if v_actor <> v_message.requested_by_user_id and not public.oasis_sales_actor_is_admin(v_actor) then
        raise exception using errcode = '42501', message = 'PERMISSION_DENIED';
    end if;
    v_previous_status := v_message.status;

    if coalesce(p_opt_out, false) then
        insert into public.oasis_company_kakao_contact_controls (
            company_uid, recipient_phone_hash, status, reason,
            set_by_user_id, set_at
        ) values (
            v_message.company_uid, v_message.recipient_phone_hash,
            'opted_out', v_reason_code, v_actor, now()
        ) on conflict (company_uid) do update set
            recipient_phone_hash = excluded.recipient_phone_hash,
            status = 'opted_out', reason = excluded.reason,
            set_by_user_id = excluded.set_by_user_id, set_at = now();
    end if;

    select m.* into v_message
    from public.oasis_company_kakao_guidance_messages m
    where m.id = p_message_id
    for update;
    if v_message.id is null then raise exception using message = 'MESSAGE_NOT_FOUND'; end if;
    if v_actor <> v_message.requested_by_user_id and not public.oasis_sales_actor_is_admin(v_actor) then
        raise exception using errcode = '42501', message = 'PERMISSION_DENIED';
    end if;
    update public.oasis_company_kakao_guidance_messages m
    set status = case
            when m.status = 'delivered' then m.status
            else 'cancelled'
        end,
        cancelled_at = case
            when m.status = 'delivered' then m.cancelled_at
            else coalesce(m.cancelled_at, now())
        end
    where m.id = p_message_id
    returning m.* into v_message;
    -- Cancel the actual encrypted provider delivery row, not only the CRM
    -- follow-up outbox.  This is in the same transaction as the guidance/DNC
    -- state transition, and terminal cleanup removes the recoverable phone.
    update public.oasis_claim_remote_outbox o
    set status = 'cancelled',
        secure_payload_ciphertext = '',
        lease_owner = null,
        lease_until = null,
        safe_error_code = 'GUIDANCE_CANCELLED'
    where o.invite_id = v_message.secure_review_link_id
      and o.owner_user_id = v_message.requested_by_user_id
      and upper(o.event_type) like 'GUIDANCE\_%' escape '\'
      and o.status in ('pending', 'running', 'retry');
    update public.oasis_company_kakao_followup_outbox f
    set status = 'cancelled',
        completed_at = coalesce(f.completed_at, now()),
        lease_owner = null,
        lease_until = null
    where f.guidance_message_id = p_message_id
      and f.status in ('pending', 'running', 'retry');
    perform public.oasis_company_kakao_write_history(
        p_message_id, v_message.company_uid, v_actor,
        case when coalesce(p_opt_out, false) then 'cancelled_and_opted_out' else 'cancelled' end,
        v_previous_status, v_message.status,
        jsonb_build_object('reason_code', v_reason_code), null
    );
    return query select true, 'CANCELLED', '안내 발송을 중단했습니다.', p_message_id,
        v_message.status;
end;
$$;

create or replace function public.oasis_cancel_company_kakao_guidance_for_invite(
    p_owner_user_id text,
    p_invite_id uuid,
    p_opt_out boolean default true,
    p_reason text default 'customer_opt_out'
)
returns table (success boolean, code text, message text, message_id uuid, status text)
language plpgsql
volatile
security definer
set search_path = public, pg_temp
as $$
declare
    v_message public.oasis_company_kakao_guidance_messages%rowtype;
begin
    select m.* into v_message
    from public.oasis_company_kakao_guidance_messages m
    where m.secure_review_link_id = p_invite_id
      and m.requested_by_user_id = lower(btrim(coalesce(p_owner_user_id, '')))
    order by m.created_at desc, m.id desc
    limit 1;
    if v_message.id is null then
        return query select false, 'MESSAGE_NOT_FOUND', '연결된 안내 발송을 찾을 수 없습니다.', null::uuid, null::text;
        return;
    end if;
    return query select * from public.oasis_cancel_company_kakao_guidance(
        v_message.requested_by_user_id, v_message.id, p_opt_out, p_reason
    );
end;
$$;

-- Any path that writes a DNC/admin block (RPC, customer cancellation, or a
-- trusted maintenance operation) atomically stops unsent guidance delivery
-- and clears its encrypted phone payload.
create or replace function public.oasis_cancel_company_kakao_delivery_for_control()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
    if new.status not in ('opted_out', 'admin_blocked') then
        return new;
    end if;

    -- The firing statement already owns the control row.  Serialize with the
    -- provider boundary using control -> guidance -> outbox order.
    -- Stable UUID order also keeps multi-message controls deadlock resistant.
    perform 1
    from public.oasis_company_kakao_guidance_messages m
    where m.company_uid = new.company_uid
      and m.status in ('queued', 'sending')
    order by m.id
    for update;

    update public.oasis_claim_remote_outbox o
    set status = 'cancelled',
        secure_payload_ciphertext = '',
        lease_owner = null,
        lease_until = null,
        safe_error_code = case
            when new.status = 'opted_out' then 'CUSTOMER_OPT_OUT'
            else 'ADMIN_BLOCKED'
        end
    where o.invite_id in (
            select m.secure_review_link_id
            from public.oasis_company_kakao_guidance_messages m
            where m.company_uid = new.company_uid
              and m.secure_review_link_id is not null
        )
      and upper(o.event_type) like 'GUIDANCE\_%' escape '\'
      and o.status in ('pending', 'running', 'retry');

    update public.oasis_company_kakao_guidance_messages m
    set status = 'cancelled',
        cancelled_at = coalesce(m.cancelled_at, now())
    where m.company_uid = new.company_uid
      and m.status in ('queued', 'sending');
    return new;
end;
$$;

drop trigger if exists oasis_guidance_control_cancel_delivery
    on public.oasis_company_kakao_contact_controls;
create trigger oasis_guidance_control_cancel_delivery
after insert or update of status
on public.oasis_company_kakao_contact_controls
for each row execute function public.oasis_cancel_company_kakao_delivery_for_control();

create or replace function public.oasis_set_company_kakao_contact_control(
    p_current_user_id text,
    p_company_uid text,
    p_recipient_phone_hash text,
    p_status text,
    p_reason text default ''
)
returns table (success boolean, code text, message text, company_uid text, status text)
language plpgsql
volatile
security definer
set search_path = public, pg_temp
as $$
declare
    v_actor text := lower(btrim(coalesce(p_current_user_id, '')));
    v_uid text := btrim(coalesce(p_company_uid, ''));
    v_status text := lower(btrim(coalesce(p_status, '')));
    v_reason_code text := coalesce(
        nullif(
            btrim(
                left(regexp_replace(lower(coalesce(p_reason, '')), '[^a-z0-9_-]+', '_', 'g'), 80),
                '_'
            ),
            ''
        ),
        'not_specified'
    );
    v_is_admin boolean;
begin
    if not public.oasis_sales_actor_is_active(v_actor) then raise exception using errcode = '42501', message = 'PERMISSION_DENIED'; end if;
    v_is_admin := public.oasis_sales_actor_is_admin(v_actor);
    if v_status not in ('allowed', 'opted_out', 'admin_blocked') then
        return query select false, 'INVALID_STATUS', '연락 허용 상태를 확인해 주세요.', v_uid, null::text;
        return;
    end if;
    if not v_is_admin and v_status <> 'opted_out' then raise exception using errcode = '42501', message = 'PERMISSION_DENIED'; end if;
    if not v_is_admin and not exists (
        select 1 from public.oasis_company_sales_assignments a where a.company_uid = v_uid and a.assigned_user_id = v_actor
    ) then raise exception using errcode = '42501', message = 'PERMISSION_DENIED'; end if;
    insert into public.oasis_company_kakao_contact_controls (
        company_uid, recipient_phone_hash, status, reason, set_by_user_id, set_at
    ) values (
        v_uid, nullif(lower(btrim(coalesce(p_recipient_phone_hash, ''))), ''), v_status,
        v_reason_code, v_actor, now()
    ) on conflict (company_uid) do update set
        recipient_phone_hash = coalesce(excluded.recipient_phone_hash, public.oasis_company_kakao_contact_controls.recipient_phone_hash),
        status = excluded.status, reason = excluded.reason, set_by_user_id = excluded.set_by_user_id, set_at = now();
    perform public.oasis_company_kakao_write_history(null, v_uid, v_actor, 'contact_control_changed', null, v_status, jsonb_build_object('status', v_status), null);
    return query select true, 'UPDATED', '연락 허용 상태를 변경했습니다.', v_uid, v_status;
end;
$$;

create or replace function public.oasis_get_company_kakao_guidance_settings(p_current_user_id text)
returns table (send_enabled boolean, daily_limit integer, updated_at timestamptz, changed_by_user_id text, change_reason text)
language plpgsql
stable
security definer
set search_path = public, pg_temp
as $$
begin
    if not public.oasis_sales_actor_is_admin(lower(btrim(coalesce(p_current_user_id, '')))) then
        raise exception using errcode = '42501', message = 'PERMISSION_DENIED';
    end if;
    return query select s.send_enabled, s.daily_limit, s.updated_at, s.changed_by_user_id, s.change_reason
    from public.oasis_company_kakao_guidance_settings s where s.singleton;
end;
$$;

create or replace function public.oasis_update_company_kakao_guidance_settings(
    p_current_user_id text,
    p_enabled boolean,
    p_daily_limit integer,
    p_reason text
)
returns table (success boolean, code text, message text, send_enabled boolean, daily_limit integer, updated_at timestamptz)
language plpgsql
volatile
security definer
set search_path = public, pg_temp
as $$
declare
    v_actor text := lower(btrim(coalesce(p_current_user_id, '')));
    v_saved public.oasis_company_kakao_guidance_settings%rowtype;
begin
    if not public.oasis_sales_actor_is_admin(v_actor) then raise exception using errcode = '42501', message = 'PERMISSION_DENIED'; end if;
    if p_daily_limit not between 0 and 100000 or nullif(btrim(coalesce(p_reason, '')), '') is null then
        return query select false, 'INVALID_SETTINGS', '한도와 변경 사유를 확인해 주세요.', null::boolean, null::integer, null::timestamptz;
        return;
    end if;
    update public.oasis_company_kakao_guidance_settings
    set send_enabled = coalesce(p_enabled, false), daily_limit = p_daily_limit,
        changed_by_user_id = v_actor, change_reason = left(p_reason, 200)
    where singleton returning * into v_saved;
    perform public.oasis_company_kakao_write_history(null, null, v_actor, 'admin_settings_changed', null, null,
        jsonb_build_object('send_enabled', v_saved.send_enabled, 'daily_limit', v_saved.daily_limit), null);
    return query select true, 'UPDATED', '관리자 발송 설정을 변경했습니다.', v_saved.send_enabled, v_saved.daily_limit, v_saved.updated_at;
end;
$$;

create or replace function public.oasis_list_company_kakao_guidance(
    p_current_user_id text,
    p_company_uid text default '',
    p_limit integer default 100,
    p_offset integer default 0
)
returns table (
    message_id uuid, company_uid text, message_type text, status text,
    secure_review_link_id uuid, sent_at timestamptz, delivered_at timestamptz,
    failure_code text, created_at timestamptz
)
language plpgsql
stable
security definer
set search_path = public, pg_temp
as $$
declare v_actor text := lower(btrim(coalesce(p_current_user_id, '')));
begin
    if not public.oasis_sales_actor_is_active(v_actor) then raise exception using errcode = '42501', message = 'PERMISSION_DENIED'; end if;
    return query
    select m.id, m.company_uid, m.message_type, m.status, m.secure_review_link_id,
           m.sent_at, m.delivered_at, m.failure_code, m.created_at
    from public.oasis_company_kakao_guidance_messages m
    where (m.requested_by_user_id = v_actor or exists (
        select 1 from public.oasis_company_sales_assignments a
        where a.company_uid = m.company_uid and a.assigned_user_id = v_actor
    ))
      and (nullif(btrim(coalesce(p_company_uid, '')), '') is null or m.company_uid = btrim(p_company_uid))
    order by m.created_at desc
    limit greatest(1, least(coalesce(p_limit, 100), 500))
    offset greatest(0, coalesce(p_offset, 0));
end;
$$;

create or replace function public.oasis_admin_list_company_kakao_guidance(
    p_current_user_id text,
    p_status text default '',
    p_message_type text default '',
    p_limit integer default 200,
    p_offset integer default 0
)
returns table (
    message_id uuid, company_uid text, assignment_id uuid, message_type text,
    delivery_mode text, status text, requested_by_user_id text,
    secure_review_link_id uuid, provider_message_id text, sent_at timestamptz,
    delivered_at timestamptz, failure_code text, failure_summary text, created_at timestamptz
)
language plpgsql
stable
security definer
set search_path = public, pg_temp
as $$
begin
    if not public.oasis_sales_actor_is_admin(lower(btrim(coalesce(p_current_user_id, '')))) then
        raise exception using errcode = '42501', message = 'PERMISSION_DENIED';
    end if;
    return query
    select m.id, m.company_uid, m.assignment_id, m.message_type, m.delivery_mode, m.status,
           m.requested_by_user_id, m.secure_review_link_id, m.provider_message_id,
           m.sent_at, m.delivered_at, m.failure_code, m.failure_summary, m.created_at
    from public.oasis_company_kakao_guidance_messages m
    where (nullif(lower(btrim(coalesce(p_status, ''))), '') is null or m.status = lower(btrim(p_status)))
      and (nullif(lower(btrim(coalesce(p_message_type, ''))), '') is null or m.message_type = lower(btrim(p_message_type)))
    order by m.created_at desc
    limit greatest(1, least(coalesce(p_limit, 200), 1000))
    offset greatest(0, coalesce(p_offset, 0));
end;
$$;


create or replace function public.oasis_reserve_company_kakao_guidance(
    p_current_user_id text,
    p_company_id uuid,
    p_company_uid text,
    p_assignment_id uuid,
    p_recipient_phone_hash text,
    p_message_type text,
    p_template_key text,
    p_template_version text,
    p_delivery_mode text,
    p_idempotency_key text,
    p_session_id text default null,
    p_contact_id uuid default null,
    p_recipient_contact_updated_at timestamptz default null
)
returns table (
    success boolean,
    code text,
    message text,
    message_id uuid,
    status text,
    created_at timestamptz
)
language plpgsql
volatile
security definer
set search_path = public, pg_temp
as $$
declare
    v_actor text := lower(btrim(coalesce(p_current_user_id, '')));
    v_uid text := btrim(coalesce(p_company_uid, ''));
    v_type text := lower(btrim(coalesce(p_message_type, '')));
    v_mode text := lower(btrim(coalesce(p_delivery_mode, 'mock')));
    v_key text := btrim(coalesce(p_idempotency_key, ''));
    v_phone_hash text := lower(btrim(coalesce(p_recipient_phone_hash, '')));
    v_template_key text := lower(btrim(coalesce(p_template_key, '')));
    v_template_version text := left(
        coalesce(nullif(btrim(p_template_version), ''), 'v1'),
        40
    );
    v_existing public.oasis_company_kakao_guidance_messages%rowtype;
    v_saved public.oasis_company_kakao_guidance_messages%rowtype;
    v_elig record;
    v_settings public.oasis_company_kakao_guidance_settings%rowtype;
    v_today_count integer;
    v_contact_updated_at timestamptz;
begin
    if not public.oasis_sales_actor_is_active(v_actor) then
        raise exception using errcode = '42501', message = 'PERMISSION_DENIED';
    end if;
    if length(v_key) not between 1 and 200
       or v_mode not in ('mock', 'live')
       or v_template_key <> v_type then
        return query select false, 'INVALID_REQUEST', '발송 요청값을 확인해 주세요.', null::uuid, null::text, null::timestamptz;
        return;
    end if;

    select m.* into v_existing
    from public.oasis_company_kakao_guidance_messages m
    where m.requested_by_user_id = v_actor and m.idempotency_key = v_key;
    if v_existing.id is not null then
        if v_existing.company_id is distinct from p_company_id
           or v_existing.company_uid is distinct from v_uid
           or v_existing.assignment_id is distinct from p_assignment_id
           or v_existing.recipient_contact_id is distinct from p_contact_id
           or v_existing.recipient_contact_updated_at is distinct from
                p_recipient_contact_updated_at
           or v_existing.recipient_phone_hash is distinct from v_phone_hash
           or v_existing.message_type is distinct from v_type
           or v_existing.template_key is distinct from v_template_key
           or v_existing.template_version is distinct from v_template_version
           or v_existing.delivery_mode is distinct from v_mode then
            return query select false, 'IDEMPOTENCY_CONFLICT', '같은 요청 키가 다른 안내 요청에 이미 사용되었습니다.', v_existing.id, v_existing.status, v_existing.created_at;
            return;
        end if;
        return query select true, 'IDEMPOTENT_REPLAY', '이미 처리된 요청입니다.', v_existing.id, v_existing.status, v_existing.created_at;
        return;
    end if;

    perform pg_advisory_xact_lock(pg_catalog.hashtextextended('oasis-guidance:' || v_uid || ':' || v_type, 0));

    select * into v_elig
    from public.oasis_check_company_kakao_guidance_eligibility(
        v_actor,
        v_uid,
        v_type,
        lower(btrim(coalesce(p_recipient_phone_hash, ''))),
        p_contact_id
    );
    if not coalesce(v_elig.eligible, false) then
        perform public.oasis_company_kakao_write_history(
            null, v_uid, v_actor, 'reservation_blocked', null, 'blocked',
            jsonb_build_object('code', coalesce(v_elig.code, 'NOT_ELIGIBLE')), p_session_id
        );
        return query select false, coalesce(v_elig.code, 'NOT_ELIGIBLE'), coalesce(v_elig.message, '발송할 수 없습니다.'), null::uuid, 'blocked', null::timestamptz;
        return;
    end if;
    if p_assignment_id is not null and p_assignment_id <> v_elig.assignment_id then
        return query select false, 'ASSIGNMENT_MISMATCH', '업체 배정정보가 변경되었습니다.', null::uuid, 'blocked', null::timestamptz;
        return;
    end if;
    if p_company_id is not null and not exists (
        select 1
        from public.oasis_company_sales_assignments a
        where a.id = v_elig.assignment_id
          and a.company_id = p_company_id
    ) then
        return query select false, 'COMPANY_MISMATCH', '업체 정보가 변경되었습니다.', null::uuid, 'blocked', null::timestamptz;
        return;
    end if;
    if v_mode = 'live' and (
        p_contact_id is null
        or p_recipient_contact_updated_at is null
    ) then
        return query select false, 'CANONICAL_MOBILE_REQUIRED', '검증된 공개 휴대전화 연락처를 다시 선택해 주세요.', null::uuid, 'blocked', null::timestamptz;
        return;
    end if;
    if v_mode = 'live' then
        -- Lock the canonical public-contact row through reservation and bind
        -- this message to its exact non-PII row version.  Any later update
        -- (including a phone change or opt-out) makes send-ready fail closed.
        select c.updated_at into v_contact_updated_at
        from public.oasis_prospect_contacts c
        where c.id = p_contact_id
          and c.prospect_id = p_company_id
        for update;
        if v_contact_updated_at is null
           or v_contact_updated_at is distinct from p_recipient_contact_updated_at then
            return query select false, 'CANONICAL_MOBILE_CHANGED', '발송 연락처가 변경되었습니다. 다시 확인해 주세요.', null::uuid, 'blocked', null::timestamptz;
            return;
        end if;
    end if;

    select * into v_settings from public.oasis_company_kakao_guidance_settings s where s.singleton for update;
    if v_mode = 'live' and not coalesce(v_settings.send_enabled, false) then
        return query select false, 'GUIDANCE_DISABLED', '관리자가 실제 발송을 비활성화했습니다.', null::uuid, 'blocked', null::timestamptz;
        return;
    end if;
    if v_mode = 'live' then
        select count(*)::integer into v_today_count
        from public.oasis_company_kakao_guidance_messages m
        where m.delivery_mode = 'live'
          and m.status in ('queued', 'sending', 'sent', 'delivered')
          and (m.created_at at time zone 'Asia/Seoul')::date = (now() at time zone 'Asia/Seoul')::date;
        if v_today_count >= coalesce(v_settings.daily_limit, 0) then
            return query select false, 'DAILY_LIMIT_REACHED', '오늘 발송 한도에 도달했습니다.', null::uuid, 'blocked', null::timestamptz;
            return;
        end if;
    end if;

    insert into public.oasis_company_kakao_guidance_messages (
        company_id, company_uid, assignment_id, recipient_contact_id,
        recipient_contact_updated_at,
        recipient_phone_hash,
        message_type, template_key, template_version, delivery_mode,
        status, requested_by_user_id, idempotency_key
    ) values (
        (
            select a.company_id
            from public.oasis_company_sales_assignments a
            where a.id = v_elig.assignment_id
        ),
        v_uid, v_elig.assignment_id, p_contact_id,
        p_recipient_contact_updated_at,
        v_phone_hash,
        v_type, v_template_key, v_template_version,
        v_mode, 'queued', v_actor, v_key
    ) returning * into v_saved;

    perform public.oasis_company_kakao_write_history(
        v_saved.id, v_uid, v_actor, 'reserved', null, 'queued',
        jsonb_build_object('message_type', v_type, 'delivery_mode', v_mode), p_session_id
    );
    perform public.oasis_write_company_assignment_audit(
        v_actor, p_company_id, v_uid, 'kakao_guidance_reserved', '{}'::jsonb,
        jsonb_build_object('guidance_message_id', v_saved.id, 'message_type', v_type, 'delivery_mode', v_mode), p_session_id
    );
    return query select true, 'RESERVED', '안내 발송을 예약했습니다.', v_saved.id, v_saved.status, v_saved.created_at;
exception
    when unique_violation then
        select m.* into v_existing from public.oasis_company_kakao_guidance_messages m
        where m.requested_by_user_id = v_actor and m.idempotency_key = v_key;
        if v_existing.id is not null then
            if v_existing.company_id is distinct from p_company_id
               or v_existing.company_uid is distinct from v_uid
               or v_existing.assignment_id is distinct from p_assignment_id
               or v_existing.recipient_contact_id is distinct from p_contact_id
               or v_existing.recipient_contact_updated_at is distinct from
                    p_recipient_contact_updated_at
               or v_existing.recipient_phone_hash is distinct from v_phone_hash
               or v_existing.message_type is distinct from v_type
               or v_existing.template_key is distinct from v_template_key
               or v_existing.template_version is distinct from v_template_version
               or v_existing.delivery_mode is distinct from v_mode then
                return query select false, 'IDEMPOTENCY_CONFLICT', '같은 요청 키가 다른 안내 요청에 이미 사용되었습니다.', v_existing.id, v_existing.status, v_existing.created_at;
                return;
            end if;
            return query select true, 'IDEMPOTENT_REPLAY', '이미 처리된 요청입니다.', v_existing.id, v_existing.status, v_existing.created_at;
            return;
        end if;
        raise;
end;
$$;

create or replace function public.oasis_attach_company_kakao_guidance_invite(
    p_current_user_id text,
    p_message_id uuid,
    p_invite_id uuid
)
returns table (success boolean, code text, message text, message_id uuid, invite_id uuid)
language plpgsql
volatile
security definer
set search_path = public, pg_temp
as $$
declare
    v_actor text := lower(btrim(coalesce(p_current_user_id, '')));
    v_message public.oasis_company_kakao_guidance_messages%rowtype;
    v_invite public.oasis_claim_remote_invites%rowtype;
begin
    select m.* into v_message
    from public.oasis_company_kakao_guidance_messages m
    where m.id = p_message_id;
    if v_message.id is null then raise exception using message = 'MESSAGE_NOT_FOUND'; end if;
    if v_message.requested_by_user_id <> v_actor and not public.oasis_sales_actor_is_admin(v_actor) then
        raise exception using errcode = '42501', message = 'PERMISSION_DENIED';
    end if;

    -- Lock the invite before the guidance row so customer cancellation and
    -- attachment cannot cross after cancellation has committed.
    select i.* into v_invite
    from public.oasis_claim_remote_invites i
    where i.id = p_invite_id
      and i.owner_user_id = v_message.requested_by_user_id
      and i.status in ('created', 'opened')
      and i.expires_at > now()
    for update;
    if v_invite.id is null then
        raise exception using errcode = '42501', message = 'INVITE_NOT_OWNED';
    end if;

    select m.* into v_message
    from public.oasis_company_kakao_guidance_messages m
    where m.id = p_message_id
    for update;
    if v_message.id is null then raise exception using message = 'MESSAGE_NOT_FOUND'; end if;
    if (
        v_message.requested_by_user_id <> v_actor
        and not public.oasis_sales_actor_is_admin(v_actor)
    ) or v_message.requested_by_user_id <> v_invite.owner_user_id then
        raise exception using errcode = '42501', message = 'PERMISSION_DENIED';
    end if;
    if v_message.secure_review_link_id is not null and v_message.secure_review_link_id <> p_invite_id then
        raise exception using message = 'INVITE_ALREADY_ATTACHED';
    end if;
    if exists (select 1 from public.oasis_company_kakao_guidance_messages m where m.secure_review_link_id = p_invite_id and m.id <> p_message_id) then
        raise exception using message = 'INVITE_ALREADY_ATTACHED';
    end if;
    update public.oasis_company_kakao_guidance_messages set secure_review_link_id = p_invite_id where id = p_message_id;
    perform public.oasis_company_kakao_write_history(p_message_id, v_message.company_uid, v_actor, 'invite_attached', v_message.status, v_message.status, '{}'::jsonb, null);
    return query select true, 'ATTACHED', '검토신청 링크를 연결했습니다.', p_message_id, p_invite_id;
end;
$$;

create or replace function public.oasis_finalize_company_kakao_guidance(
    p_current_user_id text,
    p_message_id uuid,
    p_status text,
    p_provider_message_id text default '',
    p_provider_group_id text default '',
    p_failure_code text default '',
    p_failure_summary text default ''
)
returns table (success boolean, code text, message text, message_id uuid, status text, sent_at timestamptz, delivered_at timestamptz)
language plpgsql
volatile
security definer
set search_path = public, pg_temp
as $$
declare
    v_message public.oasis_company_kakao_guidance_messages%rowtype;
    v_saved public.oasis_company_kakao_guidance_messages%rowtype;
    v_actor text;
    v_status text := lower(btrim(coalesce(p_status, '')));
    v_first_success boolean;
    v_next_contact timestamptz;
begin
    select m.* into v_message from public.oasis_company_kakao_guidance_messages m where m.id = p_message_id for update;
    if v_message.id is null then raise exception using message = 'MESSAGE_NOT_FOUND'; end if;
    v_actor := coalesce(nullif(lower(btrim(coalesce(p_current_user_id, ''))), ''), v_message.requested_by_user_id);
    if v_actor <> v_message.requested_by_user_id and not public.oasis_sales_actor_is_admin(v_actor) then
        raise exception using errcode = '42501', message = 'PERMISSION_DENIED';
    end if;
    if v_status not in ('queued', 'sending', 'sent', 'delivered', 'failed', 'blocked', 'cancelled', 'simulated') then
        return query select false, 'INVALID_STATUS', '발송 상태를 확인해 주세요.', v_message.id, v_message.status, v_message.sent_at, v_message.delivered_at;
        return;
    end if;
    if v_message.status in ('delivered', 'cancelled') and v_status <> v_message.status then
        return query select true, 'TERMINAL_STATE', '이미 종료된 발송입니다.', v_message.id, v_message.status, v_message.sent_at, v_message.delivered_at;
        return;
    end if;
    v_first_success := v_message.status not in ('sent', 'delivered') and v_status in ('sent', 'delivered');
    update public.oasis_company_kakao_guidance_messages m set
        status = v_status,
        provider_message_id = case when nullif(btrim(p_provider_message_id), '') is not null then left(btrim(p_provider_message_id), 200) else m.provider_message_id end,
        provider_group_id = case when nullif(btrim(p_provider_group_id), '') is not null then left(btrim(p_provider_group_id), 200) else m.provider_group_id end,
        sent_at = case when v_status in ('sent', 'delivered') then coalesce(m.sent_at, now()) else m.sent_at end,
        delivered_at = case when v_status = 'delivered' then coalesce(m.delivered_at, now()) else m.delivered_at end,
        dedupe_until = case when v_status in ('sent', 'delivered') then coalesce(m.dedupe_until, now() + interval '7 days') else m.dedupe_until end,
        failure_code = case when v_status in ('failed', 'blocked') then left(regexp_replace(upper(coalesce(p_failure_code, '')), '[^A-Z0-9_-]', '_', 'g'), 80) else '' end,
        failure_summary = case when v_status in ('failed', 'blocked') then left(coalesce(p_failure_summary, ''), 300) else '' end,
        cancelled_at = case when v_status = 'cancelled' then coalesce(m.cancelled_at, now()) else m.cancelled_at end
    where m.id = p_message_id returning * into v_saved;

    perform public.oasis_company_kakao_write_history(
        v_saved.id, v_saved.company_uid, v_actor, 'status_changed', v_message.status, v_saved.status,
        jsonb_build_object('failure_code', v_saved.failure_code), null
    );

    if v_first_success and v_saved.delivery_mode = 'live' then
        v_next_contact := public.oasis_company_kakao_add_business_days(now(), 3);
        perform * from public.oasis_record_company_sales_contact(
            v_saved.requested_by_user_id, v_saved.company_id, v_saved.company_uid,
            'kakao', 'kakao_sent', '카카오톡 검토신청 안내 발송', v_next_contact, now(), null
        );
        insert into public.oasis_company_kakao_followup_outbox (
            guidance_message_id, company_uid, assigned_user_id, due_at,
            status, idempotency_key, next_retry_at
        ) values (
            v_saved.id, v_saved.company_uid, v_saved.requested_by_user_id, v_next_contact,
            'pending', 'guidance-followup:' || v_saved.id::text, v_next_contact
        ) on conflict (guidance_message_id) do nothing;
    elsif v_status = 'cancelled' then
        update public.oasis_company_kakao_followup_outbox
        set status = 'cancelled', completed_at = coalesce(completed_at, now()), lease_owner = null, lease_until = null
        where guidance_message_id = v_saved.id and status in ('pending', 'running', 'retry');
    end if;
    return query select true, 'FINALIZED', '발송 상태를 저장했습니다.', v_saved.id, v_saved.status, v_saved.sent_at, v_saved.delivered_at;
end;
$$;


-- The outbox is the durable record of the provider attempt.  The normal
-- worker callback updates the guidance message immediately; this replay-safe
-- reconciler closes the crash window if that callback is interrupted after
-- the outbox terminal update committed.
create or replace function public.oasis_reconcile_company_kakao_guidance_outbox(
    p_limit integer default 100
)
returns integer
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
    v_limit integer := greatest(1, least(coalesce(p_limit, 100), 1000));
    v_target record;
    v_outbox public.oasis_claim_remote_outbox%rowtype;
    v_result record;
    v_status text;
    v_count integer := 0;
begin
    for v_target in
        select m.id
        from public.oasis_company_kakao_guidance_messages m
        where exists (
            select 1
            from public.oasis_claim_remote_outbox o
            where o.guidance_message_id = m.id
              and o.status in (
                  'sent', 'delivered', 'failed', 'expired', 'cancelled'
              )
              and (
                  (
                      o.status = 'delivered'
                      and m.status in ('queued', 'sending', 'sent')
                  )
                  or (
                      o.status = 'sent'
                      and m.status in ('queued', 'sending')
                  )
                  or (
                      o.status in ('failed', 'expired', 'cancelled')
                      and m.status in ('queued', 'sending')
                  )
                  or (
                      o.status = 'sent'
                      and o.provider_message_id <> ''
                      and m.status = 'sent'
                      and m.provider_message_id is distinct from
                          o.provider_message_id
                  )
              )
        )
        order by m.updated_at, m.id
        for update of m skip locked
        limit v_limit
    loop
        select o.*
        into v_outbox
        from public.oasis_claim_remote_outbox o
        where o.guidance_message_id = v_target.id
          and o.status in (
              'sent', 'delivered', 'failed', 'expired', 'cancelled'
          )
        order by o.updated_at desc, o.id
        limit 1;

        if not found then
            continue;
        end if;

        v_status := case
            when v_outbox.status in ('sent', 'delivered')
                then v_outbox.status
            when v_outbox.status = 'cancelled'
                then 'cancelled'
            else 'failed'
        end;

        select * into v_result
        from public.oasis_finalize_company_kakao_guidance(
            v_outbox.owner_user_id,
            v_outbox.guidance_message_id,
            v_status,
            v_outbox.provider_message_id,
            '',
            case
                when v_status = 'failed'
                    then coalesce(
                        nullif(v_outbox.safe_error_code, ''),
                        'OUTBOX_TERMINAL'
                    )
                else ''
            end,
            ''
        );
        if found
           and coalesce(v_result.success, false)
           and v_result.code = 'FINALIZED' then
            v_count := v_count + 1;
        end if;
    end loop;

    return v_count;
end;
$$;

-- Reconcile any terminal outbox rows produced by an earlier draft before the
-- one-minute maintenance job takes over.
select public.oasis_reconcile_company_kakao_guidance_outbox(1000);


drop trigger if exists oasis_guidance_settings_updated_at on public.oasis_company_kakao_guidance_settings;
create trigger oasis_guidance_settings_updated_at before update on public.oasis_company_kakao_guidance_settings
for each row execute function public.oasis_company_kakao_touch_updated_at();
drop trigger if exists oasis_guidance_messages_updated_at on public.oasis_company_kakao_guidance_messages;
create trigger oasis_guidance_messages_updated_at before update on public.oasis_company_kakao_guidance_messages
for each row execute function public.oasis_company_kakao_touch_updated_at();
drop trigger if exists oasis_guidance_controls_updated_at on public.oasis_company_kakao_contact_controls;
create trigger oasis_guidance_controls_updated_at before update on public.oasis_company_kakao_contact_controls
for each row execute function public.oasis_company_kakao_touch_updated_at();
drop trigger if exists oasis_guidance_followup_updated_at on public.oasis_company_kakao_followup_outbox;
create trigger oasis_guidance_followup_updated_at before update on public.oasis_company_kakao_followup_outbox
for each row execute function public.oasis_company_kakao_touch_updated_at();

-- Canonical contact rows previously had an updated_at column but no trigger.
-- The version-binding send gate relies on every update advancing this value.
drop trigger if exists oasis_guidance_prospect_contacts_updated_at
on public.oasis_prospect_contacts;
create trigger oasis_guidance_prospect_contacts_updated_at
before update on public.oasis_prospect_contacts
for each row execute function public.oasis_company_kakao_touch_updated_at();

create or replace function public.oasis_company_kakao_history_immutable()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
    raise exception using errcode = '42501', message = 'OASIS_GUIDANCE_HISTORY_IMMUTABLE';
end;
$$;

drop trigger if exists oasis_guidance_history_immutable on public.oasis_company_kakao_guidance_history;
create trigger oasis_guidance_history_immutable before update or delete on public.oasis_company_kakao_guidance_history
for each row execute function public.oasis_company_kakao_history_immutable();

-- The delivery worker calls this fail-closed immediately before invoking the
-- provider.  It returns identifiers/status codes only and never exposes the
-- canonical mobile, its hash, or an encrypted payload.
drop function if exists public.oasis_check_company_kakao_guidance_send_ready(uuid);
create or replace function public.oasis_check_company_kakao_guidance_send_ready(
    p_message_id uuid,
    p_contact_id uuid,
    p_recipient_phone_hash text
)
returns table (
    allowed boolean,
    code text,
    message_id uuid
)
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    v_message public.oasis_company_kakao_guidance_messages%rowtype;
    v_recipient_phone_hash text := lower(btrim(coalesce(p_recipient_phone_hash, '')));
begin
    if p_contact_id is null
       or v_recipient_phone_hash !~ '^[0-9a-f]{64}$' then
        return query select false, 'DELIVERY_BINDING_INVALID', p_message_id;
        return;
    end if;

    select m.* into v_message
    from public.oasis_company_kakao_guidance_messages m
    where m.id = p_message_id;

    if v_message.id is null then
        return query select false, 'MESSAGE_NOT_FOUND', p_message_id;
        return;
    end if;
    if v_message.delivery_mode <> 'live'
       or v_message.status not in ('queued', 'sending') then
        return query select false, 'GUIDANCE_NOT_SENDABLE', v_message.id;
        return;
    end if;
    if v_message.recipient_contact_id is distinct from p_contact_id
       or v_message.recipient_phone_hash <> v_recipient_phone_hash then
        return query select false, 'DELIVERY_BINDING_MISMATCH', v_message.id;
        return;
    end if;
    if not exists (
        select 1
        from public.oasis_company_kakao_guidance_settings s
        where s.singleton and s.send_enabled
    ) then
        return query select false, 'GUIDANCE_DISABLED', v_message.id;
        return;
    end if;
    if exists (
        select 1
        from public.oasis_company_kakao_contact_controls c
        where (c.company_uid = v_message.company_uid
               or c.recipient_phone_hash = v_message.recipient_phone_hash)
          and c.status in ('opted_out', 'admin_blocked')
    ) or exists (
        select 1
        from public.oasis_prospect_contacts c
        where c.prospect_id = v_message.company_id
          and (c.do_not_contact is true or c.opt_out_at is not null)
    ) then
        return query select false, 'DO_NOT_CONTACT', v_message.id;
        return;
    end if;
    if not exists (
        select 1
        from public.oasis_company_sales_assignments a
        where a.id = v_message.assignment_id
          and a.company_id = v_message.company_id
          and a.company_uid = v_message.company_uid
          and a.assigned_user_id = v_message.requested_by_user_id
          and not a.permanently_excluded
          and a.status not in (
              'unassigned', 'wrong_number', 'closed', 'long_hold',
              'permanently_excluded'
          )
    ) then
        return query select false, 'ASSIGNMENT_CHANGED', v_message.id;
        return;
    end if;
    if v_message.recipient_contact_id is null or not exists (
        select 1
        from public.oasis_prospect_contacts c
        where c.id = v_message.recipient_contact_id
          and c.prospect_id = v_message.company_id
          and c.updated_at is not distinct from
              v_message.recipient_contact_updated_at
          and c.contact_type in ('phone', 'mobile', 'mobile_phone')
          and c.do_not_contact is not true
          and c.opt_out_at is null
          and c.verification_status <> 'rejected'
          and regexp_replace(coalesce(c.contact_value, ''), '[^0-9]', '', 'g')
              ~ '^01(0[0-9]{8}|[16789][0-9]{7,8})$'
    ) then
        return query select false, 'CANONICAL_MOBILE_CHANGED', v_message.id;
        return;
    end if;
    if v_message.secure_review_link_id is null or not exists (
        select 1
        from public.oasis_claim_remote_invites i
        where i.id = v_message.secure_review_link_id
          and i.owner_user_id = v_message.requested_by_user_id
          and i.status in ('created', 'opened')
          and i.expires_at > now()
    ) then
        return query select false, 'INVITE_NOT_ACTIVE', v_message.id;
        return;
    end if;
    if not exists (
        select 1
        from public.oasis_claim_remote_outbox o
        where o.guidance_message_id = v_message.id
          and o.invite_id = v_message.secure_review_link_id
          and o.owner_user_id = v_message.requested_by_user_id
          and o.idempotency_key = 'guidance:' || v_message.id::text
          and o.event_type = 'GUIDANCE_' || upper(v_message.message_type)
          and o.template_code = 'GUIDANCE_' || upper(v_message.message_type)
          and o.status = 'running'
          and o.secure_payload_ciphertext <> ''
          and o.expires_at > now()
    ) then
        return query select false, 'DELIVERY_NOT_ACTIVE', v_message.id;
        return;
    end if;

    return query select true, 'READY', v_message.id;
end;
$$;

create or replace function public.oasis_company_kakao_guidance_feature_ready()
returns boolean
language sql
stable
set search_path = ''
as $$
    select to_regclass('public.oasis_company_kakao_guidance_messages') is not null
       and to_regclass('public.oasis_company_kakao_contact_controls') is not null
       and to_regclass('public.oasis_company_kakao_guidance_history') is not null
       and to_regclass('public.oasis_company_kakao_followup_outbox') is not null;
$$;

-- 고객이 공개 신청링크에서 취소하면 이후 인증·수집 작업과 메시지만 중단한다.
-- 이미 수집된 문서, 사건, 감사이력은 삭제하지 않는다.
create or replace function public.oasis_claim_remote_cancel_invite(
    p_owner_user_id text,
    p_token_hash text,
    p_reason text default 'customer_cancelled'
)
returns table (
    success boolean,
    code text,
    message text,
    invite_id uuid,
    invite_status text,
    cancelled_jobs integer,
    cancelled_messages integer
)
language plpgsql
volatile
security definer
set search_path = public, pg_temp
as $$
declare
    v_owner text := lower(btrim(coalesce(p_owner_user_id, '')));
    v_token_hash text := lower(btrim(coalesce(p_token_hash, '')));
    v_invite public.oasis_claim_remote_invites%rowtype;
    v_guidance public.oasis_company_kakao_guidance_messages%rowtype;
    v_previous_guidance_status text;
    v_previous_control_status text;
    v_reason_code text;
    v_jobs integer := 0;
    v_messages integer := 0;
    v_guidance_messages integer := 0;
    v_outbox_updates integer := 0;
    v_followups integer := 0;
    v_invite_changed boolean := false;
    v_guidance_changed boolean := false;
    v_control_changed boolean := false;
begin
    if v_token_hash !~ '^[0-9a-f]{64}$' or v_owner = '' then
        return query select false, 'INVALID_INVITE', '신청링크를 확인할 수 없습니다.', null::uuid, null::text, 0, 0;
        return;
    end if;
    perform pg_advisory_xact_lock(pg_catalog.hashtextextended('oasis-claim-cancel:' || v_token_hash, 0));
    select i.* into v_invite
    from public.oasis_claim_remote_invites i
    where i.owner_user_id = v_owner and i.token_hash = v_token_hash
    for update;
    if v_invite.id is null then
        return query select false, 'INVALID_INVITE', '신청링크를 확인할 수 없습니다.', null::uuid, null::text, 0, 0;
        return;
    end if;

    v_reason_code := nullif(
        btrim(
            left(
                regexp_replace(lower(coalesce(p_reason, '')), '[^a-z0-9_-]+', '_', 'g'),
                80
            ),
            '_'
        ),
        ''
    );
    v_reason_code := coalesce(v_reason_code, 'customer_cancelled');

    -- Read the binding without a guidance row lock.  Every opt-out writer
    -- takes the contact-control row first; its AFTER trigger then locks the
    -- guidance and encrypted delivery outbox in the shared global order.
    select m.* into v_guidance
    from public.oasis_company_kakao_guidance_messages m
    where m.secure_review_link_id = v_invite.id
      and m.requested_by_user_id = v_owner
    order by m.created_at desc, m.id desc
    limit 1;

    if v_guidance.id is not null then
        v_previous_guidance_status := v_guidance.status;

        select count(*)
        into v_guidance_messages
        from public.oasis_claim_remote_outbox o
        where o.invite_id = v_invite.id
          and o.owner_user_id = v_owner
          and upper(o.event_type) like 'GUIDANCE\_%' escape '\'
          and o.status in ('pending', 'running', 'retry');

        select c.status into v_previous_control_status
        from public.oasis_company_kakao_contact_controls c
        where c.company_uid = v_guidance.company_uid
        for update;

        v_control_changed := v_previous_control_status is distinct from 'opted_out';
        insert into public.oasis_company_kakao_contact_controls (
            company_uid,
            recipient_phone_hash,
            status,
            reason,
            set_by_user_id,
            set_at
        ) values (
            v_guidance.company_uid,
            v_guidance.recipient_phone_hash,
            'opted_out',
            v_reason_code,
            v_owner,
            now()
        ) on conflict (company_uid) do update set
            recipient_phone_hash = excluded.recipient_phone_hash,
            status = 'opted_out',
            reason = excluded.reason,
            set_by_user_id = excluded.set_by_user_id,
            set_at = excluded.set_at;

        select m.* into v_guidance
        from public.oasis_company_kakao_guidance_messages m
        where m.id = v_guidance.id
          and m.secure_review_link_id = v_invite.id
          and m.requested_by_user_id = v_owner
        for update;

        if v_guidance.id is not null then
            v_guidance_changed :=
                v_previous_guidance_status not in ('delivered', 'cancelled')
                and v_guidance.status = 'cancelled';

            if v_guidance.status not in ('delivered', 'cancelled') then
                update public.oasis_company_kakao_guidance_messages m
                set status = 'cancelled',
                    cancelled_at = coalesce(m.cancelled_at, now())
                where m.id = v_guidance.id
                returning m.* into v_guidance;
                v_guidance_changed := true;
            end if;
        end if;
    end if;

    update public.oasis_claim_remote_jobs j
    set status = 'cancelled', secure_payload_ciphertext = '', lease_owner = null,
        lease_until = null, sensitive_expires_at = null,
        safe_error_code = 'CUSTOMER_CANCELLED',
        safe_message = '고객이 검토신청을 취소했습니다.'
    where j.invite_id = v_invite.id and j.owner_user_id = v_owner
      and j.status in ('queued', 'running', 'waiting', 'retry');
    get diagnostics v_jobs = row_count;

    update public.oasis_claim_remote_outbox o
    set status = 'cancelled', secure_payload_ciphertext = '', lease_owner = null,
        lease_until = null, safe_error_code = 'CUSTOMER_CANCELLED'
    where o.invite_id = v_invite.id and o.owner_user_id = v_owner
      and o.status in ('pending', 'running', 'retry');
    get diagnostics v_outbox_updates = row_count;
    v_messages := v_guidance_messages + v_outbox_updates;

    if v_guidance.id is not null then
        update public.oasis_company_kakao_followup_outbox f
        set status = 'cancelled',
            completed_at = coalesce(f.completed_at, now()),
            lease_owner = null,
            lease_until = null
        where f.guidance_message_id = v_guidance.id
          and f.status in ('pending', 'running', 'retry');
        get diagnostics v_followups = row_count;

        if v_guidance_changed or v_control_changed or v_followups > 0 then
            perform public.oasis_company_kakao_write_history(
                v_guidance.id,
                v_guidance.company_uid,
                v_owner,
                'claim_invite_cancelled_and_opted_out',
                v_previous_guidance_status,
                v_guidance.status,
                jsonb_build_object(
                    'reason_code', v_reason_code,
                    'followups', v_followups
                ),
                null
            );
        end if;
    end if;

    if v_invite.status not in ('expired', 'cancelled') then
        update public.oasis_claim_remote_invites i
        set status = 'cancelled', secure_payload_ciphertext = ''
        where i.id = v_invite.id;
        v_invite.status := 'cancelled';
        v_invite_changed := true;
    end if;

    -- p_reason 원문은 저장하지 않는다. 상태가 실제로 바뀐 호출만 안전한
    -- 코드와 집계값으로 기록하여 재호출도 최종 상태가 동일하게 유지된다.
    if v_invite_changed or v_jobs > 0 or v_messages > 0 then
        perform public.oasis_company_kakao_write_history(
            null, null, v_owner, 'claim_invite_cancelled', null, v_invite.status,
            jsonb_build_object(
                'reason_code', v_reason_code,
                'jobs', v_jobs,
                'messages', v_messages
            ), null
        );
    end if;
    return query select true, 'CANCELLED', '검토신청과 이후 인증 요청을 중단했습니다.',
        v_invite.id, v_invite.status, v_jobs, v_messages;
end;
$$;

-- The representative's self-entered authentication phone and identity data
-- live only in oasis_claim_remote_jobs.secure_payload_ciphertext.  These
-- overrides enforce a ten-minute idle deadline without changing the absolute
-- hard_expires_at ceiling used by the existing collection flow.
create or replace function public.oasis_claim_remote_get_session_status(
    p_owner_user_id text,
    p_invite_id uuid
)
returns table (
    invite_id uuid,
    invite_status text,
    invite_expires_at timestamptz,
    case_id uuid,
    job_stage text,
    job_status text,
    progress integer,
    safe_message text,
    safe_error_code text,
    job_updated_at timestamptz
)
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_now timestamptz := clock_timestamp();
    v_invite public.oasis_claim_remote_invites;
begin
    select i.*
    into v_invite
    from public.oasis_claim_remote_invites i
    where i.id = p_invite_id
      and i.owner_user_id = lower(trim(p_owner_user_id))
    for update;

    if not found then
        raise exception 'REMOTE_INVITE_NOT_FOUND';
    end if;

    if v_invite.status in ('created', 'queued', 'sent', 'opened')
       and v_invite.expires_at <= v_now then
        update public.oasis_claim_remote_invites i
        set status = 'expired',
            secure_payload_ciphertext = '',
            updated_at = v_now
        where i.id = v_invite.id
        returning i.* into v_invite;
    end if;

    update public.oasis_claim_remote_jobs j
    set status = 'expired',
        secure_payload_ciphertext = '',
        sensitive_expires_at = null,
        lease_owner = null,
        lease_until = null,
        safe_error_code = case
            when j.hard_expires_at <= v_now then 'JOB_TTL_EXPIRED'
            else 'SENSITIVE_TTL_EXPIRED'
        end,
        updated_at = v_now
    where j.invite_id = v_invite.id
      and j.owner_user_id = v_invite.owner_user_id
      and j.status in ('queued', 'running', 'waiting', 'retry')
      and (
          j.hard_expires_at <= v_now
          or j.sensitive_expires_at is null
          or j.sensitive_expires_at <= v_now
      );

    return query
    select i.id,
           i.status,
           i.expires_at,
           j.case_id,
           j.stage,
           j.status,
           j.progress,
           j.safe_message,
           j.safe_error_code,
           j.updated_at
    from public.oasis_claim_remote_invites i
    left join public.oasis_claim_remote_jobs j
      on j.invite_id = i.id
     and j.owner_user_id = i.owner_user_id
    where i.id = v_invite.id
      and i.owner_user_id = v_invite.owner_user_id
    limit 1;
end;
$$;


-- PII-free point-in-time authorization immediately before every external
-- authentication/collection provider call.  The synchronous first request
-- uses its short reservation; worker calls bind to the exact live lease.
create or replace function public.oasis_claim_remote_check_job_active(
    p_job_id uuid,
    p_owner_user_id text,
    p_mode text,
    p_worker_id text
)
returns table (
    allowed boolean,
    code text,
    job_id uuid
)
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
    v_now timestamptz := clock_timestamp();
    v_owner text := lower(btrim(coalesce(p_owner_user_id, '')));
    v_mode text := lower(btrim(coalesce(p_mode, '')));
    v_worker text := btrim(coalesce(p_worker_id, ''));
    v_job public.oasis_claim_remote_jobs%rowtype;
begin
    if v_mode not in ('submission_reserved', 'leased') then
        return query select false, 'JOB_MODE_INVALID', p_job_id;
        return;
    end if;

    select j.* into v_job
    from public.oasis_claim_remote_jobs j
    where j.id = p_job_id;
    if v_job.id is null then
        return query select false, 'JOB_NOT_FOUND', p_job_id;
        return;
    end if;
    if v_owner = '' or v_job.owner_user_id <> v_owner then
        return query select false, 'JOB_OWNER_MISMATCH', p_job_id;
        return;
    end if;
    if v_job.hard_expires_at <= v_now then
        return query select false, 'JOB_HARD_EXPIRED', p_job_id;
        return;
    end if;
    if v_job.sensitive_expires_at is null
       or v_job.sensitive_expires_at <= v_now then
        return query select false, 'JOB_SENSITIVE_EXPIRED', p_job_id;
        return;
    end if;

    if v_mode = 'submission_reserved' then
        if v_job.status <> 'waiting'
           or v_job.stage <> 'submission_reserved' then
            return query select false, 'JOB_RESERVATION_INVALID', p_job_id;
            return;
        end if;
        if v_job.next_run_at <= v_now then
            return query select false, 'JOB_RESERVATION_EXPIRED', p_job_id;
            return;
        end if;
    else
        if v_job.status <> 'running' then
            return query select false, 'JOB_NOT_RUNNING', p_job_id;
            return;
        end if;
        if v_worker = '' or v_job.lease_owner is distinct from v_worker then
            return query select false, 'JOB_LEASE_NOT_OWNED', p_job_id;
            return;
        end if;
        if v_job.lease_until is null or v_job.lease_until <= v_now then
            return query select false, 'JOB_LEASE_EXPIRED', p_job_id;
            return;
        end if;
    end if;

    return query select true, 'ACTIVE', p_job_id;
end;
$$;


create or replace function public.oasis_claim_remote_consume_invite(
    p_owner_user_id text,
    p_token_hash text,
    p_job jsonb
)
returns setof public.oasis_claim_remote_jobs
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_now timestamptz := clock_timestamp();
    v_invite public.oasis_claim_remote_invites;
    v_job public.oasis_claim_remote_jobs;
    v_job_id uuid;
    v_case_id uuid;
    v_stage text;
    v_initial_status text;
    v_ciphertext text;
    v_key_version text;
    v_next_run_at timestamptz;
    v_hard_expires_at timestamptz;
    v_requested_sensitive_expires_at timestamptz;
    v_sensitive_expires_at timestamptz;
    v_max_attempts integer;
begin
    if jsonb_typeof(coalesce(p_job, '{}'::jsonb)) <> 'object' then
        raise exception 'REMOTE_JOB_INVALID';
    end if;

    select i.*
    into v_invite
    from public.oasis_claim_remote_invites i
    where i.owner_user_id = lower(trim(p_owner_user_id))
      and i.token_hash = lower(trim(p_token_hash))
    for update;

    if not found then
        raise exception 'REMOTE_INVITE_NOT_FOUND';
    end if;
    if v_invite.status = 'submitted' or v_invite.consumed_at is not null then
        raise exception 'REMOTE_INVITE_ALREADY_CONSUMED';
    end if;
    if v_invite.expires_at <= v_now or v_invite.status = 'expired' then
        raise exception 'REMOTE_INVITE_EXPIRED';
    end if;
    if v_invite.status not in ('created', 'queued', 'sent', 'opened') then
        raise exception 'REMOTE_INVITE_NOT_ACTIVE';
    end if;

    v_job_id := nullif(trim(p_job ->> 'id'), '')::uuid;
    v_case_id := nullif(trim(p_job ->> 'case_id'), '')::uuid;
    v_stage := lower(trim(p_job ->> 'stage'));
    v_initial_status := lower(
        coalesce(nullif(trim(p_job ->> 'initial_status'), ''), 'queued')
    );
    v_ciphertext := coalesce(p_job ->> 'secure_payload_ciphertext', '');
    v_key_version := trim(p_job ->> 'payload_key_version');
    v_next_run_at := greatest(
        v_now,
        coalesce(
            nullif(trim(p_job ->> 'next_run_at'), '')::timestamptz,
            v_now
        )
    );
    v_hard_expires_at := least(
        nullif(trim(p_job ->> 'hard_expires_at'), '')::timestamptz,
        v_now + interval '45 minutes'
    );
    v_requested_sensitive_expires_at :=
        nullif(trim(p_job ->> 'sensitive_expires_at'), '')::timestamptz;
    v_max_attempts := greatest(
        1,
        least(coalesce((p_job ->> 'max_attempts')::integer, 12), 100)
    );

    if v_hard_expires_at is not null then
        v_sensitive_expires_at := least(
            v_hard_expires_at,
            coalesce(
                v_requested_sensitive_expires_at,
                v_now + interval '10 minutes'
            ),
            v_now + interval '10 minutes'
        );
    end if;

    if v_job_id is null
       or v_case_id is null
       or v_stage !~ '^[a-z0-9._-]{1,80}$'
       or v_initial_status not in ('queued', 'waiting')
       or (v_stage = 'submission_reserved' and v_initial_status <> 'waiting')
       or (v_initial_status = 'waiting' and v_stage <> 'submission_reserved')
       or length(v_ciphertext) < 40
       or v_key_version !~ '^[A-Za-z0-9._-]{1,40}$'
       or v_hard_expires_at is null
       or v_hard_expires_at <= v_now
       or v_sensitive_expires_at is null
       or v_sensitive_expires_at <= v_now
       or v_next_run_at > v_hard_expires_at then
        raise exception 'REMOTE_JOB_INVALID';
    end if;

    insert into public.oasis_claim_remote_jobs (
        id,
        owner_user_id,
        invite_id,
        case_id,
        stage,
        status,
        secure_payload_ciphertext,
        payload_key_version,
        next_run_at,
        max_attempts,
        hard_expires_at,
        sensitive_expires_at,
        created_at,
        updated_at
    ) values (
        v_job_id,
        v_invite.owner_user_id,
        v_invite.id,
        v_case_id,
        v_stage,
        v_initial_status,
        v_ciphertext,
        v_key_version,
        v_next_run_at,
        v_max_attempts,
        v_hard_expires_at,
        v_sensitive_expires_at,
        v_now,
        v_now
    )
    returning * into v_job;

    update public.oasis_claim_remote_invites
    set status = 'submitted',
        secure_payload_ciphertext = '',
        consumed_at = v_now,
        case_id = v_case_id,
        updated_at = v_now
    where id = v_invite.id;

    return next v_job;
end;
$$;


-- Replay-safe override of the existing remote-outbox RPC.  Guidance rows are
-- accepted only when the clear, non-PII guidance_message_id matches the same
-- owner and invite already attached to the reserved live message.
create or replace function public.oasis_claim_remote_enqueue_outbox(
    p_message jsonb
)
returns setof public.oasis_claim_remote_outbox
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_now timestamptz := clock_timestamp();
    v_message public.oasis_claim_remote_outbox;
    v_invite public.oasis_claim_remote_invites;
    v_job public.oasis_claim_remote_jobs;
    v_id uuid;
    v_owner_user_id text;
    v_invite_id uuid;
    v_case_id uuid;
    v_guidance_message_id uuid;
    v_event_type text;
    v_template_code text;
    v_idempotency_key text;
    v_ciphertext text;
    v_key_version text;
    v_run_after timestamptz;
    v_expires_at timestamptz;
    v_max_attempts integer;
    v_is_guidance boolean;
begin
    if jsonb_typeof(coalesce(p_message, '{}'::jsonb)) <> 'object' then
        raise exception 'REMOTE_OUTBOX_INVALID';
    end if;

    v_id := nullif(trim(p_message ->> 'id'), '')::uuid;
    v_owner_user_id := lower(trim(p_message ->> 'owner_user_id'));
    v_invite_id := nullif(trim(p_message ->> 'invite_id'), '')::uuid;
    v_case_id := nullif(trim(p_message ->> 'case_id'), '')::uuid;
    v_guidance_message_id :=
        nullif(trim(p_message ->> 'guidance_message_id'), '')::uuid;
    v_event_type := upper(trim(p_message ->> 'event_type'));
    v_template_code := trim(p_message ->> 'template_code');
    v_idempotency_key := trim(p_message ->> 'idempotency_key');
    v_ciphertext := coalesce(p_message ->> 'secure_payload_ciphertext', '');
    v_key_version := trim(p_message ->> 'payload_key_version');
    v_run_after := coalesce(
        nullif(trim(p_message ->> 'run_after'), '')::timestamptz,
        v_now
    );
    v_expires_at :=
        nullif(trim(p_message ->> 'expires_at'), '')::timestamptz;
    v_max_attempts := greatest(
        1,
        least(coalesce((p_message ->> 'max_attempts')::integer, 8), 100)
    );
    v_is_guidance := left(v_event_type, 9) = 'GUIDANCE_';

    if v_id is null
       or coalesce(v_owner_user_id, '') = ''
       or (v_invite_id is null and v_case_id is null)
       or v_event_type !~ '^[A-Z0-9_.-]{1,80}$'
       or v_template_code !~ '^[A-Za-z0-9_.-]{1,120}$'
       or length(v_idempotency_key) not between 1 and 200
       or length(v_ciphertext) < 40
       or v_key_version !~ '^[A-Za-z0-9._-]{1,40}$'
       or v_expires_at is null
       or v_expires_at <= v_now
       or v_run_after >= v_expires_at
       or (v_is_guidance and (v_guidance_message_id is null or v_invite_id is null))
       or (not v_is_guidance and v_guidance_message_id is not null) then
        raise exception 'REMOTE_OUTBOX_INVALID';
    end if;

    -- Match the cancellation path's invite -> job -> outbox lock order.  This
    -- also makes a terminal transition wait until a concurrent NEXT_AUTH row
    -- has either been safely inserted or rejected.
    if v_invite_id is not null then
        select i.*
        into v_invite
        from public.oasis_claim_remote_invites i
        where i.id = v_invite_id
          and i.owner_user_id = v_owner_user_id
        for update;

        if not found then
            raise exception 'REMOTE_OUTBOX_TARGET_INVALID';
        end if;
    end if;

    if v_is_guidance and (
        v_invite.status not in ('created', 'opened')
        or v_invite.expires_at <= v_now
        or not exists (
            select 1
            from public.oasis_company_kakao_guidance_messages m
            where m.id = v_guidance_message_id
              and m.secure_review_link_id = v_invite_id
              and m.requested_by_user_id = v_owner_user_id
              and m.delivery_mode = 'live'
              and m.status in ('queued', 'sending')
              and m.recipient_contact_id is not null
              and m.recipient_contact_updated_at is not null
        )
    ) then
        raise exception 'REMOTE_OUTBOX_GUIDANCE_BINDING_INVALID';
    end if;

    if v_event_type = 'NEXT_AUTH' then
        select j.*
        into v_job
        from public.oasis_claim_remote_jobs j
        where j.owner_user_id = v_owner_user_id
          and j.invite_id = v_invite_id
          and j.case_id = v_case_id
        for update;

        if not found
           or v_job.status not in ('queued', 'running', 'waiting', 'retry')
           or v_job.hard_expires_at <= v_now
           or v_job.sensitive_expires_at is null
           or v_job.sensitive_expires_at <= v_now then
            raise exception 'REMOTE_OUTBOX_AUTH_FLOW_NOT_ACTIVE';
        end if;

        v_expires_at := least(
            v_expires_at,
            v_now + interval '10 minutes',
            v_job.sensitive_expires_at,
            v_job.hard_expires_at
        );
        if v_run_after >= v_expires_at then
            raise exception 'REMOTE_OUTBOX_INVALID';
        end if;
    end if;

    insert into public.oasis_claim_remote_outbox (
        id,
        owner_user_id,
        invite_id,
        case_id,
        guidance_message_id,
        event_type,
        template_code,
        idempotency_key,
        status,
        secure_payload_ciphertext,
        payload_key_version,
        run_after,
        expires_at,
        max_attempts,
        created_at,
        updated_at
    ) values (
        v_id,
        v_owner_user_id,
        v_invite_id,
        v_case_id,
        v_guidance_message_id,
        v_event_type,
        v_template_code,
        v_idempotency_key,
        'pending',
        v_ciphertext,
        v_key_version,
        v_run_after,
        v_expires_at,
        v_max_attempts,
        v_now,
        v_now
    )
    on conflict (owner_user_id, idempotency_key) do nothing;

    select o.*
    into v_message
    from public.oasis_claim_remote_outbox o
    where o.owner_user_id = v_owner_user_id
      and o.idempotency_key = v_idempotency_key
    for update;

    if not found then
        raise exception 'REMOTE_OUTBOX_INSERT_FAILED';
    end if;
    if v_message.event_type <> v_event_type
       or v_message.template_code <> v_template_code
       or v_message.invite_id is distinct from v_invite_id
       or v_message.case_id is distinct from v_case_id
       or v_message.guidance_message_id is distinct from
          v_guidance_message_id then
        raise exception 'REMOTE_OUTBOX_IDEMPOTENCY_CONFLICT';
    end if;

    return next v_message;
end;
$$;


-- At-most-once provider boundary for live guidance.  The worker decrypts and
-- validates the public delivery contact, then calls this RPC immediately
-- before SOLAPI.  Clearing the ciphertext while retaining the owned lease
-- means a process crash can require reconciliation, but can never reveal a
-- destination to another automatic retry.
drop function if exists public.oasis_claim_remote_begin_guidance_dispatch(uuid, text);
create or replace function public.oasis_claim_remote_begin_guidance_dispatch(
    p_message_id uuid,
    p_worker_id text,
    p_contact_id uuid,
    p_recipient_phone_hash text
)
returns table (
    success boolean,
    code text,
    message_id uuid
)
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
    v_now timestamptz := clock_timestamp();
    v_worker text := btrim(coalesce(p_worker_id, ''));
    v_phone_hash text := lower(btrim(coalesce(p_recipient_phone_hash, '')));
    v_guidance_message_id uuid;
    v_message public.oasis_company_kakao_guidance_messages%rowtype;
    v_outbox public.oasis_claim_remote_outbox%rowtype;
begin
    if p_message_id is null
       or v_worker !~ '^[A-Za-z0-9._:-]{1,120}$'
       or p_contact_id is null
       or v_phone_hash !~ '^[0-9a-f]{64}$' then
        return query select false, 'GUIDANCE_DISPATCH_INVALID', p_message_id;
        return;
    end if;

    -- Read the non-PII binding first, then serialize every cancellation/control
    -- path on the guidance row before locking its outbox row.  Re-check the
    -- binding after both locks so a stale or swapped row cannot cross the
    -- provider boundary.
    select o.guidance_message_id
    into v_guidance_message_id
    from public.oasis_claim_remote_outbox o
    where o.id = p_message_id;

    if v_guidance_message_id is null then
        return query select false, 'GUIDANCE_DISPATCH_NOT_READY', p_message_id;
        return;
    end if;

    select m.*
    into v_message
    from public.oasis_company_kakao_guidance_messages m
    where m.id = v_guidance_message_id
    for update;

    select o.*
    into v_outbox
    from public.oasis_claim_remote_outbox o
    where o.id = p_message_id
    for update;

    if not found then
        return query select false, 'OUTBOX_NOT_FOUND', p_message_id;
        return;
    end if;
    if v_message.id is null
       or v_outbox.guidance_message_id is distinct from v_message.id
       or left(v_outbox.event_type, 9) <> 'GUIDANCE_'
       or v_outbox.status <> 'running'
       or v_outbox.lease_owner is distinct from v_worker
       or v_outbox.lease_until is null
       or v_outbox.lease_until <= v_now
       or v_outbox.expires_at <= v_now
       or v_outbox.guidance_dispatch_started_at is not null
       or length(v_outbox.secure_payload_ciphertext) < 40 then
        return query select false, 'GUIDANCE_DISPATCH_NOT_READY', v_message.id;
        return;
    end if;

    -- Repeat every mutable send-readiness predicate in the same transaction
    -- that erases the destination and records dispatch start.  The clear
    -- contact id and worker-computed HMAC bind the decrypted payload to the
    -- canonical public-number reservation without exposing the number.
    if v_message.delivery_mode <> 'live'
       or v_message.status not in ('queued', 'sending')
       or v_message.recipient_contact_id is distinct from p_contact_id
       or v_message.recipient_phone_hash <> v_phone_hash
       or v_message.secure_review_link_id is distinct from v_outbox.invite_id
       or v_message.requested_by_user_id <> v_outbox.owner_user_id
       or v_outbox.idempotency_key <> 'guidance:' || v_message.id::text
       or v_outbox.event_type <> 'GUIDANCE_' || upper(v_message.message_type)
       or v_outbox.template_code <> 'GUIDANCE_' || upper(v_message.message_type)
       or not exists (
           select 1
           from public.oasis_company_kakao_guidance_settings s
           where s.singleton
             and s.send_enabled
       )
       or exists (
           select 1
           from public.oasis_company_kakao_contact_controls c
           where (
                   c.company_uid = v_message.company_uid
                   or c.recipient_phone_hash = v_message.recipient_phone_hash
               )
             and c.status in ('opted_out', 'admin_blocked')
       )
       or exists (
           select 1
           from public.oasis_prospect_contacts c
           where c.prospect_id = v_message.company_id
             and (c.do_not_contact is true or c.opt_out_at is not null)
       )
       or not exists (
           select 1
           from public.oasis_company_sales_assignments a
           where a.id = v_message.assignment_id
             and a.company_id = v_message.company_id
             and a.company_uid = v_message.company_uid
             and a.assigned_user_id = v_message.requested_by_user_id
             and not a.permanently_excluded
             and a.status not in (
                 'unassigned', 'wrong_number', 'closed', 'long_hold',
                 'permanently_excluded'
             )
       )
       or not exists (
           select 1
           from public.oasis_prospect_contacts c
           where c.id = v_message.recipient_contact_id
             and c.prospect_id = v_message.company_id
             and c.updated_at is not distinct from
                 v_message.recipient_contact_updated_at
             and c.contact_type in ('phone', 'mobile', 'mobile_phone')
             and c.do_not_contact is not true
             and c.opt_out_at is null
             and c.verification_status <> 'rejected'
             and regexp_replace(
                 coalesce(c.contact_value, ''), '[^0-9]', '', 'g'
             ) ~ '^01(0[0-9]{8}|[16789][0-9]{7,8})$'
       )
       or not exists (
           select 1
           from public.oasis_claim_remote_invites i
           where i.id = v_message.secure_review_link_id
             and i.owner_user_id = v_message.requested_by_user_id
             and i.status in ('created', 'opened')
             and i.expires_at > v_now
       ) then
        return query select false,
            'GUIDANCE_DISPATCH_REVALIDATION_FAILED', v_message.id;
        return;
    end if;

    update public.oasis_claim_remote_outbox o
    set secure_payload_ciphertext = '',
        guidance_dispatch_started_at = v_now,
        -- A dispatch-started row is never automatically leased again.  The
        -- current worker may confirm it; otherwise expiry/manual provider
        -- reconciliation resolves the ambiguous external side effect.
        lease_until = o.expires_at,
        updated_at = v_now
    where o.id = v_outbox.id;

    return query select true, 'GUIDANCE_DISPATCH_STARTED', v_message.id;
end;
$$;


-- Independently of the Railway worker and the task-automation migration,
-- terminal claim state immediately destroys any still-recoverable NEXT_AUTH
-- payload that contains the customer's self-entered authentication number.
create or replace function public.oasis_claim_remote_purge_auth_outbox_terminal()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    if new.status in ('complete', 'partial', 'failed', 'expired', 'cancelled')
       and (
           tg_op = 'INSERT'
           or old.status is distinct from new.status
       ) then
        update public.oasis_claim_remote_outbox o
        set status = 'cancelled',
            secure_payload_ciphertext = '',
            lease_owner = null,
            lease_until = null,
            safe_error_code = 'AUTH_FLOW_TERMINAL',
            updated_at = clock_timestamp()
        where o.owner_user_id = new.owner_user_id
          and o.invite_id = new.invite_id
          and o.event_type = 'NEXT_AUTH'
          and o.status in ('pending', 'running', 'retry');
    end if;
    return new;
end;
$$;

drop trigger if exists oasis_claim_job_purge_auth_outbox_terminal
    on public.oasis_claim_remote_jobs;
create trigger oasis_claim_job_purge_auth_outbox_terminal
after insert or update of status
on public.oasis_claim_remote_jobs
for each row execute function public.oasis_claim_remote_purge_auth_outbox_terminal();

-- Rows made terminal by the normalization earlier in this same replayable
-- migration predate the trigger creation, so reconcile them once explicitly.
update public.oasis_claim_remote_outbox o
set status = 'cancelled',
    secure_payload_ciphertext = '',
    lease_owner = null,
    lease_until = null,
    safe_error_code = 'AUTH_FLOW_TERMINAL',
    updated_at = clock_timestamp()
where o.event_type = 'NEXT_AUTH'
  and o.status in ('pending', 'running', 'retry')
  and exists (
      select 1
      from public.oasis_claim_remote_jobs j
      where j.owner_user_id = o.owner_user_id
        and j.invite_id = o.invite_id
        and j.status in ('complete', 'partial', 'failed', 'expired', 'cancelled')
  );


create or replace function public.oasis_claim_remote_activate_reserved_job(
    p_owner_user_id text,
    p_job_id uuid,
    p_case_id uuid,
    p_secure_payload_ciphertext text,
    p_stage text
)
returns setof public.oasis_claim_remote_jobs
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_now timestamptz := clock_timestamp();
    v_stage text := lower(trim(p_stage));
    v_job public.oasis_claim_remote_jobs;
begin
    if p_job_id is null
       or p_case_id is null
       or v_stage !~ '^[a-z0-9._-]{1,80}$'
       or v_stage = 'submission_reserved'
       or length(coalesce(p_secure_payload_ciphertext, '')) < 40 then
        raise exception 'REMOTE_JOB_INVALID';
    end if;

    select j.*
    into v_job
    from public.oasis_claim_remote_jobs j
    where j.id = p_job_id
      and j.owner_user_id = lower(trim(p_owner_user_id))
      and j.case_id = p_case_id
    for update;

    if not found
       or v_job.status <> 'waiting'
       or v_job.stage <> 'submission_reserved'
       or v_job.lease_owner is not null then
        raise exception 'REMOTE_JOB_NOT_RESERVED';
    end if;

    if v_job.hard_expires_at <= v_now
       or v_job.sensitive_expires_at is null
       or v_job.sensitive_expires_at <= v_now then
        update public.oasis_claim_remote_jobs j
        set status = 'expired',
            secure_payload_ciphertext = '',
            sensitive_expires_at = null,
            lease_owner = null,
            lease_until = null,
            safe_error_code = case
                when j.hard_expires_at <= v_now then 'JOB_TTL_EXPIRED'
                else 'SENSITIVE_TTL_EXPIRED'
            end,
            updated_at = v_now
        where j.id = v_job.id;
        -- An empty SETOF response commits the fail-closed cleanup while the
        -- repository converts it into an activation error.
        return;
    end if;

    update public.oasis_claim_remote_jobs j
    set status = 'queued',
        stage = v_stage,
        secure_payload_ciphertext = p_secure_payload_ciphertext,
        sensitive_expires_at = least(
            j.hard_expires_at,
            v_now + interval '10 minutes'
        ),
        progress = 0,
        next_run_at = v_now,
        safe_message = '',
        safe_error_code = '',
        updated_at = v_now
    where j.id = v_job.id
    returning j.* into v_job;

    return next v_job;
end;
$$;


create or replace function public.oasis_claim_remote_fail_reserved_job(
    p_owner_user_id text,
    p_job_id uuid,
    p_case_id uuid,
    p_safe_error_code text,
    p_safe_message text default ''
)
returns setof public.oasis_claim_remote_jobs
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_now timestamptz := clock_timestamp();
    v_error_code text := upper(trim(p_safe_error_code));
    v_job public.oasis_claim_remote_jobs;
begin
    if p_job_id is null
       or p_case_id is null
       or v_error_code !~ '^[A-Z0-9_-]{1,80}$' then
        raise exception 'REMOTE_JOB_INVALID';
    end if;

    select j.*
    into v_job
    from public.oasis_claim_remote_jobs j
    where j.id = p_job_id
      and j.owner_user_id = lower(trim(p_owner_user_id))
      and j.case_id = p_case_id
    for update;

    if not found
       or v_job.status <> 'waiting'
       or v_job.stage <> 'submission_reserved'
       or v_job.lease_owner is not null then
        raise exception 'REMOTE_JOB_NOT_RESERVED';
    end if;

    update public.oasis_claim_remote_jobs j
    set status = 'failed',
        stage = 'submission_failed',
        secure_payload_ciphertext = '',
        sensitive_expires_at = null,
        progress = 0,
        next_run_at = v_now,
        safe_message = left(coalesce(p_safe_message, ''), 500),
        safe_error_code = v_error_code,
        updated_at = v_now
    where j.id = v_job.id
    returning j.* into v_job;

    return next v_job;
end;
$$;


create or replace function public.oasis_claim_remote_lease_jobs(
    p_worker_id text,
    p_limit integer default 1,
    p_lease_seconds integer default 60
)
returns setof public.oasis_claim_remote_jobs
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_now timestamptz := clock_timestamp();
    v_worker_id text := trim(p_worker_id);
    v_limit integer := greatest(1, least(coalesce(p_limit, 1), 50));
    v_lease_seconds integer :=
        greatest(15, least(coalesce(p_lease_seconds, 60), 600));
begin
    if coalesce(v_worker_id, '') = '' or length(v_worker_id) > 120 then
        raise exception 'REMOTE_WORKER_INVALID';
    end if;

    update public.oasis_claim_remote_jobs j
    set status = 'expired',
        secure_payload_ciphertext = '',
        sensitive_expires_at = null,
        lease_owner = null,
        lease_until = null,
        safe_error_code = case
            when j.hard_expires_at <= v_now then 'JOB_TTL_EXPIRED'
            else 'SENSITIVE_TTL_EXPIRED'
        end,
        updated_at = v_now
    where j.status in ('queued', 'running', 'waiting', 'retry')
      and (
          j.hard_expires_at <= v_now
          or j.sensitive_expires_at is null
          or j.sensitive_expires_at <= v_now
      );

    update public.oasis_claim_remote_jobs j
    set status = 'failed',
        secure_payload_ciphertext = '',
        sensitive_expires_at = null,
        lease_owner = null,
        lease_until = null,
        safe_error_code = 'MAX_ATTEMPTS_EXCEEDED',
        updated_at = v_now
    where j.status in ('queued', 'running', 'waiting', 'retry')
      and j.attempt_count >= j.max_attempts
      and (j.lease_until is null or j.lease_until <= v_now);

    return query
    with candidates as (
        select j.id
        from public.oasis_claim_remote_jobs j
        where (
                j.status in ('queued', 'waiting', 'retry')
                or (j.status = 'running' and j.lease_until <= v_now)
            )
          and j.next_run_at <= v_now
          and j.hard_expires_at > v_now
          and j.sensitive_expires_at > v_now
          and j.attempt_count < j.max_attempts
          and (j.lease_until is null or j.lease_until <= v_now)
        order by j.next_run_at asc, j.created_at asc
        for update skip locked
        limit v_limit
    )
    update public.oasis_claim_remote_jobs j
    set status = 'running',
        lease_owner = v_worker_id,
        lease_until = least(
            j.hard_expires_at,
            j.sensitive_expires_at,
            v_now + make_interval(secs => v_lease_seconds)
        ),
        heartbeat_at = v_now,
        attempt_count = j.attempt_count + 1,
        updated_at = v_now
    from candidates c
    where j.id = c.id
    returning j.*;
end;
$$;


-- Drop legacy heartbeat overloads before adding the optional stage parameter.
-- This prevents PostgREST from seeing ambiguous named RPC signatures while
-- still allowing older positional callers to omit the final defaulted value.
drop function if exists public.oasis_claim_remote_heartbeat_job(
    uuid,
    text,
    integer
);

drop function if exists public.oasis_claim_remote_heartbeat_job(
    uuid,
    text,
    integer,
    timestamptz
);

create or replace function public.oasis_claim_remote_heartbeat_job(
    p_job_id uuid,
    p_worker_id text,
    p_lease_seconds integer default 60,
    p_sensitive_expires_at timestamptz default null,
    p_stage text default null
)
returns setof public.oasis_claim_remote_jobs
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_now timestamptz := clock_timestamp();
    v_worker_id text := trim(p_worker_id);
    v_lease_seconds integer :=
        greatest(15, least(coalesce(p_lease_seconds, 60), 600));
    v_sensitive_expires_at timestamptz;
    v_stage text;
    v_collection_stage boolean;
    v_job public.oasis_claim_remote_jobs;
begin
    if coalesce(v_worker_id, '') = '' or length(v_worker_id) > 120 then
        raise exception 'REMOTE_WORKER_INVALID';
    end if;

    select j.*
    into v_job
    from public.oasis_claim_remote_jobs j
    where j.id = p_job_id
      and j.status = 'running'
      and j.lease_owner = v_worker_id
    for update;

    if not found or v_job.lease_until <= v_now then
        raise exception 'REMOTE_JOB_LEASE_LOST';
    end if;

    if v_job.hard_expires_at <= v_now
       or v_job.sensitive_expires_at is null
       or v_job.sensitive_expires_at <= v_now then
        update public.oasis_claim_remote_jobs j
        set status = 'expired',
            secure_payload_ciphertext = '',
            sensitive_expires_at = null,
            lease_owner = null,
            lease_until = null,
            safe_error_code = case
                when j.hard_expires_at <= v_now then 'JOB_TTL_EXPIRED'
                else 'SENSITIVE_TTL_EXPIRED'
            end,
            updated_at = v_now
        where j.id = v_job.id;
        return;
    end if;

    v_stage := lower(
        trim(
            coalesce(
                nullif(trim(coalesce(p_stage, '')), ''),
                v_job.stage
            )
        )
    );
    if v_stage !~ '^[a-z0-9._-]{1,80}$' then
        raise exception 'REMOTE_JOB_STAGE_INVALID';
    end if;

    -- The worker sends `collecting` before the first long document call and
    -- uses `collection_*` for resumable collection states.  Only those
    -- stages may retain the encrypted self-input until the immutable hard
    -- deadline.  Authentication/polling stages remain bounded by ten minutes
    -- and by their existing (non-renewable) sensitive deadline.
    v_collection_stage := (
        v_stage = 'collecting'
        or left(v_stage, 11) = 'collection_'
    );

    if v_collection_stage then
        v_sensitive_expires_at := v_job.hard_expires_at;
    else
        v_sensitive_expires_at := least(
            v_job.hard_expires_at,
            v_job.sensitive_expires_at,
            coalesce(
                p_sensitive_expires_at,
                v_job.sensitive_expires_at
            ),
            v_now + interval '10 minutes'
        );
    end if;

    if v_sensitive_expires_at <= v_now then
        update public.oasis_claim_remote_jobs j
        set status = 'expired',
            secure_payload_ciphertext = '',
            sensitive_expires_at = null,
            lease_owner = null,
            lease_until = null,
            safe_error_code = 'SENSITIVE_TTL_EXPIRED',
            updated_at = v_now
        where j.id = v_job.id;
        return;
    end if;

    update public.oasis_claim_remote_jobs j
    set stage = v_stage,
        lease_until = least(
            j.hard_expires_at,
            v_sensitive_expires_at,
            v_now + make_interval(secs => v_lease_seconds)
        ),
        sensitive_expires_at = v_sensitive_expires_at,
        heartbeat_at = v_now,
        updated_at = v_now
    where j.id = v_job.id
    returning j.* into v_job;

    return next v_job;
end;
$$;


-- Drop the legacy signature before adding the sensitive deadline parameter.
-- This prevents PostgREST from seeing ambiguous release overloads.
drop function if exists public.oasis_claim_remote_release_job(
    uuid,
    text,
    text,
    text,
    text,
    text,
    integer,
    timestamptz,
    text,
    text
);

create or replace function public.oasis_claim_remote_release_job(
    p_job_id uuid,
    p_worker_id text,
    p_next_status text,
    p_stage text,
    p_secure_payload_ciphertext text,
    p_payload_key_version text,
    p_progress integer default 0,
    p_next_run_at timestamptz default null,
    p_safe_message text default '',
    p_safe_error_code text default '',
    p_sensitive_expires_at timestamptz default null
)
returns setof public.oasis_claim_remote_jobs
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_now timestamptz := clock_timestamp();
    v_job public.oasis_claim_remote_jobs;
    v_status text := lower(trim(p_next_status));
    v_stage text := lower(trim(p_stage));
    v_terminal boolean;
    v_ciphertext text := coalesce(p_secure_payload_ciphertext, '');
    v_key_version text := trim(coalesce(p_payload_key_version, ''));
    v_error_code text := upper(trim(coalesce(p_safe_error_code, '')));
    v_sensitive_expires_at timestamptz;
    v_collection_stage boolean;
begin
    select j.*
    into v_job
    from public.oasis_claim_remote_jobs j
    where j.id = p_job_id
      and j.status = 'running'
      and j.lease_owner = trim(p_worker_id)
    for update;

    if not found or v_job.lease_until <= v_now then
        raise exception 'REMOTE_JOB_LEASE_LOST';
    end if;

    if v_job.hard_expires_at <= v_now
       or v_job.sensitive_expires_at is null
       or v_job.sensitive_expires_at <= v_now then
        update public.oasis_claim_remote_jobs j
        set status = 'expired',
            secure_payload_ciphertext = '',
            sensitive_expires_at = null,
            lease_owner = null,
            lease_until = null,
            safe_error_code = case
                when j.hard_expires_at <= v_now then 'JOB_TTL_EXPIRED'
                else 'SENSITIVE_TTL_EXPIRED'
            end,
            updated_at = v_now
        where j.id = v_job.id
        returning j.* into v_job;
        return next v_job;
        return;
    end if;

    if v_status not in (
        'queued',
        'waiting',
        'retry',
        'complete',
        'partial',
        'failed',
        'expired',
        'cancelled'
    ) then
        raise exception 'REMOTE_JOB_STATUS_INVALID';
    end if;
    if v_stage !~ '^[a-z0-9._-]{1,80}$' then
        raise exception 'REMOTE_JOB_STAGE_INVALID';
    end if;
    if v_error_code <> '' and v_error_code !~ '^[A-Z0-9_-]{1,80}$' then
        raise exception 'REMOTE_JOB_ERROR_CODE_INVALID';
    end if;

    if v_status in ('queued', 'waiting', 'retry')
       and v_job.attempt_count >= v_job.max_attempts then
        v_status := 'failed';
        v_error_code := 'MAX_ATTEMPTS_EXCEEDED';
    end if;

    v_terminal := v_status in (
        'complete',
        'partial',
        'failed',
        'expired',
        'cancelled'
    );

    if not v_terminal
       and (
           length(v_ciphertext) < 40
           or v_key_version !~ '^[A-Za-z0-9._-]{1,40}$'
       ) then
        raise exception 'REMOTE_JOB_PAYLOAD_REQUIRED';
    end if;

    if not v_terminal then
        v_collection_stage := (
            v_stage = 'collecting'
            or left(v_stage, 11) = 'collection_'
        );

        if v_collection_stage then
            -- Collection retries must survive long Tilko document calls, but
            -- can never outlive the immutable job hard deadline.
            v_sensitive_expires_at := v_job.hard_expires_at;
        else
            v_sensitive_expires_at := least(
                v_job.hard_expires_at,
                v_job.sensitive_expires_at,
                coalesce(
                    p_sensitive_expires_at,
                    v_job.sensitive_expires_at
                ),
                v_now + interval '10 minutes'
            );
        end if;

        if v_sensitive_expires_at <= v_now then
            update public.oasis_claim_remote_jobs j
            set status = 'expired',
                secure_payload_ciphertext = '',
                sensitive_expires_at = null,
                lease_owner = null,
                lease_until = null,
                safe_error_code = 'SENSITIVE_TTL_EXPIRED',
                updated_at = v_now
            where j.id = v_job.id
            returning j.* into v_job;
            return next v_job;
            return;
        end if;
    end if;

    update public.oasis_claim_remote_jobs j
    set status = v_status,
        stage = v_stage,
        secure_payload_ciphertext = case
            when v_terminal then ''
            else v_ciphertext
        end,
        payload_key_version = case
            when v_terminal then j.payload_key_version
            else v_key_version
        end,
        sensitive_expires_at = case
            when v_terminal then null
            else v_sensitive_expires_at
        end,
        progress = greatest(0, least(coalesce(p_progress, 0), 100)),
        next_run_at = coalesce(p_next_run_at, v_now),
        lease_owner = null,
        lease_until = null,
        safe_message = left(coalesce(p_safe_message, ''), 500),
        safe_error_code = v_error_code,
        updated_at = v_now
    where j.id = v_job.id
    returning j.* into v_job;

    return next v_job;
end;
$$;


create or replace function public.oasis_claim_remote_expire_due()
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_now timestamptz := clock_timestamp();
    v_invites integer := 0;
    v_jobs integer := 0;
    v_messages integer := 0;
    v_guidance_reconciled integer := 0;
    v_guidance_reconciliation_failed boolean := false;
begin
    update public.oasis_claim_remote_invites
    set status = 'expired',
        secure_payload_ciphertext = '',
        updated_at = v_now
    where status in ('created', 'queued', 'sent', 'opened')
      and expires_at <= v_now;
    get diagnostics v_invites = row_count;

    update public.oasis_claim_remote_jobs j
    set status = 'expired',
        secure_payload_ciphertext = '',
        sensitive_expires_at = null,
        lease_owner = null,
        lease_until = null,
        safe_error_code = case
            when j.hard_expires_at <= v_now then 'JOB_TTL_EXPIRED'
            else 'SENSITIVE_TTL_EXPIRED'
        end,
        updated_at = v_now
    where j.status in ('queued', 'running', 'waiting', 'retry')
      and (
          j.hard_expires_at <= v_now
          or j.sensitive_expires_at is null
          or j.sensitive_expires_at <= v_now
      );
    get diagnostics v_jobs = row_count;

    update public.oasis_claim_remote_outbox
    set status = 'expired',
        secure_payload_ciphertext = '',
        lease_owner = null,
        lease_until = null,
        safe_error_code = 'MESSAGE_TTL_EXPIRED',
        updated_at = v_now
    where status in ('pending', 'running', 'retry')
      and expires_at <= v_now;
    get diagnostics v_messages = row_count;

    -- Privacy erasure above is deliberately independent of non-privacy CRM
    -- reconciliation.  A corrupt guidance row can therefore never roll back
    -- expired authentication ciphertext cleanup.
    begin
        v_guidance_reconciled :=
            public.oasis_reconcile_company_kakao_guidance_outbox(1000);
    exception
        when others then
            -- Do not persist SQL error text: it can contain unsafe context.
            v_guidance_reconciliation_failed := true;
            v_guidance_reconciled := 0;
    end;

    return jsonb_build_object(
        'invites', v_invites,
        'jobs', v_jobs,
        'messages', v_messages,
        'guidance_reconciled', v_guidance_reconciled,
        'guidance_reconciliation_failed',
            v_guidance_reconciliation_failed
    );
end;
$$;


create or replace function public.oasis_claim_remote_retention_health()
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_now timestamptz := clock_timestamp();
    v_cron_job_present boolean := false;
    v_cron_job_active boolean := false;
    v_cron_schedule text := '';
    v_cron_job_count bigint := 0;
    v_cron_command_exact boolean := false;
    v_cron_database_exact boolean := false;
    v_cron_role_can_execute boolean := false;
    v_overdue_jobs bigint := 0;
    v_overdue_outbox bigint := 0;
    v_guidance_reconciliation bigint := 0;
    v_unconfirmed_guidance_dispatch bigint := 0;
begin
    select
        true,
        coalesce(c.active, false),
        coalesce(c.schedule, '')
    into
        v_cron_job_present,
        v_cron_job_active,
        v_cron_schedule
    from cron.job c
    where c.jobname = 'oasis-claim-auth-payload-expiry-v910'
    order by c.jobid desc
    limit 1;

    select
        count(*),
        coalesce(bool_and(
            btrim(c.command) =
                'select public.oasis_claim_remote_expire_due();'
        ), false),
        coalesce(bool_and(c.database = current_database()), false),
        coalesce(bool_and(pg_catalog.has_function_privilege(
            c.username,
            'public.oasis_claim_remote_expire_due()',
            'EXECUTE'
        )), false)
    into
        v_cron_job_count,
        v_cron_command_exact,
        v_cron_database_exact,
        v_cron_role_can_execute
    from cron.job c
    where c.jobname = 'oasis-claim-auth-payload-expiry-v910';

    select count(*)
    into v_overdue_jobs
    from public.oasis_claim_remote_jobs j
    where j.status in ('queued', 'running', 'waiting', 'retry')
      and (
          j.hard_expires_at <= v_now
          or j.sensitive_expires_at is null
          or j.sensitive_expires_at <= v_now
      );

    select count(*)
    into v_overdue_outbox
    from public.oasis_claim_remote_outbox o
    where o.status in ('pending', 'running', 'retry')
      and o.expires_at <= v_now;

    select count(*)
    into v_guidance_reconciliation
    from public.oasis_company_kakao_guidance_messages m
    join public.oasis_claim_remote_outbox o
      on o.guidance_message_id = m.id
    where o.status in ('sent', 'delivered', 'failed', 'expired', 'cancelled')
      and (
          (
              o.status = 'delivered'
              and m.status in ('queued', 'sending', 'sent')
          )
          or (
              o.status = 'sent'
              and m.status in ('queued', 'sending')
          )
          or (
              o.status in ('failed', 'expired', 'cancelled')
              and m.status in ('queued', 'sending')
          )
          or (
              o.status = 'sent'
              and o.provider_message_id <> ''
              and m.status = 'sent'
              and m.provider_message_id is distinct from o.provider_message_id
          )
      );

    select count(*)
    into v_unconfirmed_guidance_dispatch
    from public.oasis_claim_remote_outbox o
    where o.guidance_dispatch_started_at is not null
      and o.status = 'running';

    return jsonb_build_object(
        'checked_at', v_now,
        'cron_job_present', v_cron_job_present,
        'cron_job_count', v_cron_job_count,
        'cron_job_active', v_cron_job_active,
        'cron_schedule', v_cron_schedule,
        'cron_command_exact', v_cron_command_exact,
        'cron_database_exact', v_cron_database_exact,
        'cron_role_can_execute', v_cron_role_can_execute,
        'overdue_job_count', v_overdue_jobs,
        'overdue_outbox_count', v_overdue_outbox,
        'guidance_reconciliation_count', v_guidance_reconciliation,
        'guidance_reconciliation_pending',
            v_guidance_reconciliation > 0,
        'unconfirmed_guidance_dispatch_count',
            v_unconfirmed_guidance_dispatch
    );
end;
$$;


alter table public.oasis_company_kakao_guidance_settings enable row level security;
alter table public.oasis_company_kakao_guidance_messages enable row level security;
alter table public.oasis_company_kakao_contact_controls enable row level security;
alter table public.oasis_company_kakao_guidance_history enable row level security;
alter table public.oasis_company_kakao_followup_outbox enable row level security;

revoke all on table public.oasis_company_kakao_guidance_settings from PUBLIC, anon, authenticated, service_role;
revoke all on table public.oasis_company_kakao_guidance_messages from PUBLIC, anon, authenticated, service_role;
revoke all on table public.oasis_company_kakao_contact_controls from PUBLIC, anon, authenticated, service_role;
revoke all on table public.oasis_company_kakao_guidance_history from PUBLIC, anon, authenticated, service_role;
revoke all on table public.oasis_company_kakao_followup_outbox from PUBLIC, anon, authenticated, service_role;
grant select, insert, update, delete on table public.oasis_company_kakao_guidance_settings to service_role;
grant select, insert, update, delete on table public.oasis_company_kakao_guidance_messages to service_role;
grant select, insert, update, delete on table public.oasis_company_kakao_contact_controls to service_role;
grant select, insert on table public.oasis_company_kakao_guidance_history to service_role;
grant select, insert, update, delete on table public.oasis_company_kakao_followup_outbox to service_role;
revoke all on sequence public.oasis_company_kakao_guidance_history_id_seq from PUBLIC, anon, authenticated, service_role;
grant usage, select on sequence public.oasis_company_kakao_guidance_history_id_seq to service_role;

do $$
declare fn record;
begin
    for fn in
        select p.proname, pg_get_function_identity_arguments(p.oid) identity_arguments
        from pg_proc p join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'public'
          and p.proname in (
            'oasis_company_kakao_touch_updated_at',
            'oasis_company_kakao_history_immutable',
            'oasis_company_kakao_add_business_days',
            'oasis_company_kakao_write_history',
            'oasis_company_kakao_guidance_feature_ready',
            'oasis_resolve_company_kakao_guidance_mobile',
            'oasis_check_company_kakao_guidance_eligibility',
            'oasis_check_company_kakao_guidance_send_ready',
            'oasis_reserve_company_kakao_guidance',
            'oasis_attach_company_kakao_guidance_invite',
            'oasis_finalize_company_kakao_guidance',
            'oasis_reconcile_company_kakao_guidance_outbox',
            'oasis_cancel_company_kakao_guidance',
            'oasis_cancel_company_kakao_guidance_for_invite',
            'oasis_cancel_company_kakao_delivery_for_control',
            'oasis_set_company_kakao_contact_control',
            'oasis_get_company_kakao_guidance_settings',
            'oasis_update_company_kakao_guidance_settings',
            'oasis_list_company_kakao_guidance',
            'oasis_admin_list_company_kakao_guidance',
            'oasis_claim_remote_cancel_invite',
             'oasis_claim_remote_get_session_status',
             'oasis_claim_remote_check_job_active',
             'oasis_claim_remote_consume_invite',
             'oasis_claim_remote_enqueue_outbox',
             'oasis_claim_remote_begin_guidance_dispatch',
             'oasis_claim_remote_purge_auth_outbox_terminal',
             'oasis_claim_remote_activate_reserved_job',
            'oasis_claim_remote_fail_reserved_job',
            'oasis_claim_remote_lease_jobs',
            'oasis_claim_remote_heartbeat_job',
            'oasis_claim_remote_release_job',
            'oasis_claim_remote_expire_due',
            'oasis_claim_remote_retention_health'
          )
    loop
        execute format('revoke all on function public.%I(%s) from PUBLIC, anon, authenticated, service_role', fn.proname, fn.identity_arguments);
        execute format('grant execute on function public.%I(%s) to service_role', fn.proname, fn.identity_arguments);
    end loop;
end;
$$;

comment on table public.oasis_company_kakao_guidance_messages is
    '발송 이력. 휴대전화 원문은 금지하고 HMAC-SHA256 지문만 저장한다.';
comment on column public.oasis_company_kakao_guidance_messages.recipient_phone_hash is
    '발송 전용 공개 DB 휴대전화의 HMAC 지문. 고객 self-input 인증번호와 비교하거나 연결하지 않는다.';
comment on column public.oasis_company_kakao_guidance_messages.recipient_contact_id is
    'Trusted resolver가 선택한 공개 연락처 행. live 발송 목적지 결속 및 최종 발송 직전 DNC 재검사용이며 인증번호와 무관하다.';
comment on column public.oasis_company_kakao_guidance_messages.recipient_contact_updated_at is
    '발송 예약 시점의 공개 연락처 행 버전. 원문 번호를 저장하지 않고 예약 후 연락처 변경을 최종 발송 직전에 차단한다.';
comment on table public.oasis_company_kakao_guidance_history is
    '개인정보 원문 없이 남기는 append-only 안내 감사이력.';
comment on table public.oasis_company_kakao_guidance_settings is
    'DB 보조 발송 스위치/일일한도. Railway SEND_ENABLED 환경변수 hard gate를 우회할 수 없다.';

comment on column public.oasis_claim_remote_jobs.sensitive_expires_at is
    'Encrypted customer self-input idle deadline. It is separate from the public guidance-send contact and never authorizes a recipient-number match.';
comment on column public.oasis_claim_remote_outbox.guidance_message_id is
    'Non-PII binding from the exact encrypted guidance delivery row to its canonical-contact reservation. It is never an authentication-phone identifier.';
comment on column public.oasis_claim_remote_outbox.guidance_dispatch_started_at is
    'At-most-once provider boundary. When set, the public delivery ciphertext was erased before the external request and the row must never be automatically retried.';

-- Encrypted simple-auth payloads carry database expiry timestamps and are also
-- cleared by the Railway worker.  Register a mandatory one-minute database
-- sweep so worker downtime cannot leave expired ciphertext behind.
do $oasis_guidance_auth_expiry_cron$
declare
    v_job_id bigint;
    v_job_count bigint;
    v_active boolean;
    v_schedule text;
    v_command text;
    v_database text;
    v_role_can_execute boolean;
begin
    select cron.schedule(
        'oasis-claim-auth-payload-expiry-v910',
        '* * * * *',
        'select public.oasis_claim_remote_expire_due();'
    )
    into v_job_id;

    select c.active, c.schedule, c.command, c.database
    into v_active, v_schedule, v_command, v_database
    from cron.job c
    where c.jobid = v_job_id;

    select count(*)
    into v_job_count
    from cron.job c
    where c.jobname = 'oasis-claim-auth-payload-expiry-v910';

    select pg_catalog.has_function_privilege(
        c.username,
        'public.oasis_claim_remote_expire_due()',
        'EXECUTE'
    )
    into v_role_can_execute
    from cron.job c
    where c.jobid = v_job_id;

    if not found
       or v_job_count <> 1
       or not coalesce(v_active, false)
       or not coalesce(v_role_can_execute, false)
       or v_schedule <> '* * * * *'
       or v_database is distinct from current_database()
       or btrim(v_command) <> 'select public.oasis_claim_remote_expire_due();' then
        raise exception 'REMOTE_RETENTION_CRON_REGISTRATION_FAILED';
    end if;
end;
$oasis_guidance_auth_expiry_cron$;

-- The heartbeat signature gains an optional stage parameter in this
-- migration.  Ask PostgREST to refresh its function catalog after commit so
-- named RPC calls do not remain pinned to the removed overload.
notify pgrst, 'reload schema';

commit;
