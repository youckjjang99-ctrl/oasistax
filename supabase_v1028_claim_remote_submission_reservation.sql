-- OASIS CRM v10.2.8 - 원격 경정청구 제출 예약 및 중복 인증 차단
-- 고객 입력 제출 시 초대권과 대기 작업을 먼저 원자적으로 생성한다.
-- 외부 인증 발송은 예약 성공 후에만 수행하며, 완료된 초대 링크는
-- v10.2.7의 읽기 전용 세션 복원 흐름을 그대로 사용한다.

begin;

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
    v_hard_expires_at :=
        nullif(trim(p_job ->> 'hard_expires_at'), '')::timestamptz;
    v_max_attempts := greatest(
        1,
        least(coalesce((p_job ->> 'max_attempts')::integer, 12), 100)
    );

    if v_job_id is null
       or v_case_id is null
       or v_stage !~ '^[a-z0-9._-]{1,80}$'
       or v_initial_status not in ('queued', 'waiting')
       or (
           v_stage = 'submission_reserved'
           and v_initial_status <> 'waiting'
       )
       or (
           v_initial_status = 'waiting'
           and v_stage <> 'submission_reserved'
       )
       or length(v_ciphertext) < 40
       or v_key_version !~ '^[A-Za-z0-9._-]{1,40}$'
       or v_hard_expires_at is null
       or v_hard_expires_at <= v_now
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
        created_at,
        updated_at
    )
    values (
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
       or v_job.lease_owner is not null
       or v_job.hard_expires_at <= v_now then
        raise exception 'REMOTE_JOB_NOT_RESERVED';
    end if;

    update public.oasis_claim_remote_jobs j
    set
        status = 'queued',
        stage = v_stage,
        secure_payload_ciphertext = p_secure_payload_ciphertext,
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
    set
        status = 'failed',
        stage = 'submission_failed',
        secure_payload_ciphertext = '',
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


revoke execute
on function public.oasis_claim_remote_consume_invite(text, text, jsonb)
from public, anon, authenticated, service_role;
grant execute
on function public.oasis_claim_remote_consume_invite(text, text, jsonb)
to service_role;

revoke execute
on function public.oasis_claim_remote_activate_reserved_job(
    text,
    uuid,
    uuid,
    text,
    text
)
from public, anon, authenticated, service_role;
grant execute
on function public.oasis_claim_remote_activate_reserved_job(
    text,
    uuid,
    uuid,
    text,
    text
)
to service_role;

revoke execute
on function public.oasis_claim_remote_fail_reserved_job(
    text,
    uuid,
    uuid,
    text,
    text
)
from public, anon, authenticated, service_role;
grant execute
on function public.oasis_claim_remote_fail_reserved_job(
    text,
    uuid,
    uuid,
    text,
    text
)
to service_role;

commit;
