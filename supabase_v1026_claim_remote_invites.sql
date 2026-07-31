-- OASIS CRM v10.2.6 - 원격 경정청구 초대, durable job, 알림 outbox
-- 공개 고객 브라우저는 이 테이블이나 RPC를 직접 호출하지 않는다.
-- Railway의 service_role 서버와 worker만 아래 RPC를 호출한다.

begin;

create table if not exists public.oasis_claim_remote_invites (
    id uuid primary key,
    owner_user_id text not null,
    token_hash text not null unique,
    status text not null default 'created'
        check (
            status in (
                'created',
                'queued',
                'sent',
                'opened',
                'submitted',
                'expired',
                'cancelled',
                'send_failed'
            )
        ),
    recipient_name_masked text not null default '',
    recipient_phone_masked text not null default '',
    secure_payload_ciphertext text not null,
    payload_key_version text not null,
    expires_at timestamptz not null,
    opened_at timestamptz,
    consumed_at timestamptz,
    case_id uuid,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint oasis_claim_remote_invites_owner_required
        check (
            owner_user_id = lower(trim(owner_user_id))
            and length(owner_user_id) between 1 and 200
        ),
    constraint oasis_claim_remote_invites_token_hash_format
        check (token_hash ~ '^[0-9a-f]{64}$'),
    constraint oasis_claim_remote_invites_key_version_format
        check (
            payload_key_version ~ '^[A-Za-z0-9._-]{1,40}$'
        ),
    constraint oasis_claim_remote_invites_expiry_order
        check (expires_at > created_at),
    constraint oasis_claim_remote_invites_ciphertext_lifecycle
        check (
            (
                status in (
                    'submitted',
                    'expired',
                    'cancelled',
                    'send_failed'
                )
                and secure_payload_ciphertext = ''
            )
            or (
                status in (
                    'created',
                    'queued',
                    'sent',
                    'opened'
                )
                and length(secure_payload_ciphertext) >= 40
            )
        ),
    constraint oasis_claim_remote_invites_consumed_state
        check (
            (
                status = 'submitted'
                and consumed_at is not null
                and case_id is not null
            )
            or status <> 'submitted'
        ),
    constraint oasis_claim_remote_invites_id_owner_key
        unique (id, owner_user_id)
);

create unique index if not exists
    oasis_claim_remote_invites_case_unique_idx
    on public.oasis_claim_remote_invites (case_id)
    where case_id is not null;

create index if not exists oasis_claim_remote_invites_owner_status_idx
    on public.oasis_claim_remote_invites (
        owner_user_id,
        status,
        created_at desc
    );

create index if not exists oasis_claim_remote_invites_expiry_idx
    on public.oasis_claim_remote_invites (expires_at)
    where status in ('created', 'queued', 'sent', 'opened');


create table if not exists public.oasis_claim_remote_jobs (
    id uuid primary key,
    owner_user_id text not null,
    invite_id uuid not null,
    case_id uuid not null,
    stage text not null,
    status text not null default 'queued'
        check (
            status in (
                'queued',
                'running',
                'waiting',
                'retry',
                'complete',
                'partial',
                'failed',
                'expired',
                'cancelled'
            )
        ),
    secure_payload_ciphertext text not null,
    payload_key_version text not null,
    progress integer not null default 0
        check (progress between 0 and 100),
    next_run_at timestamptz not null default now(),
    lease_owner text,
    lease_until timestamptz,
    attempt_count integer not null default 0
        check (attempt_count >= 0),
    max_attempts integer not null default 12
        check (max_attempts between 1 and 100),
    hard_expires_at timestamptz not null,
    heartbeat_at timestamptz,
    safe_message text not null default '',
    safe_error_code text not null default '',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint oasis_claim_remote_jobs_owner_required
        check (
            owner_user_id = lower(trim(owner_user_id))
            and length(owner_user_id) between 1 and 200
        ),
    constraint oasis_claim_remote_jobs_stage_format
        check (stage ~ '^[a-z0-9._-]{1,80}$'),
    constraint oasis_claim_remote_jobs_key_version_format
        check (
            payload_key_version ~ '^[A-Za-z0-9._-]{1,40}$'
        ),
    constraint oasis_claim_remote_jobs_error_code_format
        check (
            safe_error_code = ''
            or safe_error_code ~ '^[A-Z0-9_-]{1,80}$'
        ),
    constraint oasis_claim_remote_jobs_expiry_order
        check (hard_expires_at > created_at),
    constraint oasis_claim_remote_jobs_lease_pair
        check (
            (lease_owner is null and lease_until is null)
            or (
                nullif(trim(lease_owner), '') is not null
                and lease_until is not null
            )
        ),
    constraint oasis_claim_remote_jobs_ciphertext_lifecycle
        check (
            (
                status in (
                    'complete',
                    'partial',
                    'failed',
                    'expired',
                    'cancelled'
                )
                and secure_payload_ciphertext = ''
            )
            or (
                status in ('queued', 'running', 'waiting', 'retry')
                and length(secure_payload_ciphertext) >= 40
            )
        ),
    constraint oasis_claim_remote_jobs_invite_owner_fkey
        foreign key (invite_id, owner_user_id)
        references public.oasis_claim_remote_invites(id, owner_user_id)
        on delete restrict,
    constraint oasis_claim_remote_jobs_invite_unique
        unique (invite_id),
    constraint oasis_claim_remote_jobs_case_unique
        unique (case_id)
);

