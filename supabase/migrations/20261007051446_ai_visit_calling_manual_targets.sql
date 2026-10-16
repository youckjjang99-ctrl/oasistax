-- v9.14.4: immutable manual call snapshots; never create/overwrite CRM records.
-- Local migration only. No provider call, worker startup or calling flag changes.
begin;
set local lock_timeout='5s';
set local statement_timeout='120s';

create table if not exists public.oasis_voice_manual_targets (
 id uuid primary key default gen_random_uuid(),
 created_by text not null, request_id text not null check(request_id ~ '^[A-Za-z0-9._:-]{8,120}$'),
 request_fingerprint text not null,
 company_name text not null check(length(btrim(company_name)) between 1 and 160),
 business_type text not null check(business_type in ('individual','corporate','unknown')),
 phone_e164 text not null check(phone_e164 ~ '^\+82[0-9]{8,10}$'),
 purpose text not null check(purpose in ('test','customer_guidance')),
 representative_name text not null default '' check(length(representative_name)<=80),
 address text not null default '' check(length(address)<=300),
 region text not null default '' check(length(region)<=80),
 industry text not null default '' check(length(industry)<=120),
 consent_confirmed boolean not null check(consent_confirmed is true),
 consent_kind text not null check(consent_kind in ('explicit_consent','callback_request')),
 evidence_ref text not null check(length(btrim(evidence_ref)) between 3 and 500),
 granted_at timestamptz not null, expires_at timestamptz not null,
 revoked_at timestamptz, revoked_by text,
 created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
 check(expires_at>granted_at), unique(created_by,request_id)
);
create index if not exists oasis_voice_manual_actor on public.oasis_voice_manual_targets(created_by,created_at desc,id);
create index if not exists oasis_voice_manual_phone on public.oasis_voice_manual_targets(phone_e164);
alter table public.oasis_voice_manual_targets enable row level security;
revoke all on public.oasis_voice_manual_targets from public,anon,authenticated,service_role;
grant select,insert,update on public.oasis_voice_manual_targets to service_role;

create or replace function public.oasis_voice_manual_snapshot_guard() returns trigger
language plpgsql security invoker set search_path='' as $$
begin
 if (to_jsonb(new)-array['revoked_at','revoked_by','updated_at']) is distinct from
    (to_jsonb(old)-array['revoked_at','revoked_by','updated_at'])
  or (old.revoked_at is not null and new.revoked_at is distinct from old.revoked_at)
  or (old.revoked_by is not null and new.revoked_by is distinct from old.revoked_by)
  or (new.revoked_at is not null and nullif(btrim(new.revoked_by),'') is null) then
  raise exception 'IMMUTABLE_MANUAL_TARGET'; end if;
 return new;
end;
$$;
drop trigger if exists oasis_voice_manual_snapshot_immutable on public.oasis_voice_manual_targets;
create trigger oasis_voice_manual_snapshot_immutable before update on public.oasis_voice_manual_targets
 for each row execute function public.oasis_voice_manual_snapshot_guard();

alter table public.oasis_voice_jobs add column if not exists target_kind text not null default 'crm';
alter table public.oasis_voice_jobs add column if not exists manual_target_id uuid references public.oasis_voice_manual_targets(id);
alter table public.oasis_voice_jobs alter column company_uid drop not null;
alter table public.oasis_voice_jobs alter column assignment_id drop not null;
alter table public.oasis_voice_jobs alter column prospect_id drop not null;
alter table public.oasis_voice_jobs alter column contact_id drop not null;
alter table public.oasis_voice_jobs alter column permission_id drop not null;
do $constraints$
begin
 if not exists(select 1 from pg_constraint where conrelid='public.oasis_voice_jobs'::regclass and conname='oasis_voice_job_target_shape') then
  alter table public.oasis_voice_jobs add constraint oasis_voice_job_target_shape check(
   (target_kind='crm' and company_uid is not null and assignment_id is not null and prospect_id is not null
    and contact_id is not null and permission_id is not null and manual_target_id is null)
   or (target_kind='manual' and company_uid is null and assignment_id is null and prospect_id is null
    and contact_id is null and permission_id is null and manual_target_id is not null));
 end if;
