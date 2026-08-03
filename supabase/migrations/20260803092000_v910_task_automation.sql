-- OASIS CRM v9.10.0 P0 - durable follow-up and tax-review task automation
--
-- This migration deliberately reuses oasis_company_kakao_followup_outbox.
-- It adds the smallest durable task master needed by the current product;
-- public phone numbers and customer-entered authentication data are never
-- copied into tasks, error summaries, or RPC results.

begin;

create table if not exists public.oasis_work_tasks (
    id uuid primary key default extensions.gen_random_uuid(),
    company_uid text,
    claim_case_id uuid,
    assigned_user_id text not null,
    created_by_user_id text not null,
    task_type text not null,
    title text not null,
    description text not null default '',
    priority text not null default 'normal',
    status text not null default 'pending',
    due_at timestamptz not null,
    completed_at timestamptz,
    cancelled_at timestamptz,
    cancellation_code text not null default '',
    source_type text not null,
    source_id uuid not null,
    idempotency_key text not null unique,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint oasis_work_tasks_company_uid_check
        check (
            company_uid is null
            or length(btrim(company_uid)) between 1 and 180
        ),
    constraint oasis_work_tasks_assignee_check
        check (
            assigned_user_id = lower(btrim(assigned_user_id))
            and length(assigned_user_id) between 1 and 200
        ),
    constraint oasis_work_tasks_creator_check
        check (
            created_by_user_id = lower(btrim(created_by_user_id))
            and length(created_by_user_id) between 1 and 200
        ),
    constraint oasis_work_tasks_type_check
        check (task_type in ('guidance_followup', 'claim_tax_review')),
    constraint oasis_work_tasks_title_check
        check (
            (task_type = 'guidance_followup' and title = '카카오톡 검토신청 후속 확인')
            or
            (task_type = 'claim_tax_review' and title = '경정청구 수집자료 세무사 검토')
        ),
    constraint oasis_work_tasks_priority_check
        check (priority in ('normal', 'high')),
    constraint oasis_work_tasks_status_check
        check (status in ('scheduled', 'pending', 'in_progress', 'completed', 'cancelled')),
    constraint oasis_work_tasks_source_check
        check (
            (task_type = 'guidance_followup' and source_type = 'guidance_message')
            or
            (task_type = 'claim_tax_review' and source_type = 'claim_case')
        ),
    constraint oasis_work_tasks_idempotency_check
        check (length(btrim(idempotency_key)) between 1 and 200),
    constraint oasis_work_tasks_cancellation_code_check
        check (
            cancellation_code = ''
            or cancellation_code ~ '^[A-Z0-9_-]{1,80}$'
        ),
    constraint oasis_work_tasks_terminal_times_check
        check (
            (status <> 'completed' or completed_at is not null)
            and (status <> 'cancelled' or cancelled_at is not null)
        ),
    constraint oasis_work_tasks_source_unique
        unique (task_type, source_id)
);

-- Earlier drafts may lack the source-identity unique constraint and may have
-- noncanonical or even swapped textual keys.  Drop the canonical check before
-- the two-phase rewrite, fail closed on duplicate durable identities, and use
-- a collision-checked temporary namespace so the idempotency unique key never
-- sees an in-place key swap.
alter table public.oasis_work_tasks
    drop constraint if exists oasis_work_tasks_canonical_idempotency_check;

do $oasis_work_tasks_rekey$
declare
    v_prefix text;
begin
    if exists (
        select 1
        from public.oasis_work_tasks t
        group by t.task_type, t.source_id
        having count(*) > 1
    ) then
        raise exception 'OASIS_WORK_TASK_SOURCE_DUPLICATES';
    end if;

    loop
        v_prefix := 'v910-rekey-' || extensions.gen_random_uuid()::text || ':';
        exit when not exists (
            select 1
            from public.oasis_work_tasks t
            where t.idempotency_key like v_prefix || '%'
        );
    end loop;

    update public.oasis_work_tasks t
    set idempotency_key = v_prefix || t.id::text,
        updated_at = clock_timestamp()
    where t.idempotency_key is distinct from case t.task_type
            when 'guidance_followup'
                then 'guidance-followup:' || t.source_id::text
            when 'claim_tax_review'
                then 'claim-tax-review:' || t.source_id::text
        end;

    update public.oasis_work_tasks t
    set idempotency_key = case t.task_type
            when 'guidance_followup'
                then 'guidance-followup:' || t.source_id::text
            when 'claim_tax_review'
                then 'claim-tax-review:' || t.source_id::text
        end,
        updated_at = clock_timestamp()
    where t.idempotency_key is distinct from case t.task_type
            when 'guidance_followup'
                then 'guidance-followup:' || t.source_id::text
            when 'claim_tax_review'
                then 'claim-tax-review:' || t.source_id::text
        end;