create index if not exists oasis_claim_remote_jobs_due_idx
    on public.oasis_claim_remote_jobs (
        next_run_at,
        created_at
    )
    where status in ('queued', 'waiting', 'retry', 'running');

create index if not exists oasis_claim_remote_jobs_lease_idx
    on public.oasis_claim_remote_jobs (lease_until)
    where status = 'running';

create index if not exists oasis_claim_remote_jobs_owner_case_idx
    on public.oasis_claim_remote_jobs (owner_user_id, case_id);

create index if not exists oasis_claim_remote_jobs_expiry_idx
    on public.oasis_claim_remote_jobs (hard_expires_at)
    where status in ('queued', 'running', 'waiting', 'retry');


create table if not exists public.oasis_claim_remote_outbox (
    id uuid primary key,
    owner_user_id text not null,
    invite_id uuid,
    case_id uuid,
    event_type text not null,
    template_code text not null,
    idempotency_key text not null,
    status text not null default 'pending'
        check (
            status in (
                'pending',
                'running',
                'retry',
                'sent',
                'delivered',
                'failed',
                'expired',
                'cancelled'
            )
        ),
    secure_payload_ciphertext text not null,
    payload_key_version text not null,
    provider_message_id text not null default '',
    run_after timestamptz not null default now(),
    expires_at timestamptz not null,
    lease_owner text,
    lease_until timestamptz,
    attempt_count integer not null default 0
        check (attempt_count >= 0),
    max_attempts integer not null default 8
        check (max_attempts between 1 and 100),
    safe_error_code text not null default '',
    sent_at timestamptz,
    delivered_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint oasis_claim_remote_outbox_owner_required
        check (
            owner_user_id = lower(trim(owner_user_id))
            and length(owner_user_id) between 1 and 200
        ),
    constraint oasis_claim_remote_outbox_target_required
        check (invite_id is not null or case_id is not null),
    constraint oasis_claim_remote_outbox_event_format
        check (event_type ~ '^[A-Z0-9_.-]{1,80}$'),
    constraint oasis_claim_remote_outbox_template_format
        check (template_code ~ '^[A-Za-z0-9_.-]{1,120}$'),
    constraint oasis_claim_remote_outbox_idempotency_format
        check (length(trim(idempotency_key)) between 1 and 200),
    constraint oasis_claim_remote_outbox_key_version_format
        check (
            payload_key_version ~ '^[A-Za-z0-9._-]{1,40}$'
        ),
    constraint oasis_claim_remote_outbox_error_code_format
        check (
            safe_error_code = ''
            or safe_error_code ~ '^[A-Z0-9_-]{1,80}$'
        ),
    constraint oasis_claim_remote_outbox_expiry_order
        check (expires_at > created_at),
    constraint oasis_claim_remote_outbox_lease_pair
        check (
            (lease_owner is null and lease_until is null)
            or (
                nullif(trim(lease_owner), '') is not null
                and lease_until is not null
            )
        ),
    constraint oasis_claim_remote_outbox_ciphertext_lifecycle
        check (
            (
                status in (
                    'sent',
                    'delivered',
                    'failed',
                    'expired',
                    'cancelled'
                )
                and secure_payload_ciphertext = ''
            )
            or (
                status in ('pending', 'running', 'retry')
                and length(secure_payload_ciphertext) >= 40
            )
        ),
    constraint oasis_claim_remote_outbox_invite_owner_fkey
        foreign key (invite_id, owner_user_id)
        references public.oasis_claim_remote_invites(id, owner_user_id)
        on delete restrict,
    constraint oasis_claim_remote_outbox_idempotency_unique
        unique (owner_user_id, idempotency_key)
);

create index if not exists oasis_claim_remote_outbox_due_idx
    on public.oasis_claim_remote_outbox (run_after, created_at)
    where status in ('pending', 'running', 'retry');