end $constraints$;

-- Manual callers have no company UID. Resolve *only an exact normalized phone*
-- to existing CRM companies so a phone-only entry cannot bypass company opt-out.
-- This indexes the CRM contacts, never the employment collector's source table.
create index if not exists oasis_voice_phone_company_lookup
 on public.oasis_prospect_contacts(public.oasis_voice_phone(contact_value),prospect_id)
 where contact_type in ('phone','mobile','landline','mobile_phone','landline_phone');
create or replace function public.oasis_voice_blocked(p_uid text,p_phone text) returns boolean
language sql stable security invoker set search_path='' as $$
 with linked_uids as materialized (
  select p_uid as company_uid where p_uid is not null
  union
  select p.company_uid from public.oasis_prospect_contacts c
   join public.oasis_prospect_companies p on p.id=c.prospect_id
   where c.contact_type in ('phone','mobile','landline','mobile_phone','landline_phone')
    and public.oasis_voice_phone(c.contact_value)=p_phone and p.company_uid is not null
 )
 select exists(select 1 from public.oasis_voice_suppressions s
  where s.company_uid in (select company_uid from linked_uids) or s.phone_e164=p_phone)
 or exists(select 1 from public.oasis_prospect_contacts c join public.oasis_prospect_companies p on p.id=c.prospect_id
  where p.company_uid in (select company_uid from linked_uids) and (c.do_not_contact or c.opt_out_at is not null))
 or exists(select 1 from public.oasis_prospect_contacts c where public.oasis_voice_phone(c.contact_value)=p_phone
  and (c.do_not_contact or c.opt_out_at is not null))
 or exists(select 1 from public.oasis_company_kakao_contact_controls c
  where c.company_uid in (select company_uid from linked_uids) and c.status in ('opted_out','admin_blocked'));
$$;
create unique index if not exists oasis_voice_job_manual_target on public.oasis_voice_jobs(manual_target_id) where manual_target_id is not null;
-- A phone-only suppression is global across manual and CRM targets.
alter table public.oasis_voice_suppressions alter column company_uid drop not null;
do $constraints$
begin
 if not exists(select 1 from pg_constraint where conrelid='public.oasis_voice_suppressions'::regclass and conname='oasis_voice_suppression_manual_phone') then
  alter table public.oasis_voice_suppressions add constraint oasis_voice_suppression_manual_phone
   check(company_uid is not null or phone_e164 ~ '^\+82[0-9]{8,10}$');
 end if;
end $constraints$;

create or replace function public.oasis_voice_job_target(p_job_id uuid) returns jsonb
language plpgsql stable security invoker set search_path='' as $$
declare j public.oasis_voice_jobs%rowtype; m public.oasis_voice_manual_targets%rowtype;
begin
 select * into j from public.oasis_voice_jobs where id=p_job_id;
 if j.id is null then return null; end if;
 if j.target_kind='crm' then return public.oasis_voice_target(j.company_uid); end if;
 select * into m from public.oasis_voice_manual_targets where id=j.manual_target_id;
 if m.id is null or m.created_by is distinct from j.owner_user_id
  or not coalesce(public.oasis_sales_actor_is_active(m.created_by),false)
  or not coalesce(public.oasis_sales_actor_is_admin(m.created_by),false) then return null; end if;
 return jsonb_build_object('company_uid',null,'target_kind','manual','manual_target_id',m.id,
  'manual_kind',m.purpose,'owner_user_id',m.created_by,'assignment_id',null,'prospect_id',null,'contact_id',null,
  'phone_e164',m.phone_e164,'company_name',m.company_name,'representative_name',m.representative_name,
  'address',m.address,'region',m.region,'industry',m.industry,'business_type',m.business_type,'purpose',m.purpose);
