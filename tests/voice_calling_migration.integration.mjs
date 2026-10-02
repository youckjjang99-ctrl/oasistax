// Offline PostgreSQL integration contract test. NEVER connects to Supabase.
// PGLITE_PACKAGE may point at an isolated @electric-sql/pglite/dist/index.js.
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
process.on('uncaughtException', error => {
 console.error('ISOLATED TEST FAILED:', error.message, error.code || '', error.position || '', error.where || '');
 process.exit(1);
});
const { PGlite } = await import(process.env.PGLITE_PACKAGE || '@electric-sql/pglite');
const db = new PGlite();
const migration = await readFile(new URL('../supabase/migrations/20261001235500_ai_visit_calling.sql', import.meta.url), 'utf8');
let checks = 0;
function check(actual, expected, label) { assert.deepEqual(actual, expected, label); checks++; }
async function scalar(sql) { return (await db.query(sql)).rows[0].v; }
async function action(actor, name, payload={}) { return (await db.query('select public.oasis_voice_action($1,$2,$3::jsonb) v',[actor,name,JSON.stringify(payload)])).rows[0].v; }
async function worker(name, payload={}) { return (await db.query('select public.oasis_voice_worker($1,$2::jsonb) v',[name,JSON.stringify(payload)])).rows[0].v; }
await db.exec(`
 create role anon; create role authenticated; create role service_role bypassrls;
 create table public.oasis_users(user_id text primary key,role text,status text);
 insert into public.oasis_users values ('alice','member','approved'),('bob','member','approved'),('admin','admin','approved'),('suspended','member','suspended');
 create table public.oasis_prospect_companies(id uuid primary key,company_uid text,company_name text,address text,owner_user_id text,source_data jsonb default '{}',updated_at timestamptz default now());
 create table public.oasis_prospect_contacts(id uuid primary key,prospect_id uuid references public.oasis_prospect_companies,contact_type text,contact_value text,verification_status text default 'auto_verified',is_primary boolean default true,confidence int default 100,updated_at timestamptz default now(),do_not_contact boolean default false,opt_out_at timestamptz);
 create table public.oasis_company_sales_assignments(id uuid primary key,company_uid text,company_id uuid,assigned_user_id text,status text default 'assigned',assignment_expires_at timestamptz,permanently_excluded boolean default false,migration_conflict boolean default false,released_at timestamptz,updated_at timestamptz default now(),contact_count int default 0);
 create table public.oasis_company_kakao_contact_controls(company_uid text,status text);
 create table public.test_contact_logs(company_uid text,result text);
 create function public.oasis_sales_actor_is_active(u text) returns boolean language sql as $$ select exists(select 1 from public.oasis_users where user_id=u and status='approved') $$;
 create function public.oasis_sales_actor_is_admin(u text) returns boolean language sql as $$ select exists(select 1 from public.oasis_users where user_id=u and status='approved' and role='admin') $$;
 create function public.oasis_normalize_sales_phone(v text) returns text language sql immutable as $$ select regexp_replace(v,'[^0-9]','','g') $$;
 create function public.oasis_record_company_sales_contact(actor text,pid uuid,uid text,method text,result text,notes text,nxt timestamptz,at_time timestamptz,session text) returns table(success boolean) language plpgsql as $$ begin
  insert into public.test_contact_logs values(uid,result);
  update public.oasis_company_sales_assignments set contact_count=contact_count+1 where company_uid=uid;
  return query select true; end $$;
 create function public.voice_test_now() returns timestamptz language sql stable as $$ select '2030-01-07T01:00:00Z'::timestamptz $$;
 grant all on all tables in schema public to service_role;
 insert into public.oasis_prospect_companies values
 ('00000000-0000-4000-8000-000000000001','business:0000000001','Synthetic A','Synthetic visit location A','alice','{}',now()),
 ('00000000-0000-4000-8000-000000000002','business:0000000002','Synthetic B','Synthetic visit location B','bob','{}',now()),
 ('00000000-0000-4000-8000-000000000003','business:0000000003','Synthetic C','Synthetic visit location C','alice','{}',now());
 insert into public.oasis_company_sales_assignments(id,company_uid,company_id,assigned_user_id) select id,company_uid,id,owner_user_id from public.oasis_prospect_companies;
 insert into public.oasis_prospect_contacts(id,prospect_id,contact_type,contact_value) values
 ('00000000-0000-4000-8000-000000000011','00000000-0000-4000-8000-000000000001','phone','01000000001'),
 ('00000000-0000-4000-8000-000000000012','00000000-0000-4000-8000-000000000002','phone','01000000002'),
 ('00000000-0000-4000-8000-000000000013','00000000-0000-4000-8000-000000000003','phone','01000000003');
`);
await db.exec(migration);
await db.exec(migration);
check(await scalar('select count(*)::int v from public.oasis_prospect_companies'),3,'additive twice preserves source');
check(await scalar("select count(*)::int v from pg_class where relname like 'oasis_voice_%' and relkind='r' and relrowsecurity"),4,'RLS enabled');
check(await scalar("select has_function_privilege('anon','public.oasis_voice_action(text,text,jsonb)','execute') v"),false,'anon denied');
check(await scalar("select has_function_privilege('authenticated','public.oasis_voice_worker(text,jsonb)','execute') v"),false,'browser cannot forge worker');
check(await scalar("select has_table_privilege('authenticated','public.oasis_voice_jobs','select') v"),false,'no direct browser reads');
check(await scalar("select has_table_privilege('service_role','public.oasis_voice_events','delete') v"),false,'append-only audit');
check(await scalar("select has_table_privilege('service_role','public.oasis_voice_jobs','delete') v"),false,'no hard delete');
// Exact DDL already executed twice. Freeze only the test copy of the clock so
// business-hour/consent tests can run at any wall-clock time (production uses now()).
await db.exec(migration.replace(/\bnow\(\)/g,'public.voice_test_now()'));
await db.exec('set role service_role');
const uidA='business:0000000001', uidB='business:0000000002', uidC='business:0000000003';
check((await action('suspended','candidates')).code,'NOT_AUTHORIZED','inactive actor');
check((await action('alice','candidates')).rows.length,2,'owner candidate scope');
check((await action('bob','candidates')).rows.length,1,'other owner candidate scope');
check((await action('admin','candidates')).rows.length,3,'admin candidate scope');
check((await action('alice','enqueue',{company_uids:[uidA],request_id:'test-request-a'})).code,'CONSENT_REQUIRED','no implicit public phone consent');
const consent={kind:'explicit_consent',evidence_ref:'fixture-consent-reference',granted_at:'2030-01-06T00:00:00Z',expires_at:'2030-02-01T00:00:00Z'};
check((await action('alice','grant_permission',{...consent,company_uids:[uidA,uidB]})).code,'NOT_AUTHORIZED','batch ownership');
check(await scalar('select count(*)::int v from public.oasis_voice_permissions'),0,'failed batch rolls back all');
check((await action('admin','grant_permission',{...consent,company_uids:[uidA,uidB,uidC]})).ok,true,'documented batch consent');
check((await action('alice','candidates')).rows.every(x=>x.eligible && !('phone_e164' in x)),true,'masked scoped eligibility');
let enqueued=await action('alice','enqueue',{company_uids:[uidA],request_id:'test-request-a'});
check(enqueued.ok,true,'queue request');
const id=enqueued.rows[0].id;
check((await action('alice','enqueue',{company_uids:[uidA],request_id:'test-request-a'})).rows[0].id,id,'idempotent queue');
check((await action('alice','enqueue',{company_uids:[uidA],request_id:'test-request-another'})).code,'DUPLICATE','global duplicate queue');
check((await action('bob','list_jobs')).rows.length,0,'no cross-owner details');
check((await action('alice','approve',{job_id:id})).code,'NOT_AUTHORIZED','only admin approves');
check((await action('admin','approve',{job_id:id})).ok,true,'admin approval');
const claim=await worker('claim');
check(claim.job.id,id,'atomic claim');
check((await worker('claim')).code,'BUSY','second worker cannot dispatch same or parallel job');
const sid='CA'+'1'.repeat(32);
check((await worker('connect',{job_id:id,provider_call_id:sid,dispatch_nonce:claim.job.dispatch_nonce})).ok,true,'callback can arrive before dispatch return');
check((await worker('connect',{job_id:id,provider_call_id:sid,dispatch_nonce:claim.job.dispatch_nonce})).code,'PROVIDER_MISMATCH','stream replay rejected');
check((await worker('mark_dispatched',{job_id:id,provider_call_id:sid})).ok,true,'late dispatch acceptance');
check((await action('alice','list_jobs')).rows[0].status,'in_progress','acceptance never regresses active call');
check((await worker('result',{job_id:id,provider_call_id:sid,outcome:'visit_requested',customer_confirmed:false})).code,'INVALID_RESULT','no assumed booking');
const visit={job_id:id,provider_call_id:sid,outcome:'visit_requested',customer_confirmed:true,visit_at:'2030-01-08T10:00:00+09:00',address:'Synthetic confirmed visit address',summary:'Visit requested'};
check((await worker('result',visit)).ok,true,'provisional visit saved');
check((await worker('result',visit)).code,'DUPLICATE','result replay idempotent');
check(await scalar('select count(*)::int v from public.test_contact_logs'),1,'one sales contact only');
check((await action('alice','list_jobs')).rows[0].visit_confirmed_at,null,'not falsely confirmed');
check((await action('alice','confirm_visit',{job_id:id,reason:'Expert verified available time'})).ok,true,'staff confirms schedule');
check((await worker('status',{job_id:id,provider_call_id:sid,status:'completed',sequence_number:3,duration_seconds:80})).ok,true,'signed completion');
check((await worker('status',{job_id:id,provider_call_id:sid,status:'ringing',sequence_number:1})).ok,true,'out of order callback benign');
check((await action('alice','list_jobs')).rows[0].status,'completed','terminal never regressed');
check((await worker('result',{job_id:id,provider_call_id:sid,outcome:'do_not_call',customer_confirmed:true})).ok,true,'late withdrawal supersedes visit');
check((await action('alice','list_jobs')).rows.find(x=>x.id===id).visit_confirmed_at,null,'withdrawal clears current booking confirmation without deleting history');
check((await action('alice','candidates')).rows.find(x=>x.company_uid===uidA).eligible,false,'withdrawn number suppressed');
check((await action('admin','grant_permission',{...consent,company_uids:[uidA]})).code,'DO_NOT_CALL','batch grant cannot reactivate opted out');
const second=await action('alice','enqueue',{company_uids:[uidC],request_id:'test-request-c'});
await action('admin','approve',{job_id:second.rows[0].id});
await db.exec("update public.oasis_company_sales_assignments set assigned_user_id='bob' where company_uid='business:0000000003'");
check((await worker('claim')).code,'TARGET_CHANGED','changed assignment blocks before dialing');
check((await action('alice','list_jobs')).rows.some(x=>x.company_uid===uidC),false,'old owner loses detail after transfer');
await worker('bridge_error',{job_id:id,provider_call_id:sid,error_code:'DO_NOT_CALL_SAVE_FAILED'});
await worker('status',{job_id:id,provider_call_id:sid,status:'completed',sequence_number:4});
check((await action('admin','list_jobs')).rows.find(x=>x.id===id).status,'unknown','optout persistence fault quarantined despite callback');
check((await worker('claim')).code,'BUSY','quarantine stops new calls');
check((await action('alice','reconcile',{job_id:id,resolution:'confirmed_ended',reason:'test provider evidence'})).code,'NOT_AUTHORIZED','only admin reconciles');
check((await action('admin','reconcile',{job_id:id,resolution:'confirmed_ended',reason:'test provider evidence'})).ok,true,'human resolution releases global hold');
check((await action('alice','candidates')).rows.find(x=>x.company_uid===uidA).do_not_call,true,'manual resolution never unsuppresses');
await db.close();
console.log(`PASS ${checks} isolated PostgreSQL checks; exact migration applied twice; no production connection.`);