create index if not exists oasis_claim_remote_outbox_lease_idx
    on public.oasis_claim_remote_outbox (lease_until)
    where status = 'running';

create index if not exists oasis_claim_remote_outbox_case_idx
    on public.oasis_claim_remote_outbox (
        owner_user_id,
        case_id,
        created_at desc
    );

create index if not exists oasis_claim_remote_outbox_expiry_idx
    on public.oasis_claim_remote_outbox (expires_at)
    where status in ('pending', 'running', 'retry');


create or replace function public.oasis_claim_remote_touch_updated_at()
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

drop trigger if exists oasis_claim_remote_invites_updated_at
    on public.oasis_claim_remote_invites;
create trigger oasis_claim_remote_invites_updated_at
before update on public.oasis_claim_remote_invites
for each row execute function public.oasis_claim_remote_touch_updated_at();

drop trigger if exists oasis_claim_remote_jobs_updated_at
    on public.oasis_claim_remote_jobs;
create trigger oasis_claim_remote_jobs_updated_at
before update on public.oasis_claim_remote_jobs
for each row execute function public.oasis_claim_remote_touch_updated_at();

drop trigger if exists oasis_claim_remote_outbox_updated_at
    on public.oasis_claim_remote_outbox;
create trigger oasis_claim_remote_outbox_updated_at
before update on public.oasis_claim_remote_outbox
for each row execute function public.oasis_claim_remote_touch_updated_at();


create or replace function public.oasis_claim_remote_create_invite(
    p_invite jsonb
)
returns setof public.oasis_claim_remote_invites
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_now timestamptz := clock_timestamp();
    v_id uuid;
    v_owner_user_id text;
    v_token_hash text;
    v_ciphertext text;
    v_key_version text;
    v_expires_at timestamptz;
    v_invite public.oasis_claim_remote_invites;
begin
    if jsonb_typeof(coalesce(p_invite, '{}'::jsonb)) <> 'object' then
        raise exception 'REMOTE_INVITE_INVALID';
    end if;

    v_id := nullif(trim(p_invite ->> 'id'), '')::uuid;
    v_owner_user_id := lower(trim(p_invite ->> 'owner_user_id'));
    v_token_hash := lower(trim(p_invite ->> 'token_hash'));
    v_ciphertext := coalesce(p_invite ->> 'secure_payload_ciphertext', '');
    v_key_version := trim(p_invite ->> 'payload_key_version');
    v_expires_at := nullif(trim(p_invite ->> 'expires_at'), '')::timestamptz;

    if v_id is null
       or coalesce(v_owner_user_id, '') = ''
       or v_token_hash !~ '^[0-9a-f]{64}$'
       or length(v_ciphertext) < 40
       or v_key_version !~ '^[A-Za-z0-9._-]{1,40}$'
       or v_expires_at is null
       or v_expires_at <= v_now then
        raise exception 'REMOTE_INVITE_INVALID';
    end if;

    insert into public.oasis_claim_remote_invites (
        id,
        owner_user_id,
        token_hash,
        status,
        recipient_name_masked,
        recipient_phone_masked,
        secure_payload_ciphertext,
        payload_key_version,
        expires_at,
        created_at,
        updated_at
    )
    values (
        v_id,
        v_owner_user_id,
        v_token_hash,
        'created',
        left(coalesce(p_invite ->> 'recipient_name_masked', ''), 120),
        left(coalesce(p_invite ->> 'recipient_phone_masked', ''), 40),
        v_ciphertext,
        v_key_version,
        v_expires_at,
        v_now,
        v_now
    )
    returning * into v_invite;

    return next v_invite;
end;
$$;


create or replace function public.oasis_claim_remote_get_invite(
    p_owner_user_id text,
    p_token_hash text
)
returns setof public.oasis_claim_remote_invites
language sql
stable
security definer
set search_path = ''
as $$
    select i.*
    from public.oasis_claim_remote_invites i
    where i.owner_user_id = lower(trim(p_owner_user_id))
      and i.token_hash = lower(trim(p_token_hash))
    limit 1;
$$;


create or replace function public.oasis_claim_remote_resolve_invite(
    p_token_hash text
)
returns table (
    id uuid,
    owner_user_id text,
    status text,
    expires_at timestamptz
)
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_now timestamptz := clock_timestamp();
    v_invite public.oasis_claim_remote_invites;