end;
$$;

-- Shared fail-closed mutable target/consent check, independent of approval age.
-- Campaign resume can validate a queued job before granting a new approval.
create or replace function public.oasis_voice_job_contact_ready(p_job_id uuid) returns boolean
language plpgsql stable security invoker set search_path='' as $$
declare j public.oasis_voice_jobs%rowtype; m public.oasis_voice_manual_targets%rowtype;
 p public.oasis_voice_permissions%rowtype; t jsonb;
begin
 select * into j from public.oasis_voice_jobs where id=p_job_id;
 if j.id is null then return false; end if;
 t:=public.oasis_voice_job_target(j.id);
 if t is null or t->>'owner_user_id' is distinct from j.owner_user_id
  or t->>'phone_e164' is distinct from j.phone_e164
  or public.oasis_voice_blocked(j.company_uid,j.phone_e164) then return false; end if;
 if j.target_kind='manual' then
  select * into m from public.oasis_voice_manual_targets where id=j.manual_target_id;
  return coalesce(m.id is not null and m.consent_confirmed is true and m.revoked_at is null
   and m.granted_at<=now() and m.expires_at>now() and m.created_by=j.requested_by,false);
 end if;
 select * into p from public.oasis_voice_permissions where id=j.permission_id;
 return coalesce((t->>'assignment_id')::uuid is not distinct from j.assignment_id
  and (t->>'prospect_id')::uuid is not distinct from j.prospect_id
  and (t->>'contact_id')::uuid is not distinct from j.contact_id
  and p.id is not null and p.company_uid is not distinct from j.company_uid
  and p.phone_e164 is not distinct from j.phone_e164 and p.revoked_at is null
  and p.granted_at<=now() and p.expires_at>now(),false);
end;
$$;

create or replace function public.oasis_voice_manual_call(p_current_user_id text,p_payload jsonb default '{}')
returns jsonb language plpgsql security invoker set search_path='' as $$
declare actor text:=lower(btrim(coalesce(p_current_user_id,''))); normalized jsonb; fingerprint text;
 request_key text; phone text; start_at timestamptz; end_at timestamptz; field text;
 m public.oasis_voice_manual_targets%rowtype; j public.oasis_voice_jobs%rowtype;
