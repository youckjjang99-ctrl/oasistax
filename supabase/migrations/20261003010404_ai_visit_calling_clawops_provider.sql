-- v9.14.1: isolate claimed jobs by carrier and accept namespaced ClawOps IDs.
-- Additive only. Legacy jobs remain Twilio; queued jobs choose a carrier at claim.
-- No dialing, permission, approval or production configuration is enabled here.
begin;
set local lock_timeout = '5s';
set local statement_timeout = '120s';

alter table public.oasis_voice_jobs
 add column if not exists provider text not null default 'twilio'
 check (provider in ('twilio','clawops'));

create or replace function public.oasis_voice_worker(p_action text,p_payload jsonb default '{}')
returns jsonb language plpgsql security invoker set search_path='' as $$
declare job public.oasis_voice_jobs%rowtype; target jsonb; perms public.oasis_voice_permissions%rowtype;
 code text; provider_id text; provider_name text; new_status text; outcome_value text; when_at timestamptz;
 contact_result text; r record; seq integer; daily integer; allowed_phones jsonb;
begin
 perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('oasis-voice-queue',0));
 if p_action='claim' then
  provider_name:=coalesce(p_payload->>'provider','twilio');
  if provider_name not in ('twilio','clawops') then return jsonb_build_object('ok',false,'code','INVALID_INPUT'); end if;
  -- Internal pre-verification tests must never claim unrelated approved jobs.
  allowed_phones:=p_payload->'test_phone_allowlist';
  if p_payload ? 'test_phone_allowlist' then
   if provider_name<>'clawops' or jsonb_typeof(allowed_phones) is distinct from 'array' then
    return jsonb_build_object('ok',false,'code','INVALID_INPUT'); end if;
   if jsonb_array_length(allowed_phones) not between 1 and 5
    or exists(select 1 from jsonb_array_elements_text(allowed_phones) p(phone) where phone is null or phone !~ '^\+82[0-9]{8,10}$') then
    return jsonb_build_object('ok',false,'code','INVALID_INPUT'); end if;
  end if;
  -- Unknown outcomes block more dialing until investigated, never silently retry.
  update public.oasis_voice_jobs set status='unknown',safe_error_code='STALE_DISPATCH',updated_at=now()
   where status in ('dispatching','accepted','in_progress') and dispatch_at<now()-interval '10 minutes';
  if extract(isodow from now() at time zone 'Asia/Seoul')>5 or extract(hour from now() at time zone 'Asia/Seoul') not between 9 and 17 then
   return jsonb_build_object('ok',true,'code','OUTSIDE_HOURS'); end if;
  daily:=greatest(1,least(100,coalesce((p_payload->>'daily_limit')::integer,20)));
  if (select count(*) from public.oasis_voice_jobs where dispatch_at>=(date_trunc('day',now() at time zone 'Asia/Seoul') at time zone 'Asia/Seoul'))>=daily then
   return jsonb_build_object('ok',true,'code','DAILY_LIMIT'); end if;
  if exists(select 1 from public.oasis_voice_jobs where status in ('dispatching','accepted','in_progress','unknown')) then
   return jsonb_build_object('ok',true,'code','BUSY'); end if;
  select * into job from public.oasis_voice_jobs where status='approved'
   and (allowed_phones is null or phone_e164 in (select value from jsonb_array_elements_text(allowed_phones)))
   order by created_at,id limit 1 for update skip locked;
  if job.id is null then return jsonb_build_object('ok',true,'code','EMPTY'); end if;
  target:=public.oasis_voice_target(job.company_uid);
  select * into perms from public.oasis_voice_permissions where id=job.permission_id;
  if target is null or target->>'owner_user_id'<>job.owner_user_id or target->>'phone_e164'<>job.phone_e164
   or (target->>'contact_id')::uuid<>job.contact_id or job.approved_at<now()-interval '24 hours'
   or not public.oasis_sales_actor_is_admin(job.approved_by)
   or perms.revoked_at is not null or perms.granted_at>now() or perms.expires_at<=now()
   or public.oasis_voice_blocked(job.company_uid,job.phone_e164) then
   update public.oasis_voice_jobs set status='cancelled',safe_error_code='TARGET_CHANGED',finished_at=now(),updated_at=now() where id=job.id;
   insert into public.oasis_voice_events(job_id,company_uid,actor,action) values(job.id,job.company_uid,'worker','preflight_cancelled');
   return jsonb_build_object('ok',true,'code','TARGET_CHANGED');
  end if;
  update public.oasis_voice_jobs set status='dispatching',provider=provider_name,dispatch_at=now(),dispatch_nonce=gen_random_uuid(),updated_at=now() where id=job.id returning * into job;
  insert into public.oasis_voice_events(job_id,company_uid,actor,action) values(job.id,job.company_uid,'worker','dispatch_claimed');
  return jsonb_build_object('ok',true,'job',target||to_jsonb(job));
 end if;
 select * into job from public.oasis_voice_jobs where id=(p_payload->>'job_id')::uuid for update;
 if job.id is null then return jsonb_build_object('ok',false,'code','INVALID_INPUT'); end if;
 provider_id:=p_payload->>'provider_call_id';
 -- Namespace identifies the upstream provider; an explicit provider must agree.
 provider_name:=case when provider_id ~ '^clawops:CA[A-Za-z0-9_-]{8,100}$' then 'clawops'
  when provider_id ~ '^CA[0-9a-fA-F]{32}$' then 'twilio'
  when provider_id is null then coalesce(p_payload->>'provider','twilio') else null end;
 if provider_name is null or provider_name is distinct from job.provider
  or (p_payload ? 'provider' and p_payload->>'provider' is distinct from provider_name)
  or (job.provider_call_id is not null and provider_id is distinct from job.provider_call_id) then
  return jsonb_build_object('ok',false,'code','PROVIDER_MISMATCH'); end if;
 if p_action in ('connect','status','result','mark_dispatched') and provider_id is null then
  return jsonb_build_object('ok',false,'code','PROVIDER_MISMATCH'); end if;
 -- get_job is a read-only preflight for signed VoiceML: inspect the expected
 -- destination before binding a call ID or issuing its one-use media nonce.
 -- connect binds only after target, permission and nonce checks below.
 if job.provider_call_id is null and p_action in ('status','mark_dispatched') then
  if job.status<>'dispatching' then return jsonb_build_object('ok',false,'code','INVALID_STATE'); end if;
  update public.oasis_voice_jobs set provider_call_id=provider_id,updated_at=now() where id=job.id returning * into job;
 end if;
 if p_action in ('get_job','connect') then
  target:=public.oasis_voice_target(job.company_uid);
  select * into perms from public.oasis_voice_permissions where id=job.permission_id;
  if target is null or target->>'owner_user_id'<>job.owner_user_id or target->>'phone_e164'<>job.phone_e164
   or perms.revoked_at is not null or perms.expires_at<=now() or public.oasis_voice_blocked(job.company_uid,job.phone_e164)
   or job.status not in ('dispatching','accepted','in_progress') then return jsonb_build_object('ok',false,'code','TARGET_CHANGED'); end if;
  if p_action='connect' then
   if job.connected_at is not null or (p_payload->>'dispatch_nonce')::uuid is distinct from job.dispatch_nonce then
    return jsonb_build_object('ok',false,'code','PROVIDER_MISMATCH'); end if;
   update public.oasis_voice_jobs set provider_call_id=provider_id,connected_at=now(),status='in_progress',updated_at=now() where id=job.id returning * into job;
  end if;
  return jsonb_build_object('ok',true,'job',target||to_jsonb(job));
 elsif p_action='mark_dispatched' then
  update public.oasis_voice_jobs set status='accepted',updated_at=now() where id=job.id and status='dispatching';
 elsif p_action in ('mark_unknown','mark_failed','bridge_error') then
  if p_action='bridge_error' and p_payload->>'error_code'='DO_NOT_CALL_SAVE_FAILED' then
   insert into public.oasis_voice_suppressions(company_uid,phone_e164,reason,created_by)
    values(job.company_uid,job.phone_e164,'do_not_call','voice-safety');
   update public.oasis_voice_jobs set status='unknown',safe_error_code='DO_NOT_CALL_SAVE_FAILED',updated_at=now() where id=job.id;
   update public.oasis_voice_jobs set status='cancelled',finished_at=now(),updated_at=now()
    where id<>job.id and (company_uid=job.company_uid or phone_e164=job.phone_e164) and status in ('queued','approved');
  else
  update public.oasis_voice_jobs set status=case when p_action='mark_failed' then 'failed' else 'unknown' end,
   safe_error_code=case when p_action='bridge_error' then 'BRIDGE_FAILED' when p_action='mark_failed' then 'PROVIDER_REJECTED' else 'DISPATCH_UNKNOWN' end,
   updated_at=now() where id=job.id and status not in ('completed','failed','cancelled');
  end if;
 elsif p_action='status' then
  seq:=coalesce((p_payload->>'sequence_number')::integer,-1);
  if seq<job.last_provider_sequence then return jsonb_build_object('ok',true); end if;
  new_status:=case p_payload->>'status' when 'completed' then 'completed' when 'busy' then 'failed' when 'failed' then 'failed' when 'no-answer' then 'failed' when 'canceled' then 'cancelled' else null end;
  if new_status is not null and job.status not in ('completed','failed','cancelled') and coalesce(job.safe_error_code,'')<>'DO_NOT_CALL_SAVE_FAILED' then
   update public.oasis_voice_jobs set status=new_status,finished_at=now(),last_provider_sequence=seq,
    duration_seconds=greatest(0,least(7200,coalesce((p_payload->>'duration_seconds')::integer,0))),updated_at=now() where id=job.id;
  end if;
 elsif p_action='result' then
  if job.connected_at is null or job.provider_call_id is null or job.status not in ('in_progress','completed','unknown') then raise exception 'INVALID_STATE'; end if;
  outcome_value:=p_payload->>'outcome';
  if outcome_value is null or outcome_value not in ('visit_requested','callback_requested','declined','wrong_number','not_representative','do_not_call') then raise exception 'INVALID_RESULT'; end if;
  if job.outcome='do_not_call' then return jsonb_build_object('ok',true); end if;
  -- A late opt-out always wins; other repeated tool calls never duplicate contact logs.
  if job.outcome is not null and outcome_value<>'do_not_call' then return jsonb_build_object('ok',true,'code','DUPLICATE'); end if;
  if outcome_value in ('visit_requested','callback_requested') then
   if (p_payload->>'customer_confirmed') is distinct from 'true' or coalesce(p_payload->>'visit_at','') !~ '(Z|[+-][0-9]{2}:[0-9]{2})$' then raise exception 'INVALID_RESULT'; end if;
   when_at:=(p_payload->>'visit_at')::timestamptz;
   if when_at<=now() or when_at>now()+interval '90 days' or extract(isodow from when_at at time zone 'Asia/Seoul')>5
    or extract(hour from when_at at time zone 'Asia/Seoul') not between 9 and 17 or (outcome_value='visit_requested' and length(btrim(coalesce(p_payload->>'address','')))<5) then raise exception 'INVALID_RESULT'; end if;
  end if;
  if outcome_value in ('do_not_call','wrong_number','declined') then
   insert into public.oasis_voice_suppressions(company_uid,phone_e164,reason,created_by) values(job.company_uid,job.phone_e164,outcome_value,'voice-customer');
   update public.oasis_voice_jobs set status='cancelled',finished_at=now(),updated_at=now() where id<>job.id and (company_uid=job.company_uid or phone_e164=job.phone_e164) and status in ('queued','approved');
  end if;
  update public.oasis_voice_jobs set outcome=outcome_value,visit_at=when_at,address=left(coalesce(p_payload->>'address',''),300),
   summary=left(coalesce(p_payload->>'summary',''),500),customer_confirmed=coalesce((p_payload->>'customer_confirmed')::boolean,false),
   visit_confirmed_at=case when outcome_value='do_not_call' then null else visit_confirmed_at end,
   visit_confirmed_by=case when outcome_value='do_not_call' then null else visit_confirmed_by end,updated_at=now() where id=job.id;
  if job.contact_recorded_at is null and exists(select 1 from public.oasis_company_sales_assignments where id=job.assignment_id and assigned_user_id=job.owner_user_id) then
   begin
   contact_result:=case outcome_value when 'callback_requested' then 'follow_up_requested' when 'do_not_call' then 'not_interested' when 'declined' then 'not_interested' when 'wrong_number' then 'bad_number' else 'connected' end;
   select * into r from public.oasis_record_company_sales_contact(job.owner_user_id,job.prospect_id,job.company_uid,'phone',contact_result,
    'AI 상담 결과: '||outcome_value,when_at,now(),'voice:'||job.id::text);
   if coalesce(r.success,false) then
    update public.oasis_voice_jobs set contact_recorded_at=now() where id=job.id;
   else
    update public.oasis_voice_jobs set safe_error_code='CONTACT_SYNC_PENDING',updated_at=now() where id=job.id;
   end if;
   exception when others then
    -- A CRM contact-log outage must NOT undo a customer opt-out or visit request.
    update public.oasis_voice_jobs set safe_error_code='CONTACT_SYNC_PENDING',updated_at=now() where id=job.id;
   end;
  end if;
 else return jsonb_build_object('ok',false,'code','INVALID_INPUT');
 end if;
 insert into public.oasis_voice_events(job_id,company_uid,actor,action,safe_detail) values(job.id,job.company_uid,'worker',p_action,
  jsonb_build_object('outcome',outcome_value,'status',new_status));
 return jsonb_build_object('ok',true);
exception when raise_exception then return jsonb_build_object('ok',false,'code',sqlerrm);
 when invalid_text_representation or datetime_field_overflow or invalid_datetime_format or numeric_value_out_of_range then return jsonb_build_object('ok',false,'code','INVALID_INPUT');
end;
$$;

-- Preserve the existing service-role-only invoker boundary.
revoke all on function public.oasis_voice_worker(text,jsonb) from public,anon,authenticated;
grant execute on function public.oasis_voice_worker(text,jsonb) to service_role;
commit;