end;
$oasis_work_tasks_rekey$;

alter table public.oasis_work_tasks
    drop constraint if exists oasis_work_tasks_source_unique;
alter table public.oasis_work_tasks
    add constraint oasis_work_tasks_source_unique
    unique (task_type, source_id);

alter table public.oasis_work_tasks
    add constraint oasis_work_tasks_canonical_idempotency_check
    check (
        idempotency_key = case task_type
            when 'guidance_followup'
                then 'guidance-followup:' || source_id::text
            when 'claim_tax_review'
                then 'claim-tax-review:' || source_id::text
        end
    );

do $$
begin
    if to_regclass('public.oasis_claim_cases') is not null
       and not exists (
           select 1
           from pg_constraint
           where conname = 'oasis_work_tasks_claim_case_fkey'
             and conrelid = 'public.oasis_work_tasks'::regclass
       ) then
        alter table public.oasis_work_tasks
            add constraint oasis_work_tasks_claim_case_fkey
            foreign key (claim_case_id)
            references public.oasis_claim_cases(id)
            on delete restrict;
    end if;
end;
$$;

alter table public.oasis_company_kakao_followup_outbox
    drop constraint if exists oasis_guidance_followup_canonical_idempotency_check;

do $oasis_guidance_followup_rekey$
declare
    v_prefix text;
begin
    loop
        v_prefix := 'v910-followup-rekey-'
            || extensions.gen_random_uuid()::text || ':';
        exit when not exists (
            select 1
            from public.oasis_company_kakao_followup_outbox o
            where o.idempotency_key like v_prefix || '%'
        );
    end loop;

    update public.oasis_company_kakao_followup_outbox o
    set idempotency_key = v_prefix || o.id::text,
        updated_at = clock_timestamp()
    where o.idempotency_key is distinct from
        'guidance-followup:' || o.guidance_message_id::text;

    update public.oasis_company_kakao_followup_outbox o
    set idempotency_key =
            'guidance-followup:' || o.guidance_message_id::text,
        updated_at = clock_timestamp()
    where o.idempotency_key is distinct from
        'guidance-followup:' || o.guidance_message_id::text;
end;
$oasis_guidance_followup_rekey$;

alter table public.oasis_company_kakao_followup_outbox
    add constraint oasis_guidance_followup_canonical_idempotency_check
    check (
        idempotency_key = 'guidance-followup:' || guidance_message_id::text
    );

-- The v9.10 guidance migration intentionally left task_id unbound because the
-- durable task master did not exist yet.  Clear only impossible orphan
-- pointers, then attach the canonical FK without touching valid history.
do $$
begin
    if to_regclass('public.oasis_company_kakao_followup_outbox') is not null
       and not exists (
           select 1
           from pg_constraint c
           where c.contype = 'f'
             and c.conrelid =
                 'public.oasis_company_kakao_followup_outbox'::regclass
             and c.confrelid = 'public.oasis_work_tasks'::regclass
             and (
                 c.conname = 'oasis_guidance_followup_task_fkey'
                 or pg_get_constraintdef(c.oid) like
                     'FOREIGN KEY (task_id) REFERENCES oasis_work_tasks(id)%'
                 or pg_get_constraintdef(c.oid) like
                     'FOREIGN KEY (task_id) REFERENCES public.oasis_work_tasks(id)%'
             )
       ) then
        update public.oasis_company_kakao_followup_outbox o
        set task_id = null
        where o.task_id is not null
          and not exists (
              select 1
              from public.oasis_work_tasks t
              where t.id = o.task_id
          );

        alter table public.oasis_company_kakao_followup_outbox
            add constraint oasis_guidance_followup_task_fkey
            foreign key (task_id)
            references public.oasis_work_tasks(id)
            on delete set null;
    end if;