begin
    if lower(trim(p_token_hash)) !~ '^[0-9a-f]{64}$' then
        raise exception 'REMOTE_TOKEN_INVALID';
    end if;

    select i.*
    into v_invite
    from public.oasis_claim_remote_invites i
    where i.token_hash = lower(trim(p_token_hash))
    for update;

    if not found then
        raise exception 'REMOTE_INVITE_NOT_FOUND';
    end if;

    if v_invite.status = 'submitted' then
        raise exception 'REMOTE_INVITE_ALREADY_CONSUMED';
    end if;
    if v_invite.status = 'expired' then
        return query
        select
            v_invite.id,
            v_invite.owner_user_id,
            'expired'::text,
            v_invite.expires_at;
        return;
    end if;
    if v_invite.status not in ('created', 'queued', 'sent', 'opened') then
        raise exception 'REMOTE_INVITE_NOT_ACTIVE';
    end if;
    if v_invite.expires_at <= v_now then
        update public.oasis_claim_remote_invites i
        set
            status = 'expired',
            secure_payload_ciphertext = '',
            updated_at = v_now
        where i.id = v_invite.id
        returning i.* into v_invite;
        return query
        select
            v_invite.id,
            v_invite.owner_user_id,
            v_invite.status,
            v_invite.expires_at;
        return;
    end if;

    return query
    select
        v_invite.id,
        v_invite.owner_user_id,
        v_invite.status,
        v_invite.expires_at;
end;
$$;


create or replace function public.oasis_claim_remote_mark_invite_opened_global(
    p_token_hash text
)
returns table (
    id uuid,
    owner_user_id text,
    status text,
    expires_at timestamptz
)
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_now timestamptz := clock_timestamp();
    v_invite public.oasis_claim_remote_invites;
begin
    if lower(trim(p_token_hash)) !~ '^[0-9a-f]{64}$' then
        raise exception 'REMOTE_TOKEN_INVALID';
    end if;

    select i.*
    into v_invite
    from public.oasis_claim_remote_invites i
    where i.token_hash = lower(trim(p_token_hash))
    for update;

    if not found then
        raise exception 'REMOTE_INVITE_NOT_FOUND';
    end if;

    if v_invite.status = 'submitted' then
        raise exception 'REMOTE_INVITE_ALREADY_CONSUMED';
    end if;
    if v_invite.status = 'expired' then
        return query
        select
            v_invite.id,
            v_invite.owner_user_id,
            'expired'::text,
            v_invite.expires_at;
        return;
    end if;
    if v_invite.status not in ('created', 'queued', 'sent', 'opened') then
        raise exception 'REMOTE_INVITE_NOT_ACTIVE';
    end if;
    if v_invite.expires_at <= v_now then
        update public.oasis_claim_remote_invites i
        set
            status = 'expired',
            secure_payload_ciphertext = '',
            updated_at = v_now
        where i.id = v_invite.id
        returning i.* into v_invite;
        return query
        select
            v_invite.id,
            v_invite.owner_user_id,
            v_invite.status,
            v_invite.expires_at;
        return;
    end if;

    update public.oasis_claim_remote_invites i
    set
        status = 'opened',
        opened_at = coalesce(i.opened_at, v_now),
        updated_at = v_now
    where i.id = v_invite.id
    returning i.* into v_invite;

    return query
    select
        v_invite.id,
        v_invite.owner_user_id,
        v_invite.status,
        v_invite.expires_at;
end;
$$;


create or replace function public.oasis_claim_remote_mark_invite_opened(
    p_owner_user_id text,
    p_token_hash text
)
returns setof public.oasis_claim_remote_invites
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
    where i.owner_user_id = lower(trim(p_owner_user_id))
      and i.token_hash = lower(trim(p_token_hash))
    for update;

    if not found then
        raise exception 'REMOTE_INVITE_NOT_FOUND';
    end if;

    if v_invite.expires_at <= v_now then
        update public.oasis_claim_remote_invites
        set
            status = 'expired',
            secure_payload_ciphertext = '',
            updated_at = v_now
        where id = v_invite.id
        returning * into v_invite;
        return next v_invite;
        return;
    end if;

    if v_invite.status not in ('created', 'queued', 'sent', 'opened') then
        if v_invite.status = 'submitted' then
            raise exception 'REMOTE_INVITE_ALREADY_CONSUMED';
        end if;
        raise exception 'REMOTE_INVITE_NOT_ACTIVE';
    end if;

    update public.oasis_claim_remote_invites
    set
        status = 'opened',
        opened_at = coalesce(opened_at, v_now),
        updated_at = v_now
    where id = v_invite.id
    returning * into v_invite;

    return next v_invite;
