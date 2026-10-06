-- v9.14.2: durable bulk call campaigns. No dialing, consent or paid services enabled.
-- Additive tables/FK only; existing jobs, permissions, source contacts and audit stay intact.
begin;
set local lock_timeout = '5s';
set local statement_timeout = '120s';

create table if not exists public.oasis_voice_campaigns (
 id uuid primary key default gen_random_uuid(),
 request_id text not null check(request_id ~ '^[A-Za-z0-9._:-]{8,120}$'),
 created_by text not null,
 name text not null check(length(btrim(name)) between 1 and 100),
 status text not null default 'draft' check(status in ('draft','running','paused','cancelled')),
 creation_result jsonb not null default '{}',
 created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
 unique(created_by,request_id)
);
create index if not exists oasis_voice_campaign_creator on public.oasis_voice_campaigns(created_by,created_at desc,id);
alter table public.oasis_voice_jobs add column if not exists campaign_id uuid references public.oasis_voice_campaigns(id);
create index if not exists oasis_voice_job_campaign on public.oasis_voice_jobs(campaign_id,created_at desc,id) where campaign_id is not null;
alter table public.oasis_voice_campaigns enable row level security;
revoke all on public.oasis_voice_campaigns from public,anon,authenticated,service_role;
grant select,insert,update on public.oasis_voice_campaigns to service_role;

create or replace function public.oasis_voice_campaign_action(p_current_user_id text,p_action text,p_payload jsonb default '{}')
returns jsonb language plpgsql security invoker set search_path='' as $$
declare
 actor text:=lower(btrim(coalesce(p_current_user_id,''))); admin boolean;
 campaign public.oasis_voice_campaigns%rowtype; job public.oasis_voice_jobs%rowtype;
 perms public.oasis_voice_permissions%rowtype; target jsonb; reply jsonb; uid text; code text;
 campaign_key uuid; request_key text; rows_out jsonb:='[]'; skipped jsonb:='[]'; metrics jsonb;
 n integer:=0; made integer:=0; affected integer:=0; lim integer; offst integer;