end;
$$;

create index if not exists oasis_work_tasks_assignee_due_idx
    on public.oasis_work_tasks (assigned_user_id, status, due_at, created_at)
    where status in ('scheduled', 'pending', 'in_progress');
create index if not exists oasis_work_tasks_company_idx
    on public.oasis_work_tasks (company_uid, status, updated_at desc)
    where company_uid is not null;
create index if not exists oasis_work_tasks_claim_idx
    on public.oasis_work_tasks (claim_case_id, status, updated_at desc)
    where claim_case_id is not null;
create index if not exists oasis_guidance_followup_task_fk_idx
    on public.oasis_company_kakao_followup_outbox (task_id)
    where task_id is not null;

create or replace function public.oasis_work_task_touch_updated_at()
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

drop trigger if exists oasis_work_tasks_updated_at
    on public.oasis_work_tasks;
create trigger oasis_work_tasks_updated_at
before update on public.oasis_work_tasks
for each row execute function public.oasis_work_task_touch_updated_at();

-- Cancel only open work. Completed work is immutable from cancellation events.
create or replace function public.oasis_cancel_guidance_work_for_message()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
    if new.status in ('failed', 'blocked', 'cancelled', 'simulated') then
        if tg_op = 'UPDATE' and old.status is not distinct from new.status then
            return new;
        end if;
        update public.oasis_company_kakao_followup_outbox o
        set
            status = 'cancelled',
            completed_at = coalesce(o.completed_at, now()),
            lease_owner = null,
            lease_until = null,
            safe_error_code = '',
            safe_error_summary = ''
        where o.guidance_message_id = new.id
          and o.status in ('pending', 'running', 'retry');

        update public.oasis_work_tasks t
        set
            status = 'cancelled',
            cancelled_at = coalesce(t.cancelled_at, now()),
            cancellation_code = 'GUIDANCE_TERMINATED'
        where t.task_type = 'guidance_followup'
          and t.source_id = new.id
          and t.status in ('scheduled', 'pending', 'in_progress');
    end if;
    return new;
end;
$$;

drop trigger if exists oasis_guidance_message_cancel_work
    on public.oasis_company_kakao_guidance_messages;
create trigger oasis_guidance_message_cancel_work
after insert or update of status
on public.oasis_company_kakao_guidance_messages
for each row execute function public.oasis_cancel_guidance_work_for_message();

create or replace function public.oasis_cancel_guidance_work_for_control()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
    if new.status in ('opted_out', 'admin_blocked') then
        if tg_op = 'UPDATE' and old.status is not distinct from new.status then
            return new;
        end if;
        update public.oasis_company_kakao_followup_outbox o
        set
            status = 'cancelled',
            completed_at = coalesce(o.completed_at, now()),
            lease_owner = null,
            lease_until = null,
            safe_error_code = '',
            safe_error_summary = ''
        where o.company_uid = new.company_uid
          and o.status in ('pending', 'running', 'retry');

        update public.oasis_work_tasks t
        set
            status = 'cancelled',
            cancelled_at = coalesce(t.cancelled_at, now()),
            cancellation_code = case
                when new.status = 'opted_out' then 'CUSTOMER_OPT_OUT'
                else 'ADMIN_BLOCKED'
            end
        where t.task_type = 'guidance_followup'
          and t.company_uid = new.company_uid
          and t.status in ('scheduled', 'pending', 'in_progress');
    end if;
    return new;
end;
$$;

drop trigger if exists oasis_guidance_control_cancel_work
    on public.oasis_company_kakao_contact_controls;
create trigger oasis_guidance_control_cancel_work
after insert or update of status
on public.oasis_company_kakao_contact_controls
for each row execute function public.oasis_cancel_guidance_work_for_control();

-- A terminal claim collection creates one safe, PII-free tax review task.
-- Cancelled/expired jobs cancel only still-open review work.
create or replace function public.oasis_sync_claim_review_task()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_requested_by text;
    v_owner_user_id text;
    v_assignee text;
    v_is_partial boolean;