begin
 if not coalesce(public.oasis_sales_actor_is_active(actor),false)
  or not coalesce(public.oasis_sales_actor_is_admin(actor),false) then
  return jsonb_build_object('ok',false,'code','NOT_AUTHORIZED'); end if;
 if jsonb_typeof(p_payload) is distinct from 'object' then raise exception 'INVALID_INPUT'; end if;
 if p_payload->'consent_confirmed' is distinct from 'true'::jsonb then raise exception 'CONSENT_REQUIRED'; end if;
 if p_payload->'approval_confirmed' is distinct from 'true'::jsonb then raise exception 'INVALID_INPUT'; end if;
 if p_payload ? 'target_kind' then raise exception 'INVALID_INPUT'; end if;
 foreach field in array array['request_id','company_name','business_type','phone','purpose','representative_name','address','region','industry','consent_kind','evidence_ref','granted_at','expires_at'] loop
  if p_payload ? field and jsonb_typeof(p_payload->field) is distinct from 'string' then raise exception 'INVALID_INPUT'; end if;
  if coalesce(p_payload->>field,'') ~ '[[:cntrl:]]' then raise exception 'INVALID_INPUT'; end if;
 end loop;
 if coalesce(p_payload->>'granted_at','') !~ '(Z|[+-][0-9]{2}:[0-9]{2})$'
  or coalesce(p_payload->>'expires_at','') !~ '(Z|[+-][0-9]{2}:[0-9]{2})$' then raise exception 'INVALID_INPUT'; end if;
 request_key:=p_payload->>'request_id'; phone:=public.oasis_voice_phone(p_payload->>'phone');
 start_at:=(p_payload->>'granted_at')::timestamptz; end_at:=(p_payload->>'expires_at')::timestamptz;
 if request_key is null or request_key !~ '^[A-Za-z0-9._:-]{8,120}$' then raise exception 'INVALID_INPUT'; end if;
 if phone is null then raise exception 'INVALID_INPUT'; end if;
 if coalesce(p_payload->>'business_type','') not in ('individual','corporate','unknown')
  or length(btrim(coalesce(p_payload->>'company_name',''))) not between 1 and 160
  or coalesce(p_payload->>'purpose','') not in ('test','customer_guidance')
  or length(btrim(coalesce(p_payload->>'representative_name','')))>80
  or length(btrim(coalesce(p_payload->>'address','')))>300
  or length(btrim(coalesce(p_payload->>'region','')))>80
  or length(btrim(coalesce(p_payload->>'industry','')))>120
  or coalesce(p_payload->>'consent_kind','') not in ('explicit_consent','callback_request')
  or length(btrim(coalesce(p_payload->>'evidence_ref',''))) not between 3 and 500
  or start_at is null or end_at is null or not isfinite(start_at) or not isfinite(end_at)
  or start_at>now() or end_at<=now() or end_at<=start_at
  or end_at>now()+interval '2 years'
  or (p_payload->>'consent_kind'='callback_request' and end_at>now()+interval '31 days') then raise exception 'INVALID_INPUT'; end if;
 normalized:=jsonb_build_object('company_name',btrim(p_payload->>'company_name'),
  'business_type',p_payload->>'business_type','phone_e164',phone,'purpose',btrim(p_payload->>'purpose'),
  'representative_name',btrim(coalesce(p_payload->>'representative_name','')),'address',btrim(coalesce(p_payload->>'address','')),
  'region',btrim(coalesce(p_payload->>'region','')),'industry',btrim(coalesce(p_payload->>'industry','')),
  'consent_kind',p_payload->>'consent_kind','evidence_ref',btrim(p_payload->>'evidence_ref'),
  'granted_at',start_at,'expires_at',end_at);
 -- Absolute instants keep retry identity stable across database session timezones.
 fingerprint:=md5((normalized||jsonb_build_object('granted_at',extract(epoch from start_at),'expires_at',extract(epoch from end_at)))::text);
 perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('oasis-voice-queue',0));
 select * into m from public.oasis_voice_manual_targets where created_by=actor and request_id=request_key;
 if m.id is not null then
  if m.request_fingerprint is distinct from fingerprint then raise exception 'IDEMPOTENCY_CONFLICT'; end if;
  select * into j from public.oasis_voice_jobs where manual_target_id=m.id;
  if j.id is null then raise exception 'INVALID_STATE'; end if;
  return jsonb_build_object('ok',true,'job_id',j.id,'created_count',1,'replayed',true,'status',j.status,
   'phone_masked','***-****-'||right(m.phone_e164,4));
 end if;
 if public.oasis_voice_blocked(null,phone) then raise exception 'DO_NOT_CALL'; end if;
 if exists(select 1 from public.oasis_voice_jobs where phone_e164=phone and
  (status in ('queued','approved','dispatching','accepted','in_progress','unknown') or dispatch_at>now()-interval '7 days'
   or outcome in ('visit_requested','callback_requested'))) then raise exception 'DUPLICATE'; end if;
 insert into public.oasis_voice_manual_targets(created_by,request_id,request_fingerprint,company_name,business_type,phone_e164,purpose,
  representative_name,address,region,industry,consent_confirmed,consent_kind,evidence_ref,granted_at,expires_at)
 values(actor,request_key,fingerprint,normalized->>'company_name',normalized->>'business_type',phone,normalized->>'purpose',
  normalized->>'representative_name',normalized->>'address',normalized->>'region',normalized->>'industry',true,
  normalized->>'consent_kind',normalized->>'evidence_ref',start_at,end_at) returning * into m;
 insert into public.oasis_voice_jobs(requested_by,request_id,owner_user_id,target_kind,manual_target_id,company_uid,assignment_id,prospect_id,contact_id,permission_id,
  phone_e164,status,approved_by,approved_at,address)
 values(actor,request_key,actor,'manual',m.id,null,null,null,null,null,phone,'approved',actor,now(),m.address) returning * into j;
 insert into public.oasis_voice_events(job_id,actor,action,safe_detail) values(j.id,actor,'manual_call_approved',jsonb_build_object('manual_target_id',m.id,'purpose',m.purpose));
 return jsonb_build_object('ok',true,'job_id',j.id,'created_count',1,'replayed',false,'status',j.status,
  'phone_masked','***-****-'||right(phone,4));
