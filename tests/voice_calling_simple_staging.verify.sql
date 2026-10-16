-- v9.14.4 hosted verification: approved EMPTY staging only, NEVER production.
-- Replace both placeholders in memory after reading the staging cluster identity.
-- Apply simple_campaigns and manual_targets migrations twice before this file.
-- Default batch 123; set oasis.verify.batch_size to 1000 for the maximum test.
-- No HTTP/provider call is made. Every row/function change below is rolled back.
-- PostgreSQL sequence advances are not transactional; use a disposable branch.
begin;
set local statement_timeout='120s';
set local lock_timeout='5s';
select set_config('oasis.verify.project_ref','__APPROVED_EMPTY_STAGING_REF__',true);
select set_config('oasis.verify.system_identifier','__APPROVED_STAGING_SYSTEM_IDENTIFIER__',true);
select set_config('oasis.verify.batch_size','123',true);
select set_config('oasis.verify.clock','2030-01-07T01:00:00Z',true);
do $guard$
declare relation_name text; occupied boolean; actual_cluster text;
begin
 if current_setting('oasis.verify.project_ref') !~ '^[a-z]{20}$'
  or current_setting('oasis.verify.project_ref')='fkfyexehylthfftzriil'
  or current_setting('oasis.verify.system_identifier') !~ '^[0-9]{19}$' then
  raise exception 'APPROVED_STAGING_IDENTIFIERS_REQUIRED'; end if;
 select system_identifier::text into actual_cluster from pg_control_system();
 if actual_cluster is distinct from current_setting('oasis.verify.system_identifier') then
  raise exception 'WRONG_STAGING_CLUSTER'; end if;
 foreach relation_name in array array['oasis_users','oasis_customers','oasis_crm','oasis_employment_contacts',
  'oasis_prospect_companies','oasis_prospect_contacts','oasis_company_sales_assignments',
  'oasis_company_sales_contact_logs','oasis_claim_documents','oasis_voice_permissions','oasis_voice_jobs',
  'oasis_voice_events','oasis_voice_campaigns','oasis_voice_manual_targets','oasis_voice_suppressions'] loop
  if to_regclass('public.'||relation_name) is null then raise exception 'STAGING_REQUIRED_TABLE_MISSING'; end if;
  execute format('select exists(select 1 from public.%I)',relation_name) into occupied;
  if occupied then raise exception 'STAGING_MUST_CONTAIN_NO_CUSTOMER_DATA'; end if;
 end loop;
end $guard$;

-- Deterministic weekday/business hours; original definitions return on ROLLBACK.
create function public.oasis_voice_verify_clock_v9144() returns timestamptz
language sql stable security invoker set search_path='' as $$
 select current_setting('oasis.verify.clock')::timestamptz;
$$;
revoke all on function public.oasis_voice_verify_clock_v9144() from public,anon,authenticated;
grant execute on function public.oasis_voice_verify_clock_v9144() to service_role;
do $clock_patch$
declare signature text; definition text;
begin
 foreach signature in array array['public.oasis_voice_target(text)','public.oasis_voice_action(text,text,jsonb)',
  'public.oasis_voice_campaign_action(text,text,jsonb)','public.oasis_voice_worker(text,jsonb)',
  'public.oasis_voice_filtered_campaign(text,jsonb)','public.oasis_voice_manual_call(text,jsonb)',
  'public.oasis_voice_job_contact_ready(uuid)'] loop
  select pg_get_functiondef(signature::regprocedure) into definition;
  execute replace(definition,'now()','public.oasis_voice_verify_clock_v9144()');
 end loop;
end $clock_patch$;
create temporary table voice_verify_results(check_name text primary key) on commit drop;
grant select,insert on voice_verify_results to service_role;
create function pg_temp.voice_assert(p_pass boolean,p_name text) returns void language plpgsql as $$
begin
 if p_pass is distinct from true then raise exception 'VOICE_STAGING_ASSERT_FAILED: %',p_name; end if;
 insert into pg_temp.voice_verify_results values(p_name);