begin
    if tg_op = 'UPDATE' and old.status is not distinct from new.status then
        return new;
    end if;

    if new.status in ('complete', 'partial', 'failed', 'cancelled', 'expired') then
        -- Once the customer flow is terminal, the 3-business-day reminder is
        -- obsolete.  Keep the original sent-message history, but cancel any
        -- open reminder outbox/task linked through the invite.
        update public.oasis_company_kakao_followup_outbox o
        set
            status = 'cancelled',
            completed_at = coalesce(o.completed_at, now()),
            lease_owner = null,
            lease_until = null,
            safe_error_code = '',
            safe_error_summary = ''
        where o.guidance_message_id in (
                select m.id
                from public.oasis_company_kakao_guidance_messages m
                where m.secure_review_link_id = new.invite_id
            )
          and o.status in ('pending', 'running', 'retry');

        update public.oasis_work_tasks t
        set
            status = 'cancelled',
            cancelled_at = coalesce(t.cancelled_at, now()),
            cancellation_code = case
                when new.status in ('complete', 'partial')
                    then 'CLAIM_COLLECTION_TERMINAL'
                when new.status = 'failed'
                    then 'CLAIM_COLLECTION_FAILED'
                else 'CLAIM_INVITE_TERMINAL'
            end
        where t.task_type = 'guidance_followup'
          and t.source_id in (
                select m.id
                from public.oasis_company_kakao_guidance_messages m
                where m.secure_review_link_id = new.invite_id
            )
          and t.status in ('scheduled', 'pending', 'in_progress');
    end if;

    if new.status in ('complete', 'partial') then
        select c.requested_by, c.owner_user_id
        into v_requested_by, v_owner_user_id
        from public.oasis_claim_cases c
        where c.id = new.case_id;

        if not found then
            return new;
        end if;

        v_assignee := lower(btrim(coalesce(
            nullif(v_requested_by, ''),
            v_owner_user_id
        )));
        if v_assignee = '' then
            return new;
        end if;

        v_is_partial := new.status = 'partial';
        insert into public.oasis_work_tasks (
            company_uid,
            claim_case_id,
            assigned_user_id,
            created_by_user_id,
            task_type,
            title,
            description,
            priority,
            status,
            due_at,
            source_type,
            source_id,
            idempotency_key
        ) values (
            null,
            new.case_id,
            v_assignee,
            v_assignee,
            'claim_tax_review',
            '경정청구 수집자료 세무사 검토',
            '수집자료 검토 및 후속조치가 필요합니다.',
            case when v_is_partial then 'high' else 'normal' end,
            'pending',
            now(),
            'claim_case',
            new.case_id,
            'claim-tax-review:' || new.case_id::text
        )
        on conflict (task_type, source_id) do update
        set
            idempotency_key = excluded.idempotency_key,
            priority = case
                when public.oasis_work_tasks.priority = 'high'
                    then 'high'
                else excluded.priority
            end,
            updated_at = clock_timestamp()
        where public.oasis_work_tasks.status not in ('completed', 'cancelled');
    elsif new.status in ('cancelled', 'expired', 'failed') then
        update public.oasis_work_tasks t
        set
            status = 'cancelled',
            cancelled_at = coalesce(t.cancelled_at, now()),
            cancellation_code = case
                when new.status = 'expired' then 'COLLECTION_EXPIRED'
                when new.status = 'failed' then 'COLLECTION_FAILED'
                else 'COLLECTION_CANCELLED'
            end
        where t.task_type = 'claim_tax_review'
          and t.source_id = new.case_id
          and t.status in ('scheduled', 'pending', 'in_progress');
    end if;
    return new;
end;
$$;

do $$
begin
    if to_regclass('public.oasis_claim_remote_jobs') is not null then
        drop trigger if exists oasis_claim_job_sync_review_task
            on public.oasis_claim_remote_jobs;
        create trigger oasis_claim_job_sync_review_task
        after insert or update of status, stage
        on public.oasis_claim_remote_jobs
        for each row execute function public.oasis_sync_claim_review_task();
    end if;
end;
$$;