exception when raise_exception then return jsonb_build_object('ok',false,'code',
 case when sqlerrm in ('INVALID_INPUT','CONSENT_REQUIRED','IDEMPOTENCY_CONFLICT','INVALID_STATE','DO_NOT_CALL','DUPLICATE') then sqlerrm else 'NOT_READY' end);
 when invalid_text_representation or invalid_datetime_format or datetime_field_overflow or numeric_value_out_of_range then
  return jsonb_build_object('ok',false,'code','INVALID_INPUT');
end;
$$;

-- Patch installed function bodies, preserving provider/nonce/opt-out/campaign gates.
-- Markers make replay a no-op, and every expected source fragment is checked.
do $patch$
declare d text; old text; replacement text; sig text;
begin
 sig:='public.oasis_voice_worker(text,jsonb)';
 select replace(pg_get_functiondef(sig::regprocedure),chr(13),'') into d;
 if position('-- oasis_voice_manual_worker_v9144' in d)=0 then
  d:=replace(d,'target:=public.oasis_voice_target(job.company_uid);','target:=public.oasis_voice_job_target(job.id);');
  old:=$old$  select * into job from public.oasis_voice_jobs where status='approved'$old$;
  if position(old in d)=0 then raise exception 'MANUAL_WORKER_SOURCE_NOT_RECOGNIZED'; end if;
  d:=replace(d,old,$new$  -- oasis_voice_manual_worker_v9144
  with expired as (
   update public.oasis_voice_jobs set status='queued',approved_at=null,approved_by=null,
    safe_error_code='APPROVAL_EXPIRED',updated_at=now()
   where status='approved' and (approved_at is null or approved_at<=now()-interval '24 hours') returning id,company_uid)
  insert into public.oasis_voice_events(job_id,company_uid,actor,action)
   select id,company_uid,'worker','approval_expired' from expired;
  select * into job from public.oasis_voice_jobs where status='approved'$new$);
  old:=$old$  if target is null or target->>'owner_user_id'<>job.owner_user_id or target->>'phone_e164'<>job.phone_e164
   or (target->>'contact_id')::uuid<>job.contact_id or job.approved_at<now()-interval '24 hours'
   or not public.oasis_sales_actor_is_admin(job.approved_by)
   or perms.revoked_at is not null or perms.granted_at>now() or perms.expires_at<=now()
   or public.oasis_voice_blocked(job.company_uid,job.phone_e164) then$old$;
  if position(old in d)=0 then raise exception 'MANUAL_WORKER_PREFLIGHT_NOT_RECOGNIZED'; end if;
  d:=replace(d,old,$new$  if not public.oasis_voice_job_contact_ready(job.id)
   or job.approved_at is null or job.approved_at<=now()-interval '24 hours'
   or not coalesce(public.oasis_sales_actor_is_active(job.approved_by),false)
   or not coalesce(public.oasis_sales_actor_is_admin(job.approved_by),false) then$new$);
  old:=$old$  if target is null or target->>'owner_user_id'<>job.owner_user_id or target->>'phone_e164'<>job.phone_e164
   or perms.revoked_at is not null or perms.expires_at<=now() or public.oasis_voice_blocked(job.company_uid,job.phone_e164)
   or job.status not in ('dispatching','accepted','in_progress') then return jsonb_build_object('ok',false,'code','TARGET_CHANGED'); end if;$old$;
  if position(old in d)=0 then raise exception 'MANUAL_WORKER_CONNECT_NOT_RECOGNIZED'; end if;
  d:=replace(d,old,$new$  if not public.oasis_voice_job_contact_ready(job.id)
   or job.approved_at is null or job.approved_at<=now()-interval '24 hours'
   or not coalesce(public.oasis_sales_actor_is_active(job.approved_by),false)
   or not coalesce(public.oasis_sales_actor_is_admin(job.approved_by),false)
   or job.status not in ('dispatching','accepted','in_progress') then return jsonb_build_object('ok',false,'code','TARGET_CHANGED'); end if;$new$);
  execute d;
 end if;

 foreach sig in array array['public.oasis_voice_action(text,text,jsonb)','public.oasis_voice_campaign_action(text,text,jsonb)'] loop
  select replace(pg_get_functiondef(sig::regprocedure),chr(13),'') into d;
  if position('-- oasis_voice_manual_results_v9144' in d)>0 then continue; end if;
  if position('-- oasis_voice_admin_only_v9143' in d)=0 then raise exception 'MANUAL_ADMIN_GATE_MISSING'; end if;
  d:=replace(d,'-- oasis_voice_admin_only_v9143','-- oasis_voice_admin_only_v9143'||chr(10)||' -- oasis_voice_manual_results_v9144');
  d:=replace(d,'''company_name'',p.company_name','''company_name'',coalesce(p.company_name,m.company_name),''target_kind'',j.target_kind,''manual_kind'',m.purpose,''purpose'',m.purpose');
  d:=replace(d,'from public.oasis_voice_jobs j join public.oasis_prospect_companies p on p.id=j.prospect_id',
   'from public.oasis_voice_jobs j left join public.oasis_prospect_companies p on p.id=j.prospect_id left join public.oasis_voice_manual_targets m on m.id=j.manual_target_id');
  d:=replace(d,'join public.oasis_company_sales_assignments a on a.id=j.assignment_id','left join public.oasis_company_sales_assignments a on a.id=j.assignment_id');
  -- Do not produce a duplicate LEFT token for an already-left join.
  d:=replace(d,'left left join','left join');
  if sig='public.oasis_voice_campaign_action(text,text,jsonb)' then
   d:=replace(d,'target:=public.oasis_voice_target(job.company_uid);','target:=public.oasis_voice_job_target(job.id);');
   old:=$old$    if target is null or target->>'owner_user_id' is distinct from job.owner_user_id
     or target->>'phone_e164' is distinct from job.phone_e164 or (target->>'contact_id')::uuid is distinct from job.contact_id
     or (target->>'assignment_id')::uuid is distinct from job.assignment_id or perms.id is null
     or perms.revoked_at is not null or perms.granted_at>now() or perms.expires_at<=now()
     or perms.company_uid is distinct from job.company_uid or perms.phone_e164 is distinct from job.phone_e164
     or public.oasis_voice_blocked(job.company_uid,job.phone_e164) then$old$;
   if position(old in d)=0 then raise exception 'MANUAL_CAMPAIGN_PREFLIGHT_NOT_RECOGNIZED'; end if;
   d:=replace(d,old,'    if not public.oasis_voice_job_contact_ready(job.id) then');
   d:=replace(d,$old$set status='approved',approved_by=actor,approved_at=now(),updated_at=now()$old$,
    $new$set status='approved',approved_by=actor,approved_at=now(),safe_error_code=null,updated_at=now()$new$);
  else
   old:=$old$   select * into result_contact from public.oasis_record_company_sales_contact(actor,job.prospect_id,job.company_uid,'phone','consultation_scheduled',
    'AI 방문요청 담당자 확인',job.visit_at,now(),'voice-confirm:'||job.id::text);
   if not coalesce(result_contact.success,false) then raise exception 'TARGET_CHANGED'; end if;$old$;
   if position(old in d)=0 then raise exception 'MANUAL_VISIT_CONFIRM_NOT_RECOGNIZED'; end if;
   d:=replace(d,old,'   if job.target_kind=''crm'' then'||chr(10)||old||chr(10)||'   end if;');
   old:=$old$   update public.oasis_voice_jobs set status='approved',approved_by=actor,approved_at=now(),updated_at=now() where id=job.id;$old$;
   if position(old in d)=0 then raise exception 'MANUAL_APPROVAL_NOT_RECOGNIZED'; end if;
   d:=replace(d,old,$new$   if not public.oasis_voice_job_contact_ready(job.id) then raise exception 'TARGET_CHANGED'; end if;
   update public.oasis_voice_jobs set status='approved',approved_by=actor,approved_at=now(),safe_error_code=null,updated_at=now() where id=job.id;$new$);
   old:=$old$ elsif p_action in ('revoke_permission','do_not_call') then
  uid:=p_payload->>'company_uid'; target:=public.oasis_voice_target(uid);$old$;
   if position(old in d)=0 then raise exception 'MANUAL_REVOCATION_NOT_RECOGNIZED'; end if;
   d:=replace(d,old,$new$ elsif p_action in ('revoke_permission','do_not_call') then
  if p_payload ? 'job_id' then
   select * into job from public.oasis_voice_jobs where id=(p_payload->>'job_id')::uuid for update;
   if job.id is null or job.target_kind<>'manual' then raise exception 'INVALID_INPUT'; end if;
   update public.oasis_voice_manual_targets set revoked_at=coalesce(revoked_at,now()),revoked_by=coalesce(revoked_by,actor),updated_at=now() where id=job.manual_target_id;
   insert into public.oasis_voice_suppressions(company_uid,phone_e164,reason,created_by)
    values(null,job.phone_e164,case when p_action='do_not_call' then 'do_not_call' else 'revoked' end,actor);
   update public.oasis_voice_jobs set status='cancelled',finished_at=now(),updated_at=now()
    where phone_e164=job.phone_e164 and status in ('queued','approved');
   insert into public.oasis_voice_events(job_id,actor,action) values(job.id,actor,p_action);
   return jsonb_build_object('ok',true);
  end if;
  uid:=p_payload->>'company_uid'; target:=public.oasis_voice_target(uid);$new$);
  end if;
  execute d;
 end loop;
end $patch$;

revoke all on function public.oasis_voice_manual_snapshot_guard(),public.oasis_voice_job_target(uuid),public.oasis_voice_job_contact_ready(uuid),public.oasis_voice_manual_call(text,jsonb) from public,anon,authenticated;
grant execute on function public.oasis_voice_manual_snapshot_guard(),public.oasis_voice_job_target(uuid),public.oasis_voice_job_contact_ready(uuid),public.oasis_voice_manual_call(text,jsonb) to service_role;
revoke all on function public.oasis_voice_blocked(text,text) from public,anon,authenticated;
grant execute on function public.oasis_voice_blocked(text,text) to service_role;
revoke all on function public.oasis_voice_worker(text,jsonb),public.oasis_voice_action(text,text,jsonb),public.oasis_voice_campaign_action(text,text,jsonb) from public,anon,authenticated;
grant execute on function public.oasis_voice_worker(text,jsonb),public.oasis_voice_action(text,text,jsonb),public.oasis_voice_campaign_action(text,text,jsonb) to service_role;
commit;