end $$;
create temporary table voice_verify_summary(checks integer,batch_size integer,batch_ms integer) on commit drop;
grant select,insert on voice_verify_summary to service_role;
set local role service_role;
do $verify$
declare
 actor constant text:='synthetic-simple-admin'; member_actor constant text:='synthetic-simple-member';
 wanted integer:=current_setting('oasis.verify.batch_size')::integer;
 r jsonb; r2 jsonb; payload jsonb; manual_payload jsonb; claim jsonb; cid uuid; jid uuid; other_jid uuid;
 signature text; browser_role text; table_name text; bad jsonb; failed boolean;
 source_hash text; prospect_hash text; contact_hash text; assignment_hash text;
 event_count bigint; start_time timestamptz; batch_ms integer; checks integer;
 synthetic_phone text:='010'||lpad('9001',8,'0'); synthetic_other_phone text:='010'||lpad('9002',8,'0');
begin
 perform pg_temp.voice_assert(wanted between 1 and 1000,'valid synthetic batch size');
 insert into public.oasis_users(user_id,name,role,status,created_at,approved_at,approved_by) values
  (actor,'Synthetic administrator','admin','approved',now()::text,now()::text,actor),
  (member_actor,'Synthetic member','member','approved',now()::text,now()::text,actor);
 foreach browser_role in array array['anon','authenticated'] loop
  foreach signature in array array['public.oasis_voice_filtered_campaign(text,jsonb)','public.oasis_voice_manual_call(text,jsonb)',
   'public.oasis_voice_job_target(uuid)','public.oasis_voice_job_contact_ready(uuid)','public.oasis_voice_blocked(text,text)',
   'public.oasis_voice_worker(text,jsonb)'] loop
   perform pg_temp.voice_assert(not has_function_privilege(browser_role,signature,'EXECUTE'),'browser RPC denied '||browser_role||signature);
  end loop;
  foreach table_name in array array['oasis_voice_manual_targets','oasis_voice_jobs','oasis_voice_campaigns'] loop
   perform pg_temp.voice_assert(not has_table_privilege(browser_role,'public.'||table_name,'SELECT,INSERT,UPDATE,DELETE'),
    'browser table denied '||browser_role||table_name);
  end loop;
 end loop;
 foreach signature in array array['public.oasis_voice_filtered_campaign(text,jsonb)','public.oasis_voice_manual_call(text,jsonb)',
  'public.oasis_voice_job_target(uuid)','public.oasis_voice_job_contact_ready(uuid)'] loop
  perform pg_temp.voice_assert((select not prosecdef and proconfig @> array['search_path=""'] from pg_proc where oid=signature::regprocedure),
   'invoker empty path '||signature);
 end loop;
 perform pg_temp.voice_assert((select relrowsecurity from pg_class where oid='public.oasis_voice_manual_targets'::regclass),'manual RLS');
 perform pg_temp.voice_assert(not has_table_privilege('service_role','public.oasis_voice_manual_targets','DELETE'),'snapshots have no delete grant');

 insert into public.oasis_prospect_companies(id,source,source_key,business_no,company_uid,company_name,address,region,industry_name,owner_user_id)
 select md5('hosted-prospect-'||i)::uuid,'nps_monthly','synthetic-simple-'||i,'00081'||lpad(i::text,5,'0'),
  'business:00081'||lpad(i::text,5,'0'),'Synthetic company '||i,'Synthetic region address','Synthetic region','Synthetic bulk',member_actor
 from generate_series(1,wanted) i;
 insert into public.oasis_company_sales_assignments(company_id,company_uid,assigned_user_id,status,assigned_at)
 select id,company_uid,owner_user_id,'assigned',public.oasis_voice_verify_clock_v9144() from public.oasis_prospect_companies;
 insert into public.oasis_prospect_contacts(id,prospect_id,contact_type,contact_value,is_primary,verification_status,confidence,owner_user_id)
 select md5('hosted-contact-'||i)::uuid,md5('hosted-prospect-'||i)::uuid,'phone','010'||lpad(i::text,8,'0'),true,'manual_verified',100,member_actor
 from generate_series(1,wanted) i;
 insert into public.oasis_employment_contacts(contact_key,source_type,source_record_key,business_no,company_name,address,
  province,province_code,industry_name,current_employee_count,employee_growth,current_period,previous_period)
 select 'business:00081'||lpad(i::text,5,'0'),'nps_monthly','synthetic-simple-'||i,'00081'||lpad(i::text,5,'0'),
  'Synthetic company '||i,'Synthetic region address','Synthetic region','ZZ','Synthetic bulk',10,2,'2029','2028'
 from generate_series(1,wanted) i;
 select md5(jsonb_agg(to_jsonb(e) order by contact_key)::text) into source_hash from public.oasis_employment_contacts e;
 select md5(jsonb_agg(to_jsonb(e) order by id)::text) into prospect_hash from public.oasis_prospect_companies e;
 select md5(jsonb_agg(to_jsonb(e) order by id)::text) into contact_hash from public.oasis_prospect_contacts e;
 select md5(jsonb_agg(to_jsonb(e) order by id)::text) into assignment_hash from public.oasis_company_sales_assignments e;
 payload:=jsonb_build_object('request_id','hosted-batch-request','requested_count',wanted,'approval_confirmed',true,
  'filters',jsonb_build_object('business_type','corporate','region','Synthetic region','industry','Synthetic bulk','phone_type','mobile','discovery_type','employment_growth'));
 r:=public.oasis_voice_filtered_campaign(member_actor,payload);
 perform pg_temp.voice_assert(r->>'code'='NOT_AUTHORIZED','member batch denied');
 r:=public.oasis_voice_filtered_campaign(actor,payload);
 perform pg_temp.voice_assert(r->>'code'='NO_ELIGIBLE_TARGETS' and r->>'ok'='false','no implicit consent');
 perform pg_temp.voice_assert((select count(*)=0 from public.oasis_voice_permissions),'no invented permissions');
 perform pg_temp.voice_assert((select count(*)=0 from public.oasis_voice_campaigns),'no fake zero-success campaign');
 foreach bad in array array[payload||'{"requested_count":1001}'::jsonb,payload||'{"approval_confirmed":false}'::jsonb,
  payload||'{"requested_count":"123"}'::jsonb,payload||'{"filters":{"phone_type":"both"}}'::jsonb] loop
  r:=public.oasis_voice_filtered_campaign(actor,bad);
  perform pg_temp.voice_assert(r->>'code'='INVALID_INPUT','batch invalid '||md5(bad::text));
 end loop;
 insert into public.oasis_voice_permissions(company_uid,phone_e164,kind,evidence_ref,granted_at,expires_at,created_by)
 select p.company_uid,public.oasis_voice_phone(c.contact_value),'explicit_consent','Synthetic permission evidence',
  '2030-01-06T00:00:00Z','2030-02-01T00:00:00Z',actor
 from public.oasis_prospect_companies p join public.oasis_prospect_contacts c on c.prospect_id=p.id;
 start_time:=clock_timestamp();
 r:=public.oasis_voice_filtered_campaign(upper(actor),payload);
 batch_ms:=(extract(epoch from clock_timestamp()-start_time)*1000)::integer;
 perform pg_temp.voice_assert(r->>'ok'='true' and (r->>'created_count')::integer=wanted,'requested count durably queued');
 cid:=(r->>'campaign_id')::uuid;
 perform pg_temp.voice_assert((r->>'shortage_count')::integer=0 and r->>'status'='running','atomic approval running');
 perform pg_temp.voice_assert((select count(*)=wanted and bool_and(status='approved') from public.oasis_voice_jobs where campaign_id=cid),'all batch jobs approved');
 perform pg_temp.voice_assert((select count(distinct company_uid)=wanted and count(distinct phone_e164)=wanted from public.oasis_voice_jobs where campaign_id=cid),'UID and phone dedup budget');
 select count(*) into event_count from public.oasis_voice_events;
 r2:=public.oasis_voice_filtered_campaign(actor,payload);
 perform pg_temp.voice_assert(r2->>'replayed'='true' and r2->>'campaign_id'=cid::text,'canonical actor replay');
 perform pg_temp.voice_assert((select count(*)=event_count from public.oasis_voice_events),'replay no duplicate audit');
 r:=public.oasis_voice_filtered_campaign(actor,payload||jsonb_build_object('requested_count',wanted-1));
 perform pg_temp.voice_assert(r->>'code'='IDEMPOTENCY_CONFLICT','request count binding');
 r:=public.oasis_voice_filtered_campaign(actor,payload||'{"request_id":"hosted-batch-new-request"}'::jsonb);
 perform pg_temp.voice_assert(r->>'code'='NO_ELIGIBLE_TARGETS','new request cannot duplicate live queue');
 r:=public.oasis_voice_campaign_action(actor,'pause_campaign',jsonb_build_object('campaign_id',cid));
 perform pg_temp.voice_assert(r->>'ok'='true','batch pause');
 r:=public.oasis_voice_worker('claim','{"provider":"clawops"}');
 perform pg_temp.voice_assert(r->>'code'='EMPTY','paused campaign never dispatches');
 r:=public.oasis_voice_filtered_campaign(actor,payload);
 perform pg_temp.voice_assert(r->>'status'='paused','replay never resumes paused campaign');

 manual_payload:=jsonb_build_object('request_id','hosted-manual-request','company_name','Synthetic manual company','business_type','corporate',
  'phone',synthetic_phone,'purpose','customer_guidance','representative_name','Synthetic person','address','Synthetic visit address',
  'region','Synthetic region','industry','Synthetic industry','consent_confirmed',true,'consent_kind','explicit_consent',
  'evidence_ref','Synthetic permission evidence','granted_at','2030-01-06T00:00:00Z','expires_at','2030-02-01T00:00:00Z','approval_confirmed',true);
 r:=public.oasis_voice_manual_call(member_actor,manual_payload);
 perform pg_temp.voice_assert(r->>'code'='NOT_AUTHORIZED','member manual denied');
 r:=public.oasis_voice_manual_call(actor,manual_payload||'{"consent_confirmed":false}'::jsonb);
 perform pg_temp.voice_assert(r->>'code'='CONSENT_REQUIRED','manual explicit consent required');
 r:=public.oasis_voice_manual_call(actor,manual_payload||'{"consent_confirmed":"true"}'::jsonb);
 perform pg_temp.voice_assert(r->>'code'='CONSENT_REQUIRED','manual consent boolean strict');
 foreach bad in array array['{"granted_at":"2030-01-06T00:00:00"}'::jsonb,'{"expires_at":"infinity"}'::jsonb,
  '{"granted_at":"2031-01-01T00:00:00Z"}'::jsonb,'{"expires_at":"2030-01-01T00:00:00Z"}'::jsonb,
  '{"approval_confirmed":false}'::jsonb,'{"phone":"112"}'::jsonb,'{"purpose":"arbitrary"}'::jsonb] loop
  r:=public.oasis_voice_manual_call(actor,manual_payload||bad);
  perform pg_temp.voice_assert(r->>'code'='INVALID_INPUT','manual invalid '||md5(bad::text));
 end loop;
 perform pg_temp.voice_assert((select count(*)=0 from public.oasis_voice_manual_targets),'invalid manual requests write nothing');
 -- NULL company UID must not bypass existing company-wide opt-out by phone.
 insert into public.oasis_company_kakao_contact_controls(company_uid,status,reason,set_by_user_id)
 values('business:00081'||lpad('1',5,'0'),'opted_out','Synthetic optout',actor);
 r:=public.oasis_voice_manual_call(actor,manual_payload||jsonb_build_object('phone','010'||lpad('1',8,'0')));
 perform pg_temp.voice_assert(r->>'code'='DO_NOT_CALL','manual exact-phone company optout enforced');
 update public.oasis_company_kakao_contact_controls set status='allowed' where company_uid='business:00081'||lpad('1',5,'0');
 r:=public.oasis_voice_manual_call(actor,manual_payload);
 perform pg_temp.voice_assert(r->>'ok'='true' and r->>'status'='approved','manual atomic creation approval');
 jid:=(r->>'job_id')::uuid;
 perform pg_temp.voice_assert((select target_kind='manual' and company_uid is null and prospect_id is null and permission_id is null from public.oasis_voice_jobs where id=jid),'manual no fake CRM identity');
 perform pg_temp.voice_assert((select count(*)=wanted from public.oasis_voice_permissions),'manual no legacy permission fabrication');
 r:=public.oasis_voice_manual_call(actor,manual_payload||jsonb_build_object('phone','+82'||substr(synthetic_phone,2)));
 perform pg_temp.voice_assert(r->>'job_id'=jid::text and r->>'replayed'='true','international phone idempotency');
 r:=public.oasis_voice_manual_call(actor,manual_payload||jsonb_build_object('phone','0082'||substr(synthetic_phone,2)));
 perform pg_temp.voice_assert(r->>'job_id'=jid::text,'international prefix normalization');
 perform set_config('TimeZone','Asia/Seoul',true);
 r:=public.oasis_voice_manual_call(actor,manual_payload);
 perform pg_temp.voice_assert(r->>'job_id'=jid::text,'timestamp fingerprint timezone independent');
 perform set_config('TimeZone','UTC',true);
 r:=public.oasis_voice_manual_call(actor,manual_payload||'{"company_name":"Changed synthetic name"}'::jsonb);
 perform pg_temp.voice_assert(r->>'code'='IDEMPOTENCY_CONFLICT','manual immutable request binding');
 r:=public.oasis_voice_manual_call(actor,manual_payload||'{"request_id":"hosted-manual-duplicate"}'::jsonb);
 perform pg_temp.voice_assert(r->>'code'='DUPLICATE','manual duplicate phone blocked');
 failed:=false;
 begin update public.oasis_voice_manual_targets set company_name='Changed' where id=(select manual_target_id from public.oasis_voice_jobs where id=jid);
 exception when raise_exception then failed:=true; end;
 perform pg_temp.voice_assert(failed,'snapshot immutable');
 failed:=false;
 begin update public.oasis_voice_jobs set target_kind='crm' where id=jid;
 exception when check_violation then failed:=true; end;
 perform pg_temp.voice_assert(failed,'job target shape constraint');
 update public.oasis_voice_jobs set approved_at=public.oasis_voice_verify_clock_v9144()-interval '25 hours' where id=jid;
 r:=public.oasis_voice_worker('claim','{"provider":"clawops"}');
 perform pg_temp.voice_assert(r->>'code'='EMPTY','expired approval never dispatches');
 perform pg_temp.voice_assert((select status='queued' and safe_error_code='APPROVAL_EXPIRED' from public.oasis_voice_jobs where id=jid),'24h expiry preserves queued work');
 r:=public.oasis_voice_action(actor,'approve',jsonb_build_object('job_id',jid));
 perform pg_temp.voice_assert(r->>'ok'='true','explicit reapproval');
 r:=public.oasis_voice_worker('claim',jsonb_build_object('provider','clawops','test_phone_allowlist',jsonb_build_array('+8210'||lpad('9999',8,'0'))));
 perform pg_temp.voice_assert(r->>'code'='EMPTY','test allowlist enforced');
 claim:=public.oasis_voice_worker('claim','{"provider":"clawops"}');
 perform pg_temp.voice_assert(claim->'job'->>'id'=jid::text,'manual worker claim');
 perform pg_temp.voice_assert(claim->'job'->>'address'='Synthetic visit address','manual address reaches worker');
 r:=public.oasis_voice_worker('claim','{"provider":"clawops"}');
 perform pg_temp.voice_assert(r->>'code'='BUSY','global single-call concurrency');
 r:=public.oasis_voice_worker('get_job',jsonb_build_object('job_id',jid,'provider','clawops'));
 perform pg_temp.voice_assert(r->>'ok'='true','manual network preflight');
 r:=public.oasis_voice_worker('connect',jsonb_build_object('job_id',jid,'provider_call_id','clawops:CAhostedmanual01','dispatch_nonce',gen_random_uuid()));
 perform pg_temp.voice_assert(r->>'ok'='false','wrong dispatch nonce blocked');
 r:=public.oasis_voice_worker('connect',jsonb_build_object('job_id',jid,'provider_call_id','clawops:CAhostedmanual01','dispatch_nonce',claim->'job'->>'dispatch_nonce'));
 perform pg_temp.voice_assert(r->>'ok'='true','manual synthetic provider binding');
 r:=public.oasis_voice_worker('get_job',jsonb_build_object('job_id',jid,'provider_call_id','CA'||repeat('1',32)));
 perform pg_temp.voice_assert(r->>'code'='PROVIDER_MISMATCH','provider namespace isolation');
 r:=public.oasis_voice_worker('result',jsonb_build_object('job_id',jid,'provider_call_id','clawops:CAhostedmanual01','outcome','visit_requested',
  'visit_at','2030-01-08T01:00:00Z','address','Synthetic visit address','customer_confirmed',true,'summary','Synthetic visit request'));
 perform pg_temp.voice_assert(r->>'ok'='true','manual result recorded');
 r:=public.oasis_voice_action(actor,'confirm_visit',jsonb_build_object('job_id',jid,'reason','Synthetic schedule confirmed'));
 perform pg_temp.voice_assert(r->>'ok'='true','manual visit confirmed');
 perform pg_temp.voice_assert((select count(*)=0 from public.oasis_company_sales_contact_logs),'no unrelated CRM contact log');
 r:=public.oasis_voice_worker('status',jsonb_build_object('job_id',jid,'provider_call_id','clawops:CAhostedmanual01','status','completed','sequence_number',1,'duration_seconds',30));
 perform pg_temp.voice_assert(r->>'ok'='true','manual terminal status');
 r:=public.oasis_voice_campaign_action(actor,'legacy_jobs','{"limit":100}');
 perform pg_temp.voice_assert(exists(select 1 from jsonb_array_elements(r->'rows') x where x->>'id'=jid::text and x->>'duration_seconds'='30' and x->>'company_name'='Synthetic manual company'),'manual results name duration');
 r:=public.oasis_voice_manual_call(actor,manual_payload||'{"request_id":"hosted-manual-repeat-visit"}'::jsonb);
 perform pg_temp.voice_assert(r->>'code'='DUPLICATE','visit result duplicate guard');

 r:=public.oasis_voice_manual_call(actor,manual_payload||jsonb_build_object('request_id','hosted-manual-revoke','phone',synthetic_other_phone,'purpose','test'));
 other_jid:=(r->>'job_id')::uuid;
 r:=public.oasis_voice_action(actor,'revoke_permission',jsonb_build_object('job_id',other_jid));
 perform pg_temp.voice_assert(r->>'ok'='true','manual consent revoke');
 perform pg_temp.voice_assert((select status='cancelled' from public.oasis_voice_jobs where id=other_jid),'revoke cancels approved work');
 r:=public.oasis_voice_manual_call(actor,manual_payload||jsonb_build_object('request_id','hosted-manual-revoke-retry','phone',synthetic_other_phone));
 perform pg_temp.voice_assert(r->>'code'='DO_NOT_CALL','revocation global phone suppression');
 r:=public.oasis_voice_manual_call(actor,manual_payload||jsonb_build_object('request_id','hosted-manual-expire','phone','010'||lpad('9003',8,'0'),'expires_at','2030-01-07T01:30:00Z'));
 other_jid:=(r->>'job_id')::uuid;
 perform set_config('oasis.verify.clock','2030-01-07T02:00:00Z',true);
 r:=public.oasis_voice_worker('claim','{"provider":"clawops"}');
 perform pg_temp.voice_assert(r->>'code'='TARGET_CHANGED','expired manual consent blocked');
 perform set_config('oasis.verify.clock','2030-01-07T01:00:00Z',true);
 r:=public.oasis_voice_manual_call(actor,manual_payload||jsonb_build_object('request_id','hosted-manual-role','phone','010'||lpad('9004',8,'0')));
 other_jid:=(r->>'job_id')::uuid;
 claim:=public.oasis_voice_worker('claim','{"provider":"clawops"}');
 perform pg_temp.voice_assert(claim->'job'->>'id'=other_jid::text,'next manual queue target');
 update public.oasis_users set role='member' where user_id=actor;
 r:=public.oasis_voice_worker('get_job',jsonb_build_object('job_id',other_jid,'provider','clawops'));
 perform pg_temp.voice_assert(r->>'code'='TARGET_CHANGED','role demotion before network blocked');
 r:=public.oasis_voice_worker('connect',jsonb_build_object('job_id',other_jid,'provider_call_id','clawops:CAhostedrole01','dispatch_nonce',claim->'job'->>'dispatch_nonce'));
 perform pg_temp.voice_assert(r->>'code'='TARGET_CHANGED','role demotion before media blocked');
 r:=public.oasis_voice_filtered_campaign(actor,payload);
 perform pg_temp.voice_assert(r->>'code'='NOT_AUTHORIZED','role change blocks batch replay');
 update public.oasis_users set role='admin' where user_id=actor;
 perform public.oasis_voice_worker('mark_failed',jsonb_build_object('job_id',other_jid,'provider','clawops'));
 r:=public.oasis_voice_manual_call(actor,manual_payload||jsonb_build_object('request_id','hosted-manual-limits','phone','010'||lpad('9005',8,'0')));
 other_jid:=(r->>'job_id')::uuid;
 r:=public.oasis_voice_worker('claim','{"provider":"clawops","daily_limit":1}');
 perform pg_temp.voice_assert(r->>'code'='DAILY_LIMIT','shared daily cap unchanged');
 perform set_config('oasis.verify.clock','2030-01-12T01:00:00Z',true);
 r:=public.oasis_voice_worker('claim','{"provider":"clawops"}');
 perform pg_temp.voice_assert(r->>'code'='OUTSIDE_HOURS','weekend restriction unchanged');
 perform set_config('oasis.verify.clock','2030-01-07T01:00:00Z',true);
 claim:=public.oasis_voice_worker('claim','{"provider":"clawops"}');
 perform pg_temp.voice_assert(claim->'job'->>'id'=other_jid::text,'limit checks preserve next job');
 perform public.oasis_voice_worker('mark_unknown',jsonb_build_object('job_id',other_jid,'provider','clawops'));
 r:=public.oasis_voice_worker('claim','{"provider":"clawops"}');
 perform pg_temp.voice_assert(r->>'code'='BUSY','unknown outcome blocks automatic retry');
 perform pg_temp.voice_assert((select md5(jsonb_agg(to_jsonb(e) order by contact_key)::text)=source_hash from public.oasis_employment_contacts e),'source rows unchanged');
 perform pg_temp.voice_assert((select md5(jsonb_agg(to_jsonb(e) order by id)::text)=prospect_hash from public.oasis_prospect_companies e),'CRM companies unchanged');
 perform pg_temp.voice_assert((select md5(jsonb_agg(to_jsonb(e) order by id)::text)=contact_hash from public.oasis_prospect_contacts e),'CRM contacts unchanged');
 perform pg_temp.voice_assert((select md5(jsonb_agg(to_jsonb(e) order by id)::text)=assignment_hash from public.oasis_company_sales_assignments e),'CRM assignments unchanged');
 select count(*) into checks from pg_temp.voice_verify_results;
 insert into pg_temp.voice_verify_summary values(checks,wanted,batch_ms);
end $verify$;
select 'PASS' as result,checks,batch_size,batch_ms from pg_temp.voice_verify_summary;
rollback;