create or replace function public.oasis_lease_company_kakao_followups(
    p_worker_id text,
    p_limit integer default 25,
    p_lease_seconds integer default 90
)
returns table (
    id uuid,
    guidance_message_id uuid,
    idempotency_key text
)
language plpgsql
volatile
security definer
set search_path = public, pg_temp
as $$
declare
    v_worker text := btrim(coalesce(p_worker_id, ''));
    v_limit integer := greatest(1, least(coalesce(p_limit, 25), 100));
    v_lease_seconds integer := greatest(
        30,
        least(coalesce(p_lease_seconds, 90), 900)
    );
begin
    if v_worker !~ '^[A-Za-z0-9._:-]{1,120}$' then
        raise exception using message = 'INVALID_WORKER_ID';
    end if;

    -- Reconcile terminal guidance and customer controls before leasing.
    update public.oasis_company_kakao_followup_outbox o
    set
        status = 'cancelled',
        completed_at = coalesce(o.completed_at, now()),
        lease_owner = null,
        lease_until = null,
        safe_error_code = '',
        safe_error_summary = ''
    where o.status in ('pending', 'running', 'retry')
      and (
          exists (
              select 1
              from public.oasis_company_kakao_guidance_messages m
              where m.id = o.guidance_message_id
                and (
                    m.delivery_mode <> 'live'
                    or m.status not in ('sent', 'delivered')
                )
          )
          or exists (
              select 1
              from public.oasis_company_kakao_contact_controls c
              where c.company_uid = o.company_uid
                and c.status in ('opted_out', 'admin_blocked')
          )
      );

    -- A cancelled canonical same-source task is terminal.  Re-creating it
    -- under a different idempotency key would cause an endless retry/deadletter
    -- loop on the (task_type, source_id) uniqueness contract.
    update public.oasis_company_kakao_followup_outbox o
    set
        status = 'cancelled',
        task_id = t.id,
        completed_at = coalesce(o.completed_at, now()),
        lease_owner = null,
        lease_until = null,
        safe_error_code = '',
        safe_error_summary = ''
    from public.oasis_work_tasks t
    where t.task_type = 'guidance_followup'
      and t.source_id = o.guidance_message_id
      and t.status = 'cancelled'
      and o.status in ('pending', 'running', 'retry');

    -- An already-created canonical same-source task wins after a crash between
    -- task insertion and outbox completion, even if an older deployment used
    -- a different idempotency-key format.
    update public.oasis_company_kakao_followup_outbox o
    set
        status = 'created',
        task_id = t.id,
        completed_at = coalesce(o.completed_at, now()),
        lease_owner = null,
        lease_until = null,
        safe_error_code = '',
        safe_error_summary = ''
    from public.oasis_work_tasks t
    where t.task_type = 'guidance_followup'
      and t.source_id = o.guidance_message_id
      and t.status <> 'cancelled'
      and o.status in ('pending', 'running', 'retry');

    update public.oasis_company_kakao_followup_outbox o
    set
        status = 'dead_letter',
        completed_at = coalesce(o.completed_at, now()),
        lease_owner = null,
        lease_until = null,
        safe_error_code = 'TASK_RETRY_EXHAUSTED',
        safe_error_summary = '자동화 최대 재시도 횟수를 초과했습니다.'
    where o.status in ('pending', 'retry', 'running')
      and o.attempt_count >= o.max_attempts
      and (o.lease_until is null or o.lease_until < now());

    return query
    with candidates as (
        select o.id
        from public.oasis_company_kakao_followup_outbox o
        where (
                o.status in ('pending', 'retry')
                or (o.status = 'running' and o.lease_until < now())
            )
          and o.due_at <= now()
          and o.next_retry_at <= now()
          and o.attempt_count < o.max_attempts
        order by o.due_at, o.created_at, o.id
        for update skip locked
        limit v_limit
    ), leased as (
        update public.oasis_company_kakao_followup_outbox o
        set
            status = 'running',
            attempt_count = o.attempt_count + 1,
            lease_owner = v_worker,
            lease_until = now() + make_interval(secs => v_lease_seconds),
            safe_error_code = '',
            safe_error_summary = ''
        from candidates c
        where o.id = c.id
        returning o.id, o.guidance_message_id, o.idempotency_key
    )
    select l.id, l.guidance_message_id, l.idempotency_key
    from leased l;
end;
$$;