end;
$$;


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
        set
            status = 'expired',
            secure_payload_ciphertext = '',
            updated_at = v_now
        where i.id = v_invite.id
        returning i.* into v_invite;
    end if;

    update public.oasis_claim_remote_jobs j
    set
        status = 'expired',
        secure_payload_ciphertext = '',
        lease_owner = null,
        lease_until = null,
        safe_error_code = 'JOB_TTL_EXPIRED',
        updated_at = v_now
    where j.invite_id = v_invite.id
      and j.owner_user_id = v_invite.owner_user_id
      and j.status in ('queued', 'running', 'waiting', 'retry')
      and j.hard_expires_at <= v_now;

    return query
    select
        i.id,
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
    v_ciphertext text;
    v_key_version text;
    v_hard_expires_at timestamptz;
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
    v_ciphertext := coalesce(p_job ->> 'secure_payload_ciphertext', '');
    v_key_version := trim(p_job ->> 'payload_key_version');
    v_hard_expires_at :=
        nullif(trim(p_job ->> 'hard_expires_at'), '')::timestamptz;
    v_max_attempts := greatest(
        1,
        least(coalesce((p_job ->> 'max_attempts')::integer, 12), 100)
    );

    if v_job_id is null
       or v_case_id is null
       or v_stage !~ '^[a-z0-9._-]{1,80}$'
       or length(v_ciphertext) < 40
       or v_key_version !~ '^[A-Za-z0-9._-]{1,40}$'
       or v_hard_expires_at is null
       or v_hard_expires_at <= v_now then
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
        created_at,
        updated_at
    )
    values (
        v_job_id,
        v_invite.owner_user_id,
        v_invite.id,
        v_case_id,
        v_stage,
        'queued',
        v_ciphertext,
        v_key_version,
        v_now,
        v_max_attempts,
        v_hard_expires_at,
        v_now,
        v_now
    )
    returning * into v_job;

    update public.oasis_claim_remote_invites
    set
        status = 'submitted',
        secure_payload_ciphertext = '',
        consumed_at = v_now,
        case_id = v_case_id,
        updated_at = v_now
    where id = v_invite.id;

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

    update public.oasis_claim_remote_jobs
    set
        status = 'expired',
        secure_payload_ciphertext = '',
        lease_owner = null,
        lease_until = null,
        safe_error_code = 'JOB_TTL_EXPIRED',
        updated_at = v_now
    where status in ('queued', 'running', 'waiting', 'retry')
      and hard_expires_at <= v_now;

    update public.oasis_claim_remote_jobs
    set
        status = 'failed',
        secure_payload_ciphertext = '',
        lease_owner = null,
        lease_until = null,
        safe_error_code = 'MAX_ATTEMPTS_EXCEEDED',
        updated_at = v_now
    where status in ('queued', 'running', 'waiting', 'retry')
      and attempt_count >= max_attempts
      and (lease_until is null or lease_until <= v_now);

    return query
    with candidates as (
        select j.id
        from public.oasis_claim_remote_jobs j
        where (
                j.status in ('queued', 'waiting', 'retry')
                or (
                    j.status = 'running'
                    and j.lease_until <= v_now
                )
            )
          and j.next_run_at <= v_now
          and j.hard_expires_at > v_now
          and j.attempt_count < j.max_attempts
          and (j.lease_until is null or j.lease_until <= v_now)
        order by j.next_run_at asc, j.created_at asc
        for update skip locked
        limit v_limit
    )
    update public.oasis_claim_remote_jobs j
    set
        status = 'running',
        lease_owner = v_worker_id,
        lease_until = least(
            j.hard_expires_at,
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


create or replace function public.oasis_claim_remote_heartbeat_job(
    p_job_id uuid,
    p_worker_id text,
    p_lease_seconds integer default 60
)
returns setof public.oasis_claim_remote_jobs
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_now timestamptz := clock_timestamp();
    v_lease_seconds integer :=
        greatest(15, least(coalesce(p_lease_seconds, 60), 600));
    v_job public.oasis_claim_remote_jobs;
begin
    update public.oasis_claim_remote_jobs j
    set
        lease_until = least(
            j.hard_expires_at,
            v_now + make_interval(secs => v_lease_seconds)
        ),
        heartbeat_at = v_now,
        updated_at = v_now
    where j.id = p_job_id
      and j.status = 'running'
      and j.lease_owner = trim(p_worker_id)
      and j.lease_until > v_now
      and j.hard_expires_at > v_now
    returning j.* into v_job;

    if not found then
        raise exception 'REMOTE_JOB_LEASE_LOST';
    end if;

    return next v_job;
end;
$$;


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
    p_safe_error_code text default ''
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
    v_error_code text := upper(trim(coalesce(p_safe_error_code, '')));
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

    if v_job.hard_expires_at <= v_now then
        update public.oasis_claim_remote_jobs
        set
            status = 'expired',
            secure_payload_ciphertext = '',
            lease_owner = null,
            lease_until = null,
            safe_error_code = 'JOB_TTL_EXPIRED',
            updated_at = v_now
        where id = v_job.id
        returning * into v_job;
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
    if v_error_code <> ''
       and v_error_code !~ '^[A-Z0-9_-]{1,80}$' then
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
    if not v_terminal and length(v_ciphertext) < 40 then
        raise exception 'REMOTE_JOB_PAYLOAD_REQUIRED';
    end if;

    update public.oasis_claim_remote_jobs
    set
        status = v_status,
        stage = v_stage,
        secure_payload_ciphertext = case
            when v_terminal then ''
            else v_ciphertext
        end,
        payload_key_version = case
            when v_terminal
            then payload_key_version
            else trim(p_payload_key_version)
        end,
        progress = greatest(0, least(coalesce(p_progress, 0), 100)),
        next_run_at = coalesce(p_next_run_at, v_now),
        lease_owner = null,
        lease_until = null,
        safe_message = left(coalesce(p_safe_message, ''), 500),
        safe_error_code = v_error_code,
        updated_at = v_now
    where id = v_job.id
    returning * into v_job;

    return next v_job;
end;
$$;


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
    v_id uuid;
    v_owner_user_id text;
    v_invite_id uuid;
    v_case_id uuid;
    v_event_type text;
    v_template_code text;
    v_idempotency_key text;
    v_ciphertext text;
    v_key_version text;
    v_run_after timestamptz;
    v_expires_at timestamptz;
    v_max_attempts integer;
begin
    if jsonb_typeof(coalesce(p_message, '{}'::jsonb)) <> 'object' then
        raise exception 'REMOTE_OUTBOX_INVALID';
    end if;

    v_id := nullif(trim(p_message ->> 'id'), '')::uuid;
    v_owner_user_id := lower(trim(p_message ->> 'owner_user_id'));
    v_invite_id := nullif(trim(p_message ->> 'invite_id'), '')::uuid;
    v_case_id := nullif(trim(p_message ->> 'case_id'), '')::uuid;
    v_event_type := upper(trim(p_message ->> 'event_type'));
    v_template_code := trim(p_message ->> 'template_code');
    v_idempotency_key := trim(p_message ->> 'idempotency_key');
    v_ciphertext :=
        coalesce(p_message ->> 'secure_payload_ciphertext', '');
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
       or v_run_after >= v_expires_at then
        raise exception 'REMOTE_OUTBOX_INVALID';
    end if;

    insert into public.oasis_claim_remote_outbox (
        id,
        owner_user_id,
        invite_id,
        case_id,
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
    )
    values (
        v_id,
        v_owner_user_id,
        v_invite_id,
        v_case_id,
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
       or v_message.case_id is distinct from v_case_id then
        raise exception 'REMOTE_OUTBOX_IDEMPOTENCY_CONFLICT';
    end if;

    return next v_message;
end;
$$;


create or replace function public.oasis_claim_remote_lease_outbox(
    p_worker_id text,
    p_limit integer default 10,
    p_lease_seconds integer default 60
)
returns setof public.oasis_claim_remote_outbox
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_now timestamptz := clock_timestamp();
    v_worker_id text := trim(p_worker_id);
    v_limit integer := greatest(1, least(coalesce(p_limit, 10), 100));
    v_lease_seconds integer :=
        greatest(15, least(coalesce(p_lease_seconds, 60), 600));
begin
    if coalesce(v_worker_id, '') = '' or length(v_worker_id) > 120 then
        raise exception 'REMOTE_WORKER_INVALID';
    end if;

    update public.oasis_claim_remote_outbox
    set
        status = 'expired',
        secure_payload_ciphertext = '',
        lease_owner = null,
        lease_until = null,
        safe_error_code = 'MESSAGE_TTL_EXPIRED',
        updated_at = v_now
    where status in ('pending', 'running', 'retry')
      and expires_at <= v_now;

    update public.oasis_claim_remote_outbox
    set
        status = 'failed',
        secure_payload_ciphertext = '',
        lease_owner = null,
        lease_until = null,
        safe_error_code = 'MAX_ATTEMPTS_EXCEEDED',
        updated_at = v_now
    where status in ('pending', 'running', 'retry')
      and attempt_count >= max_attempts
      and (lease_until is null or lease_until <= v_now);

    return query
    with candidates as (
        select o.id
        from public.oasis_claim_remote_outbox o
        where (
                o.status in ('pending', 'retry')
                or (
                    o.status = 'running'
                    and o.lease_until <= v_now
                )
            )
          and o.run_after <= v_now
          and o.expires_at > v_now
          and o.attempt_count < o.max_attempts
          and (o.lease_until is null or o.lease_until <= v_now)
        order by o.run_after asc, o.created_at asc
        for update skip locked
        limit v_limit
    )
    update public.oasis_claim_remote_outbox o
    set
        status = 'running',
        lease_owner = v_worker_id,
        lease_until = least(
            o.expires_at,
            v_now + make_interval(secs => v_lease_seconds)
        ),
        attempt_count = o.attempt_count + 1,
        updated_at = v_now
    from candidates c
    where o.id = c.id
    returning o.*;
end;
$$;


create or replace function public.oasis_claim_remote_release_outbox(
    p_message_id uuid,
    p_worker_id text,
    p_next_status text,
    p_secure_payload_ciphertext text,
    p_payload_key_version text,
    p_provider_message_id text default '',
    p_next_run_at timestamptz default null,
    p_safe_error_code text default ''
)
returns setof public.oasis_claim_remote_outbox
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_now timestamptz := clock_timestamp();
    v_message public.oasis_claim_remote_outbox;
    v_status text := lower(trim(p_next_status));
    v_terminal boolean;
    v_ciphertext text := coalesce(p_secure_payload_ciphertext, '');
    v_error_code text := upper(trim(coalesce(p_safe_error_code, '')));
begin
    select o.*
    into v_message
    from public.oasis_claim_remote_outbox o
    where o.id = p_message_id
      and o.status = 'running'
      and o.lease_owner = trim(p_worker_id)
    for update;

    if not found or v_message.lease_until <= v_now then
        raise exception 'REMOTE_OUTBOX_LEASE_LOST';
    end if;

    if v_message.expires_at <= v_now then
        update public.oasis_claim_remote_outbox
        set
            status = 'expired',
            secure_payload_ciphertext = '',
            lease_owner = null,
            lease_until = null,
            safe_error_code = 'MESSAGE_TTL_EXPIRED',
            updated_at = v_now
        where id = v_message.id
        returning * into v_message;
        return next v_message;
        return;
    end if;

    if v_status not in (
        'pending',
        'retry',
        'sent',
        'delivered',
        'failed',
        'expired',
        'cancelled'
    ) then
        raise exception 'REMOTE_OUTBOX_STATUS_INVALID';
    end if;
    if v_error_code <> ''
       and v_error_code !~ '^[A-Z0-9_-]{1,80}$' then
        raise exception 'REMOTE_OUTBOX_ERROR_CODE_INVALID';
    end if;

    if v_status in ('pending', 'retry')
       and v_message.attempt_count >= v_message.max_attempts then
        v_status := 'failed';
        v_error_code := 'MAX_ATTEMPTS_EXCEEDED';
    end if;

    v_terminal := v_status in (
        'sent',
        'delivered',
        'failed',
        'expired',
        'cancelled'
    );
    if not v_terminal and length(v_ciphertext) < 40 then
        raise exception 'REMOTE_OUTBOX_PAYLOAD_REQUIRED';
    end if;

    update public.oasis_claim_remote_outbox
    set
        status = v_status,
        secure_payload_ciphertext = case
            when v_terminal then ''
            else v_ciphertext
        end,
        payload_key_version = case
            when v_terminal
            then payload_key_version
            else trim(p_payload_key_version)
        end,
        provider_message_id = left(
            coalesce(nullif(trim(p_provider_message_id), ''), provider_message_id),
            200
        ),
        run_after = coalesce(p_next_run_at, v_now),
        lease_owner = null,
        lease_until = null,
        safe_error_code = v_error_code,
        sent_at = case
            when v_status in ('sent', 'delivered')
            then coalesce(sent_at, v_now)
            else sent_at
        end,
        delivered_at = case
            when v_status = 'delivered'
            then coalesce(delivered_at, v_now)
            else delivered_at
        end,
        updated_at = v_now
    where id = v_message.id
    returning * into v_message;

    return next v_message;
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
begin
    update public.oasis_claim_remote_invites
    set
        status = 'expired',
        secure_payload_ciphertext = '',
        updated_at = v_now
    where status in ('created', 'queued', 'sent', 'opened')
      and expires_at <= v_now;
    get diagnostics v_invites = row_count;

    update public.oasis_claim_remote_jobs
    set
        status = 'expired',
        secure_payload_ciphertext = '',
        lease_owner = null,
        lease_until = null,
        safe_error_code = 'JOB_TTL_EXPIRED',
        updated_at = v_now
    where status in ('queued', 'running', 'waiting', 'retry')
      and hard_expires_at <= v_now;
    get diagnostics v_jobs = row_count;

    update public.oasis_claim_remote_outbox
    set
        status = 'expired',
        secure_payload_ciphertext = '',
        lease_owner = null,
        lease_until = null,
        safe_error_code = 'MESSAGE_TTL_EXPIRED',
        updated_at = v_now
    where status in ('pending', 'running', 'retry')
      and expires_at <= v_now;
    get diagnostics v_messages = row_count;

    return jsonb_build_object(
        'invites', v_invites,
        'jobs', v_jobs,
        'messages', v_messages
    );
end;
$$;


alter table public.oasis_claim_remote_invites enable row level security;
alter table public.oasis_claim_remote_jobs enable row level security;
alter table public.oasis_claim_remote_outbox enable row level security;

revoke all on table public.oasis_claim_remote_invites
from public, anon, authenticated, service_role;
revoke all on table public.oasis_claim_remote_jobs
from public, anon, authenticated, service_role;
revoke all on table public.oasis_claim_remote_outbox
from public, anon, authenticated, service_role;

revoke execute
on function public.oasis_claim_remote_touch_updated_at()
from public, anon, authenticated, service_role;

revoke execute
on function public.oasis_claim_remote_create_invite(jsonb)
from public, anon, authenticated, service_role;
grant execute
on function public.oasis_claim_remote_create_invite(jsonb)
to service_role;

revoke execute
on function public.oasis_claim_remote_get_invite(text, text)
from public, anon, authenticated, service_role;
grant execute
on function public.oasis_claim_remote_get_invite(text, text)
to service_role;

revoke execute
on function public.oasis_claim_remote_resolve_invite(text)
from public, anon, authenticated, service_role;
grant execute
on function public.oasis_claim_remote_resolve_invite(text)
to service_role;

revoke execute
on function public.oasis_claim_remote_mark_invite_opened_global(text)
from public, anon, authenticated, service_role;
grant execute
on function public.oasis_claim_remote_mark_invite_opened_global(text)
to service_role;

revoke execute
on function public.oasis_claim_remote_mark_invite_opened(text, text)
from public, anon, authenticated, service_role;
grant execute
on function public.oasis_claim_remote_mark_invite_opened(text, text)
to service_role;

revoke execute
on function public.oasis_claim_remote_get_session_status(text, uuid)
from public, anon, authenticated, service_role;
grant execute
on function public.oasis_claim_remote_get_session_status(text, uuid)
to service_role;

revoke execute
on function public.oasis_claim_remote_consume_invite(text, text, jsonb)
from public, anon, authenticated, service_role;
grant execute
on function public.oasis_claim_remote_consume_invite(text, text, jsonb)
to service_role;

revoke execute
on function public.oasis_claim_remote_lease_jobs(text, integer, integer)
from public, anon, authenticated, service_role;
grant execute
on function public.oasis_claim_remote_lease_jobs(text, integer, integer)
to service_role;

revoke execute
on function public.oasis_claim_remote_heartbeat_job(uuid, text, integer)
from public, anon, authenticated, service_role;
grant execute
on function public.oasis_claim_remote_heartbeat_job(uuid, text, integer)
to service_role;

revoke execute
on function public.oasis_claim_remote_release_job(
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
)
from public, anon, authenticated, service_role;
grant execute
on function public.oasis_claim_remote_release_job(
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
)
to service_role;

revoke execute
on function public.oasis_claim_remote_enqueue_outbox(jsonb)
from public, anon, authenticated, service_role;
grant execute
on function public.oasis_claim_remote_enqueue_outbox(jsonb)
to service_role;

revoke execute
on function public.oasis_claim_remote_lease_outbox(text, integer, integer)
from public, anon, authenticated, service_role;
grant execute
on function public.oasis_claim_remote_lease_outbox(text, integer, integer)
to service_role;

revoke execute
on function public.oasis_claim_remote_release_outbox(
    uuid,
    text,
    text,
    text,
    text,
    text,
    timestamptz,
    text
)
from public, anon, authenticated, service_role;
grant execute
on function public.oasis_claim_remote_release_outbox(
    uuid,
    text,
    text,
    text,
    text,
    text,
    timestamptz,
    text
)
to service_role;

revoke execute
on function public.oasis_claim_remote_expire_due()
from public, anon, authenticated, service_role;
grant execute
on function public.oasis_claim_remote_expire_due()
to service_role;

commit;