begin
 if not public.oasis_sales_actor_is_active(actor) then return jsonb_build_object('ok',false,'code','NOT_AUTHORIZED'); end if;
 admin:=public.oasis_sales_actor_is_admin(actor);
 -- Read page sizes are independent of the 30/100 creation limit below.
 lim:=greatest(1,least(100,coalesce((p_payload->>'limit')::integer,30)));
 offst:=greatest(0,least(1000000,coalesce((p_payload->>'offset')::integer,0)));
 if p_action='campaign_stats' then
  select jsonb_build_object('total',count(*),'queued',count(*) filter(where j.status='queued'),
   'approved',count(*) filter(where j.status='approved'),'active',count(*) filter(where j.status in ('dispatching','accepted','in_progress')),
   'completed',count(*) filter(where j.status='completed'),'failed',count(*) filter(where j.status='failed'),
   'cancelled',count(*) filter(where j.status='cancelled'),'unknown',count(*) filter(where j.status='unknown'),
   'visit_requests',count(*) filter(where j.outcome='visit_requested')) into metrics
  from public.oasis_voice_jobs j join public.oasis_company_sales_assignments a on a.id=j.assignment_id
  where admin or (j.owner_user_id=actor and a.assigned_user_id=actor);
  return jsonb_build_object('ok',true,'metrics',metrics);
 elsif p_action='list_campaigns' then
  select coalesce(jsonb_agg(to_jsonb(q)),'[]') into rows_out from (
   select c.id,c.name,c.status,c.created_at,c.updated_at,
    count(j.id) as total,count(j.id) filter(where j.status='queued') as queued,
    count(j.id) filter(where j.status='approved') as approved,
    count(j.id) filter(where j.status in ('dispatching','accepted','in_progress')) as active,
    count(j.id) filter(where j.status='completed') as completed,count(j.id) filter(where j.status='failed') as failed,
    count(j.id) filter(where j.status='cancelled') as cancelled,count(j.id) filter(where j.status='unknown') as unknown,
    count(j.id) filter(where j.outcome='visit_requested') as visit_requests
   from public.oasis_voice_campaigns c left join public.oasis_voice_jobs j on j.campaign_id=c.id
    and (admin or (j.owner_user_id=actor and exists(select 1 from public.oasis_company_sales_assignments a where a.id=j.assignment_id and a.assigned_user_id=actor)))
   where admin or c.created_by=actor or j.id is not null
   group by c.id order by c.created_at desc,c.id limit lim+1 offset offst) q;
  return jsonb_build_object('ok',true,'rows',(select coalesce(jsonb_agg(value),'[]') from jsonb_array_elements(rows_out) with ordinality v(value,num) where num<=lim),'has_more',jsonb_array_length(rows_out)>lim);
 elsif p_action in ('campaign_jobs','legacy_jobs') then
  if p_action='campaign_jobs' then
   campaign_key:=(p_payload->>'campaign_id')::uuid;
   if not exists(select 1 from public.oasis_voice_campaigns c where c.id=campaign_key and (admin or c.created_by=actor
    or exists(select 1 from public.oasis_voice_jobs j join public.oasis_company_sales_assignments a on a.id=j.assignment_id
     where j.campaign_id=c.id and j.owner_user_id=actor and a.assigned_user_id=actor))) then
    return jsonb_build_object('ok',false,'code','NOT_AUTHORIZED'); end if;
  end if;
  select coalesce(jsonb_agg(row_data),'[]') into rows_out from (
   select jsonb_build_object('id',j.id,'campaign_id',j.campaign_id,'company_uid',j.company_uid,'company_name',p.company_name,
    'status',j.status,'outcome',j.outcome,'owner_user_id',j.owner_user_id,'created_at',j.created_at,'visit_at',j.visit_at,
    'address',j.address,'summary',j.summary,'visit_confirmed_at',j.visit_confirmed_at,'safe_error_code',j.safe_error_code,
    'phone_masked','***-****-'||right(j.phone_e164,4),'duration_seconds',j.duration_seconds) row_data
   from public.oasis_voice_jobs j join public.oasis_prospect_companies p on p.id=j.prospect_id
   join public.oasis_company_sales_assignments a on a.id=j.assignment_id
   where (case when p_action='legacy_jobs' then j.campaign_id is null else j.campaign_id=campaign_key end)
    and (admin or (j.owner_user_id=actor and a.assigned_user_id=actor))
   order by j.created_at desc,j.id limit lim+1 offset offst) q;
  return jsonb_build_object('ok',true,'rows',(select coalesce(jsonb_agg(value),'[]') from jsonb_array_elements(rows_out) with ordinality v(value,num) where num<=lim),'has_more',jsonb_array_length(rows_out)>lim);
 end if;
 -- Same cross-instance transaction lock as legacy enqueue/claim; no parallel assignment or dispatch.
 perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('oasis-voice-queue',0));
 if p_action='create_campaign' then
  request_key:=p_payload->>'request_id';
  if request_key is null or request_key !~ '^[A-Za-z0-9._:-]{8,120}$'
   or length(btrim(coalesce(p_payload->>'name',''))) not between 1 and 100
   or jsonb_typeof(p_payload->'company_uids') is distinct from 'array' then raise exception 'INVALID_INPUT'; end if;
  n:=jsonb_array_length(p_payload->'company_uids');
  if n<1 or n>(case when admin then 100 else 30 end)
   or exists(select 1 from jsonb_array_elements(p_payload->'company_uids') x where jsonb_typeof(x) is distinct from 'string' or length(btrim(x #>> '{}'))=0) then raise exception 'INVALID_INPUT'; end if;
  select * into campaign from public.oasis_voice_campaigns where created_by=actor and request_id=request_key for update;
  if campaign.id is not null then
   -- Idempotency refers to the original selection, not the latest button state.
   -- Results include only original caller-supplied identifiers, never target details.
   return campaign.creation_result||jsonb_build_object('ok',true,'campaign_id',campaign.id,'campaign',to_jsonb(campaign)-'creation_result','replayed',true);
  end if;
  insert into public.oasis_voice_campaigns(request_id,created_by,name) values(request_key,actor,btrim(p_payload->>'name')) returning * into campaign;
  for uid in select distinct value from jsonb_array_elements_text(p_payload->'company_uids') loop
   -- Reuse the existing consent, ownership, opt-out, recent-call and global duplicate checks.
   -- Each one-company call is its own PL/pgSQL exception subtransaction: an invalid
   -- target cannot roll back valid targets, nor leave a partially created job.
   reply:=public.oasis_voice_action(actor,'enqueue',jsonb_build_object('company_uids',jsonb_build_array(uid),'request_id','campaign:'||campaign.id::text));
   if coalesce((reply->>'ok')::boolean,false) then
    update public.oasis_voice_jobs set campaign_id=campaign.id,updated_at=now()
     where id=(reply->'rows'->0->>'id')::uuid and requested_by=actor and campaign_id is null;
    get diagnostics affected=row_count;
    made:=made+affected;
   else
    code:=case when reply->>'code' in ('NOT_AUTHORIZED','DO_NOT_CALL','CONSENT_REQUIRED','DUPLICATE','INVALID_INPUT') then reply->>'code' else 'NOT_READY' end;
    skipped:=skipped||jsonb_build_array(jsonb_build_object('company_uid',uid,'code',code));
   end if;
  end loop;
  reply:=jsonb_build_object('ok',true,'campaign_id',campaign.id,'created_count',made,'skipped_count',jsonb_array_length(skipped),'skipped',skipped);
  update public.oasis_voice_campaigns set creation_result=reply,updated_at=now() where id=campaign.id returning * into campaign;
  insert into public.oasis_voice_events(actor,action,safe_detail) values(actor,'campaign_created',jsonb_build_object('campaign_id',campaign.id,'created_count',made,'skipped_count',jsonb_array_length(skipped)));
  return reply||jsonb_build_object('campaign',to_jsonb(campaign)-'creation_result');
 elsif p_action in ('start_campaign','pause_campaign','cancel_campaign') then
  select * into campaign from public.oasis_voice_campaigns where id=(p_payload->>'campaign_id')::uuid for update;
  if campaign.id is null or (not admin and (campaign.created_by<>actor or exists(
   select 1 from public.oasis_voice_jobs j left join public.oasis_company_sales_assignments a on a.id=j.assignment_id
   where j.campaign_id=campaign.id and (j.owner_user_id is distinct from actor or a.assigned_user_id is distinct from actor)))) then raise exception 'NOT_AUTHORIZED'; end if;
  if p_action='start_campaign' then
   if not admin then raise exception 'NOT_AUTHORIZED'; end if;
   if campaign.status='cancelled' then raise exception 'INVALID_STATE'; end if;
   for job in select * from public.oasis_voice_jobs where campaign_id=campaign.id and status in ('queued','approved') order by created_at,id for update loop
    target:=public.oasis_voice_target(job.company_uid);
    select * into perms from public.oasis_voice_permissions where id=job.permission_id;
    if target is null or target->>'owner_user_id' is distinct from job.owner_user_id
     or target->>'phone_e164' is distinct from job.phone_e164 or (target->>'contact_id')::uuid is distinct from job.contact_id
     or (target->>'assignment_id')::uuid is distinct from job.assignment_id or perms.id is null
     or perms.revoked_at is not null or perms.granted_at>now() or perms.expires_at<=now()
     or perms.company_uid is distinct from job.company_uid or perms.phone_e164 is distinct from job.phone_e164
     or public.oasis_voice_blocked(job.company_uid,job.phone_e164) then
     update public.oasis_voice_jobs set status='cancelled',safe_error_code='TARGET_CHANGED',finished_at=now(),updated_at=now() where id=job.id;
     skipped:=skipped||jsonb_build_array(jsonb_build_object('company_uid',job.company_uid,'code','TARGET_CHANGED'));
     insert into public.oasis_voice_events(job_id,company_uid,actor,action) values(job.id,job.company_uid,actor,'campaign_preflight_cancelled');
    else
     update public.oasis_voice_jobs set status='approved',approved_by=actor,approved_at=now(),updated_at=now() where id=job.id;
     made:=made+1;
     insert into public.oasis_voice_events(job_id,company_uid,actor,action) values(job.id,job.company_uid,actor,'campaign_approved');
    end if;
   end loop;
   update public.oasis_voice_campaigns set status='running',updated_at=now() where id=campaign.id;
  elsif p_action='pause_campaign' then
   if campaign.status='cancelled' then raise exception 'INVALID_STATE'; end if;
   update public.oasis_voice_campaigns set status='paused',updated_at=now() where id=campaign.id;
  else
   -- Do not hang up an active call or rewrite terminal outcomes. Only queued approvals stop.
   for job in update public.oasis_voice_jobs set status='cancelled',finished_at=now(),updated_at=now()
    where campaign_id=campaign.id and status in ('queued','approved') returning * loop
    affected:=affected+1;
    insert into public.oasis_voice_events(job_id,company_uid,actor,action) values(job.id,job.company_uid,actor,'campaign_job_cancelled');
   end loop;
   update public.oasis_voice_campaigns set status='cancelled',updated_at=now() where id=campaign.id;
  end if;
  insert into public.oasis_voice_events(actor,action,safe_detail) values(actor,p_action,jsonb_build_object('campaign_id',campaign.id,'approved_count',made,'cancelled_count',affected,'skipped_count',jsonb_array_length(skipped)));
  return jsonb_build_object('ok',true,'campaign_id',campaign.id,'approved_count',made,'cancelled_count',affected,'skipped_count',jsonb_array_length(skipped),'skipped',skipped);
 end if;
 return jsonb_build_object('ok',false,'code','INVALID_INPUT');
exception when raise_exception then return jsonb_build_object('ok',false,'code',sqlerrm);
 when invalid_text_representation or datetime_field_overflow or invalid_datetime_format or numeric_value_out_of_range then return jsonb_build_object('ok',false,'code','INVALID_INPUT');
end;
$$;
revoke all on function public.oasis_voice_campaign_action(text,text,jsonb) from public,anon,authenticated;
grant execute on function public.oasis_voice_campaign_action(text,text,jsonb) to service_role;

-- The worker replacement below retains carrier verification and all v9.14.1 safety gates.
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
   and (campaign_id is null or exists(select 1 from public.oasis_voice_campaigns c where c.id=campaign_id and c.status='running'))
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
  if job.campaign_id is not null and job.connected_at is null and not exists(
   select 1 from public.oasis_voice_campaigns c where c.id=job.campaign_id and c.status='running') then
   return jsonb_build_object('ok',false,'code','CAMPAIGN_NOT_RUNNING'); end if;
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