create or replace function public.oasis_materialize_company_kakao_followup(
    p_worker_id text,
    p_outbox_id uuid
)
returns table (
    success boolean,
    code text,
    task_id uuid,
    task_status text
)
language plpgsql
volatile
security definer
set search_path = public, pg_temp
as $$
declare
    v_worker text := btrim(coalesce(p_worker_id, ''));
    v_outbox public.oasis_company_kakao_followup_outbox%rowtype;
    v_message public.oasis_company_kakao_guidance_messages%rowtype;
    v_task public.oasis_work_tasks%rowtype;
    v_blocked boolean;
begin
    if v_worker !~ '^[A-Za-z0-9._:-]{1,120}$' then
        raise exception using message = 'INVALID_WORKER_ID';
    end if;

    select o.* into v_outbox
    from public.oasis_company_kakao_followup_outbox o
    where o.id = p_outbox_id
    for update;

    if v_outbox.id is null then
        return query select false, 'OUTBOX_NOT_FOUND', null::uuid, null::text;
        return;
    end if;

    select t.* into v_task
    from public.oasis_work_tasks t
    where t.task_type = 'guidance_followup'
      and t.source_id = v_outbox.guidance_message_id
    for update;

    if v_task.id is not null and v_task.status <> 'cancelled' then
        update public.oasis_company_kakao_followup_outbox o
        set
            status = 'created',
            task_id = v_task.id,
            completed_at = coalesce(o.completed_at, now()),
            lease_owner = null,
            lease_until = null,
            safe_error_code = '',
            safe_error_summary = ''
        where o.id = v_outbox.id;
        return query select true, 'ALREADY_CREATED', v_task.id, v_task.status;
        return;
    end if;

    if v_task.id is not null and v_task.status = 'cancelled' then
        update public.oasis_company_kakao_followup_outbox o
        set
            status = 'cancelled',
            task_id = v_task.id,
            completed_at = coalesce(o.completed_at, now()),
            lease_owner = null,
            lease_until = null,
            safe_error_code = '',
            safe_error_summary = ''
        where o.id = v_outbox.id;
        return query select false, 'NO_LONGER_ELIGIBLE', v_task.id, v_task.status;
        return;
    end if;

    if v_outbox.status <> 'running'
       or v_outbox.lease_owner is distinct from v_worker
       or v_outbox.lease_until is null
       or v_outbox.lease_until < now() then
        return query select false, 'LEASE_NOT_OWNED', null::uuid, null::text;
        return;
    end if;

    select m.* into v_message
    from public.oasis_company_kakao_guidance_messages m
    where m.id = v_outbox.guidance_message_id;

    select exists (
        select 1
        from public.oasis_company_kakao_contact_controls c
        where c.company_uid = v_outbox.company_uid
          and c.status in ('opted_out', 'admin_blocked')
    ) into v_blocked;

    if v_message.id is null
       or v_message.delivery_mode <> 'live'
       or v_message.status not in ('sent', 'delivered')
       or v_blocked then
        update public.oasis_company_kakao_followup_outbox o
        set
            status = 'cancelled',
            completed_at = coalesce(o.completed_at, now()),
            lease_owner = null,
            lease_until = null,
            safe_error_code = '',
            safe_error_summary = ''
        where o.id = v_outbox.id;
        return query select false, 'NO_LONGER_ELIGIBLE', null::uuid, null::text;
        return;
    end if;

    insert into public.oasis_work_tasks (
        company_uid,
        claim_case_id,
        assigned_user_id,
        created_by_user_id,
        task_type,
        title,
        description,
        priority,
        status,
        due_at,
        source_type,
        source_id,
        idempotency_key
    ) values (
        v_outbox.company_uid,
        null,
        lower(btrim(v_outbox.assigned_user_id)),
        lower(btrim(v_outbox.assigned_user_id)),
        'guidance_followup',
        '카카오톡 검토신청 후속 확인',
        '검토신청 진행 여부를 확인해 주세요.',
        'normal',
        'pending',
        v_outbox.due_at,
        'guidance_message',
        v_outbox.guidance_message_id,
        'guidance-followup:' || v_outbox.guidance_message_id::text
    )
    on conflict (task_type, source_id) do update
    set updated_at = clock_timestamp()
    returning * into v_task;

    if v_task.status = 'cancelled' then
        update public.oasis_company_kakao_followup_outbox o
        set
            status = 'cancelled',
            task_id = v_task.id,
            completed_at = coalesce(o.completed_at, now()),
            lease_owner = null,
            lease_until = null,
            safe_error_code = '',
            safe_error_summary = ''
        where o.id = v_outbox.id;
        return query select false, 'NO_LONGER_ELIGIBLE', v_task.id, v_task.status;
        return;
    end if;

    update public.oasis_company_kakao_followup_outbox o
    set
        status = 'created',
        task_id = v_task.id,
        completed_at = coalesce(o.completed_at, now()),
        lease_owner = null,
        lease_until = null,
        safe_error_code = '',
        safe_error_summary = ''
    where o.id = v_outbox.id;

    return query select true, 'CREATED', v_task.id, v_task.status;
end;
$$;

create or replace function public.oasis_fail_company_kakao_followup(
    p_worker_id text,
    p_outbox_id uuid,
    p_error_code text default 'TASK_RPC_FAILED',
    p_retry_after_seconds integer default 60
)
returns table (
    success boolean,
    code text,
    status text
)
language plpgsql
volatile
security definer
set search_path = public, pg_temp
as $$
declare
    v_worker text := btrim(coalesce(p_worker_id, ''));
    v_error_code text := left(
        regexp_replace(
            upper(coalesce(nullif(btrim(p_error_code), ''), 'TASK_RPC_FAILED')),
            '[^A-Z0-9_-]',
            '_',
            'g'
        ),
        80
    );
    v_retry integer := greatest(
        30,
        least(coalesce(p_retry_after_seconds, 60), 3600)
    );
    v_status text;
begin
    if v_worker !~ '^[A-Za-z0-9._:-]{1,120}$' then
        raise exception using message = 'INVALID_WORKER_ID';
    end if;

    update public.oasis_company_kakao_followup_outbox o
    set
        status = case
            when o.attempt_count >= o.max_attempts then 'dead_letter'
            else 'retry'
        end,
        next_retry_at = case
            when o.attempt_count >= o.max_attempts then o.next_retry_at
            else now() + make_interval(secs => v_retry)
        end,
        lease_owner = null,
        lease_until = null,
        safe_error_code = v_error_code,
        safe_error_summary = case
            when o.attempt_count >= o.max_attempts
                then '자동화 최대 재시도 횟수를 초과했습니다.'
            else '후속업무 생성 재시도 대기 중입니다.'
        end,
        completed_at = case
            when o.attempt_count >= o.max_attempts
                then coalesce(o.completed_at, now())
            else o.completed_at
        end
    where o.id = p_outbox_id
      and o.status = 'running'
      and o.lease_owner = v_worker
    returning o.status into v_status;

    if v_status is null then
        return query select false, 'LEASE_NOT_OWNED', null::text;
        return;
    end if;
    return query select true, 'RECORDED', v_status;
end;
$$;

alter table public.oasis_work_tasks enable row level security;

revoke all on table public.oasis_work_tasks
    from PUBLIC, anon, authenticated, service_role;
grant select, insert, update, delete on table public.oasis_work_tasks
    to service_role;

drop policy if exists oasis_work_tasks_service_role_all
    on public.oasis_work_tasks;
create policy oasis_work_tasks_service_role_all
on public.oasis_work_tasks
for all
to service_role
using (true)
with check (true);

do $$
declare
    fn record;
begin
    for fn in
        select p.proname, pg_get_function_identity_arguments(p.oid) identity_arguments
        from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'public'
          and p.proname in (
              'oasis_work_task_touch_updated_at',
              'oasis_cancel_guidance_work_for_message',
              'oasis_cancel_guidance_work_for_control',
              'oasis_sync_claim_review_task',
              'oasis_lease_company_kakao_followups',
              'oasis_materialize_company_kakao_followup',
              'oasis_fail_company_kakao_followup'
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

comment on table public.oasis_work_tasks is
    'PII-free durable central work queue. Guidance and claim automation use fixed safe titles only.';
comment on column public.oasis_work_tasks.title is
    'Fixed safe title. Customer name, phone, business number, or authentication data are prohibited.';
comment on column public.oasis_work_tasks.idempotency_key is
    'Exactly-once logical task key across worker retries and process restarts.';

commit;
