// Offline v9.14.4 filter/count RPC contracts. Generated synthetic fixtures only.
// PGlite serializes statements: Promise.all tests replay safety, not PostgreSQL
// multi-connection lock scheduling. Hosted contention remains a staging check.
import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
process.on('uncaughtException',e=>{console.error('SIMPLE CAMPAIGN TEST FAILED:',e.message,e.code||'',e.position||'',e.where||'');process.exit(1);});
const {PGlite}=await import(process.env.PGLITE_PACKAGE||'@electric-sql/pglite');
const db=new PGlite(); let checks=0;
const check=(a,b,label)=>{assert.deepEqual(a,b,label);checks++;};
const scalar=async(sql,params=[])=>(await db.query(sql,params)).rows[0].v;
const call=async(actor,payload)=>(await db.query('select public.oasis_voice_filtered_campaign($1,$2::jsonb) v',[actor,JSON.stringify(payload)])).rows[0].v;
const action=async(name,payload)=>(await db.query('select public.oasis_voice_action($1,$2,$3::jsonb) v',['admin',name,JSON.stringify(payload)])).rows[0].v;
const request=(key,filters={},count=1)=>({request_id:key,filters,requested_count:count,approval_confirmed:true});
const prior=await readFile(new URL('./voice_calling_campaigns.integration.mjs',import.meta.url),'utf8');
const fixtureStart=prior.indexOf('await db.exec(`')+'await db.exec(`'.length;
const fixtureEnd=prior.indexOf('\n`);',fixtureStart);
assert(fixtureStart>0&&fixtureEnd>fixtureStart);
await db.exec(prior.slice(fixtureStart,fixtureEnd));
await db.exec(`
 alter table public.oasis_prospect_companies add column business_no text,add column source text,add column source_key text,
  add column region text,add column industry_name text,add column employee_count integer;
 create function public.oasis_is_valid_company_uid(v text) returns boolean language sql immutable as $$ select coalesce(v ~ '^(business:[0-9]{10}|corporate:[0-9]{13}|nps:[0-9A-Z]+|fallback:[0-9a-f]{64}|source:[0-9a-f]{64})$',false) $$;
 create table public.oasis_employment_contacts(
  contact_key text primary key,source_type text,source_record_key text,business_no text,company_name text,address text,
  province text,district text,province_code text,industry_name text,industry_category text,
  current_employee_count integer default 0,employee_growth integer default 0,previous_period text default '',current_period text default '',
  is_new_company boolean default false,opening_signal_basis text default '',mobile_phone text default '',landline_phone text default '',
  has_mobile_phone boolean default false,has_landline_phone boolean default false,phone_status text default 'pending',
  phone_provider_stage text default 'kakao',phone_attempt_count integer default 0,updated_at timestamptz default now());
 alter table public.oasis_employment_contacts enable row level security;
 grant select on public.oasis_employment_contacts to service_role;
 create index test_prospect_uid on public.oasis_prospect_companies(company_uid);
 create index test_assignment_uid on public.oasis_company_sales_assignments(company_uid);
 create index test_contact_prospect on public.oasis_prospect_contacts(prospect_id);
`);
const migrationNames=['20261001235500_ai_visit_calling.sql','20261003010404_ai_visit_calling_clawops_provider.sql',
 '20261006081850_ai_visit_calling_bulk_campaigns.sql','20261006093201_ai_visit_calling_admin_catalog.sql'];
for(const f of migrationNames) await db.exec((await readFile(new URL('../supabase/migrations/'+f,import.meta.url),'utf8')).replace(/\bnow\(\)/g,'public.voice_test_now()'));
const migration=await readFile(new URL('../supabase/migrations/20261007051303_ai_visit_calling_simple_campaigns.sql',import.meta.url),'utf8');
const workerBefore=await scalar("select pg_get_functiondef('public.oasis_voice_worker(text,jsonb)'::regprocedure) v");
const actionBefore=await scalar("select pg_get_functiondef('public.oasis_voice_action(text,text,jsonb)'::regprocedure) v");
const campaignBefore=await scalar("select pg_get_functiondef('public.oasis_voice_campaign_action(text,text,jsonb)'::regprocedure) v");
await db.exec(migration.replace(/\bnow\(\)/g,'public.voice_test_now()'));
await db.exec(migration.replace(/\bnow\(\)/g,'public.voice_test_now()'));
check(await scalar("select pg_get_functiondef('public.oasis_voice_worker(text,jsonb)'::regprocedure) v"),workerBefore,'worker unchanged');
check(await scalar("select pg_get_functiondef('public.oasis_voice_action(text,text,jsonb)'::regprocedure) v"),actionBefore,'legacy action unchanged');
check(await scalar("select pg_get_functiondef('public.oasis_voice_campaign_action(text,text,jsonb)'::regprocedure) v"),campaignBefore,'legacy campaigns unchanged');
for(const role of ['anon','authenticated']) {
 check(await scalar(`select has_function_privilege('${role}','public.oasis_voice_filtered_campaign(text,jsonb)','execute') v`),false,'browser cannot forge actor');
 check(await scalar(`select has_table_privilege('${role}','public.oasis_voice_campaigns','select') v`),false,'no browser table access');
}
check(await scalar("select prosecdef v from pg_proc where oid='public.oasis_voice_filtered_campaign(text,jsonb)'::regprocedure"),false,'invoker only');
check(await scalar("select proconfig @> ARRAY['search_path=\"\"'] v from pg_proc where oid='public.oasis_voice_filtered_campaign(text,jsonb)'::regprocedure"),true,'empty search path');
check(await scalar("select relrowsecurity v from pg_class where relname='oasis_voice_campaigns'"),true,'campaign RLS retained');
check(migration.includes("pg_advisory_xact_lock(pg_catalog.hashtextextended('oasis-voice-queue',0))"),true,'same queue transaction lock');
const withManual=process.env.OASIS_TEST_WITH_MANUAL==='1';
if(withManual) {
 const manual=await readFile(new URL('../supabase/migrations/20261007051446_ai_visit_calling_manual_targets.sql',import.meta.url),'utf8');
 assert(manual.includes('oasis_voice_manual_call'),'manual migration must be complete before cumulative test');
 await db.exec(manual.replace(/\bnow\(\)/g,'public.voice_test_now()'));
 await db.exec(manual.replace(/\bnow\(\)/g,'public.voice_test_now()'));
}

// Helpers insert generated fixtures into this isolated in-memory DB, never live DB.
async function seed(start,end,group='Bulk',consent=true) {
 await db.query(`insert into public.oasis_prospect_companies(id,company_uid,company_name,address,owner_user_id,business_no,source,source_key,region,industry_name)
 select md5('prospect'||i)::uuid,'business:12381'||lpad(i::text,5,'0'),'Synthetic company '||i,'Synthetic region address '||i,
  case when i%2=0 then 'alice' else 'bob' end,'12381'||lpad(i::text,5,'0'),'synthetic_source',i::text,'Synthetic region',$3 from generate_series($1::int,$2::int) i`,[start,end,group]);
 await db.query(`insert into public.oasis_company_sales_assignments(id,company_uid,company_id,assigned_user_id)
 select p.id,p.company_uid,p.id,p.owner_user_id from public.oasis_prospect_companies p
 where p.id in(select md5('prospect'||i)::uuid from generate_series($1::int,$2::int) i)`,[start,end]);
 await db.query(`insert into public.oasis_prospect_contacts(id,prospect_id,contact_type,contact_value)
 select md5('contact'||i)::uuid,md5('prospect'||i)::uuid,'phone','010'||lpad(i::text,8,'0') from generate_series($1::int,$2::int) i`,[start,end]);
 await db.query(`insert into public.oasis_employment_contacts(contact_key,source_type,source_record_key,business_no,company_name,address,province,province_code,industry_name,current_period,employee_growth)
 select 'business:12381'||lpad(i::text,5,'0'),'synthetic_source',i::text,'12381'||lpad(i::text,5,'0'),'Synthetic company '||i,
  'Synthetic region address '||i,'Synthetic region','ZZ',$3,'2029',2 from generate_series($1::int,$2::int) i`,[start,end,group]);
 if(consent) await permit(start,end);
}
async function permit(start,end) {
 await db.query(`insert into public.oasis_voice_permissions(company_uid,phone_e164,kind,evidence_ref,granted_at,expires_at,created_by)
 select p.company_uid,public.oasis_voice_phone(c.contact_value),'explicit_consent','synthetic consent fixture',public.voice_test_now()-interval '1 day',public.voice_test_now()+interval '1 month','admin'
 from public.oasis_prospect_companies p join public.oasis_prospect_contacts c on c.prospect_id=p.id
 where p.id in(select md5('prospect'||i)::uuid from generate_series($1::int,$2::int) i)`,[start,end]);
}
await seed(1,1000,'Bulk',false);
const sourceBefore=await scalar('select md5(jsonb_agg(to_jsonb(e) order by contact_key)::text) v from public.oasis_employment_contacts e');
const crmBefore=await scalar('select md5(jsonb_agg(to_jsonb(e) order by id)::text) v from public.oasis_prospect_companies e');
const contactBefore=await scalar('select md5(jsonb_agg(to_jsonb(e) order by id)::text) v from public.oasis_prospect_contacts e');
await db.exec('set role service_role');
for(const actor of ['alice','bob','suspended','missing','']) check((await call(actor,request('unauthorized-test'))).code,'NOT_AUTHORIZED','only approved administrator');
for(const bad of [null,[],{},request('short'),{...request('bad-approval'),approval_confirmed:false},
 {...request('bad-approval-string'),approval_confirmed:'true'},request('too-many-count',{},1001),request('zero-count',{},0),request('negative-count',{},-1),
 request('float-count',{},1.5),{...request('string-count'),requested_count:'1000'},request('bad-filter-phone',{phone_type:'both'}),
 request('bad-filter-business',{business_type:'x'}),request('bad-filter-discovery',{discovery_type:'x'}),request('unexpected-filter',{query:'x'}),
 request('wrong-filter-type',{region:{name:'x'}}),{...request('unexpected-actor'),actor:'admin'}])
 check((await call('admin',bad)).code,'INVALID_INPUT','strict payload validation');
let result=await call('admin',request('no-consent-yet',{industry:'Bulk'},1000));
check(result.ok,false,'zero targets not success'); check(result.code,'NO_ELIGIBLE_TARGETS','no eligible code');
check(result.excluded_counts.CONSENT_REQUIRED,1000,'exact no-consent exclusions');
check(await scalar('select count(*)::int v from public.oasis_voice_campaigns'),0,'no empty campaign');
check(await scalar('select count(*)::int v from public.oasis_voice_permissions'),0,'never manufactures consent');
await db.exec('reset role'); await permit(1,1000); await db.exec('set role service_role');
const start=performance.now();
result=await call(' ADMIN ',request('bulk-thousand',{business_type:' CORPORATE ',region:' Synthetic   region ',industry:' BULK ',phone_type:' MOBILE ',discovery_type:' EMPLOYMENT_GROWTH '},1000));
const elapsedMs=Math.round(performance.now()-start);
check(result.ok,true,'one click queues 1000 eligible'); check(result.created_count,1000,'exact 1000 job budget');
check(result.eligible_count,1000,'exact eligibility');check(result.shortage_count,0,'no shortage');check(result.status,'running','campaign running atomically');
const cid=result.campaign_id;
check(await scalar('select count(*)::int v from public.oasis_voice_jobs where campaign_id=$1 and status=$2',[cid,'approved']),1000,'every selected job approved and durable');
check(await scalar('select count(distinct phone_e164)::int v from public.oasis_voice_jobs where campaign_id=$1',[cid]),1000,'distinct normalized phone budget');
check(await scalar('select count(distinct company_uid)::int v from public.oasis_voice_jobs where campaign_id=$1',[cid]),1000,'distinct company budget');
check(await scalar('select selection_filters v from public.oasis_voice_campaigns where id=$1',[cid]),{business_type:'corporate',region:'synthetic region',industry:'bulk',phone_type:'mobile',discovery_type:'employment_growth'},'canonical persisted filters');
check(elapsedMs<30000,true,'isolated 1000 target enqueue fits 30s budget');
const eventCount=await scalar('select count(*)::int v from public.oasis_voice_events');
const replay=await call('admin',request('bulk-thousand',{business_type:'corporate',region:'synthetic region',industry:'bulk',phone_type:'mobile',discovery_type:'employment_growth'},1000));
check(replay.replayed,true,'canonical equivalent request replays');check(replay.campaign_id,cid,'idempotent campaign');
check(await scalar('select count(*)::int v from public.oasis_voice_events'),eventCount,'replay adds no duplicate audit');
await db.query("update public.oasis_voice_campaigns set status='paused' where id=$1",[cid]);
check((await call('admin',request('bulk-thousand',{business_type:'corporate',region:'synthetic region',industry:'bulk',phone_type:'mobile',discovery_type:'employment_growth'},1000))).status,'paused','replay never resumes paused campaign');
check((await call('admin',request('bulk-thousand',{industry:'Bulk'},999))).code,'IDEMPOTENCY_CONFLICT','same request key cannot change count/filters');
check((await call('admin',request('another-thousand',{industry:'Bulk'},1000))).code,'NO_ELIGIBLE_TARGETS','another request cannot duplicate active jobs');
check(await scalar('select count(*)::int v from public.oasis_voice_jobs'),1000,'no hidden duplicate queue growth');
check(await scalar('select md5(jsonb_agg(to_jsonb(e) order by contact_key)::text) v from public.oasis_employment_contacts e'),sourceBefore,'collector rows exactly unchanged');
check(await scalar('select md5(jsonb_agg(to_jsonb(e) order by id)::text) v from public.oasis_prospect_companies e'),crmBefore,'prospects exactly unchanged');
check(await scalar('select md5(jsonb_agg(to_jsonb(e) order by id)::text) v from public.oasis_prospect_contacts e'),contactBefore,'contact sources exactly unchanged');
const legacy=await db.query('select public.oasis_voice_campaign_action($1,$2,$3::jsonb) v',['admin','create_campaign',JSON.stringify({name:'Synthetic legacy',request_id:'legacy-over-cap',company_uids:Array(101).fill('business:1238100001')})]);
check(legacy.rows[0].v.code,'INVALID_INPUT','legacy 100 company cap unchanged');
await db.exec('reset role');
// Dedup BEFORE limit; two companies sharing a phone must not under-fill count 2.
await seed(1101,1103,'Dedup');
await db.exec(`update public.oasis_prospect_contacts set contact_value='010'||lpad('1101',8,'0') where id=md5('contact1102')::uuid;
 update public.oasis_voice_permissions set phone_e164='+8210'||lpad('1101',8,'0') where company_uid='business:12381'||lpad('1102',5,'0');`);
await db.exec('set role service_role');result=await call('admin',request('dedup-request',{industry:'Dedup'},2));
check(result.created_count,2,'phone dedup performed before requested limit');check(result.eligible_count,2,'phone duplicate not counted eligible');check(result.excluded_counts.DUPLICATE_PHONE,1,'duplicate-phone exclusion count');
check(await scalar('select jsonb_agg(company_uid order by company_uid) v from public.oasis_voice_jobs where campaign_id=$1',[result.campaign_id]),['business:1238101101','business:1238101103'],'stable UID ordering');
await db.exec('reset role');
// Source has a mobile, but the current primary actual target is a landline.
await seed(1201,1201,'Actual phone');
await db.exec(`update public.oasis_prospect_contacts set contact_value='02'||lpad('1201',8,'0') where id=md5('contact1201')::uuid;
 update public.oasis_voice_permissions set phone_e164='+822'||lpad('1201',8,'0') where company_uid='business:12381'||lpad('1201',5,'0');
 update public.oasis_employment_contacts set mobile_phone='010'||lpad('1201',8,'0'),has_mobile_phone=true where source_record_key='1201';`);
await db.exec('set role service_role');
check((await call('admin',request('actual-mobile',{industry:'Actual phone',phone_type:'mobile'}))).code,'NO_ELIGIBLE_TARGETS','mobile availability cannot override actual landline');
result=await call('admin',request('actual-landline',{industry:'Actual phone',phone_type:'landline'},10));
check(result.created_count,1,'actual landline selectable');check(result.shortage_count,9,'explicit shortage');
check(await scalar('select bool_and(phone_e164 like $2) v from public.oasis_voice_jobs where campaign_id=$1',[result.campaign_id,'+822%']),true,'approved number is selected phone type');
await db.exec('reset role');
// DNC, revoked/expired/wrong-number permission, strong UID conflict and opt-outs.
await seed(1301,1308,'Safety');
await db.exec(`
 insert into public.oasis_voice_suppressions(company_uid,phone_e164,reason,created_by) select company_uid,phone_e164,'do_not_call','admin' from public.oasis_voice_permissions where company_uid='business:12381'||lpad('1301',5,'0');
 update public.oasis_voice_permissions set revoked_at=public.voice_test_now() where company_uid='business:12381'||lpad('1302',5,'0');
 update public.oasis_voice_permissions set granted_at=public.voice_test_now()-interval '2 days',expires_at=public.voice_test_now()-interval '1 day' where company_uid='business:12381'||lpad('1303',5,'0');
 update public.oasis_voice_permissions set phone_e164='+8210'||lpad('9999',8,'0') where company_uid='business:12381'||lpad('1304',5,'0');
 update public.oasis_prospect_companies set business_no='12381'||lpad('9900',5,'0') where id=md5('prospect1305')::uuid;
 update public.oasis_prospect_contacts set do_not_contact=true where id=md5('contact1306')::uuid;
 insert into public.oasis_company_kakao_contact_controls values('business:12381'||lpad('1307',5,'0'),'opted_out');
`);
await db.exec('set role service_role');result=await call('admin',request('safety-request',{industry:'Safety'},1000));
check(result.created_count,1,'only safe candidate queued');check(result.shortage_count,999,'no artificial count fulfillment');
check(result.excluded_counts.DO_NOT_CALL,3,'DNC cross-source checks');check(result.excluded_counts.CONSENT_REQUIRED,3,'number/date-bound consent');check(result.excluded_counts.IDENTITY_CONFLICT,1,'strong identity conflict denied');
await db.exec('reset role');
// Expected source key collision cannot override strong business identity.
await seed(1401,1402,'Identity');
await db.exec(`update public.oasis_prospect_companies set source_data=jsonb_build_object('contact_key','business:12381'||lpad('1402',5,'0')) where id=md5('prospect1401')::uuid;
 update public.oasis_company_sales_assignments set released_at=public.voice_test_now() where company_uid='business:12381'||lpad('1402',5,'0');`);
await db.exec('set role service_role');result=await call('admin',request('identity-conflict',{industry:'Identity'},2));
check(result.code,'NO_ELIGIBLE_TARGETS','conflicting explicit source key fails closed');check(result.excluded_counts.IDENTITY_CONFLICT,1,'source conflict accurately counted');
await db.exec('reset role');
await seed(1501,1503,'Atomic');
await db.exec(`create function public.synthetic_approval_fault() returns trigger language plpgsql as $$ begin
 if new.status='approved' and new.company_uid='business:12381'||lpad('1502',5,'0') then raise unique_violation using message='SYNTHETIC_APPROVAL_FAULT'; end if; return new; end $$;
 create trigger synthetic_approval_fault before update on public.oasis_voice_jobs for each row execute function public.synthetic_approval_fault();`);
const jobsBefore=await scalar('select count(*)::int v from public.oasis_voice_jobs');const eventsBefore=await scalar('select count(*)::int v from public.oasis_voice_events');
await db.exec('set role service_role');result=await call('admin',request('atomic-retry',{industry:'Atomic'},3));
check(result.code,'CAMPAIGN_CREATE_FAILED','unexpected mutation failure reported');
check(await scalar('select count(*)::int v from public.oasis_voice_jobs'),jobsBefore,'entire batch rolled back');
check(await scalar('select count(*)::int v from public.oasis_voice_events'),eventsBefore,'entire audit transaction rolled back');
check(await scalar("select count(*)::int v from public.oasis_voice_campaigns where request_id='atomic-retry'"),0,'no partial campaign');
await db.exec('reset role; drop trigger synthetic_approval_fault on public.oasis_voice_jobs; drop function public.synthetic_approval_fault(); set role service_role;');
check((await call('admin',request('atomic-retry',{industry:'Atomic'},3))).created_count,3,'same key safe after rollback');
await db.exec('reset role'); await seed(1601,1605,'Parallel');await db.exec('set role service_role');
const together=await Promise.all([call('admin',request('parallel-same',{industry:'Parallel'},5)),call('admin',request('parallel-same',{industry:'Parallel'},5))]);
check(together[0].campaign_id,together[1].campaign_id,'overlapping client submissions share campaign');
check(together.filter(x=>x.replayed).length,1,'second invocation replays');
check(await scalar('select count(*)::int v from public.oasis_voice_jobs where campaign_id=$1',[together[0].campaign_id]),5,'no duplicate jobs from replay');
await db.exec('reset role'); await seed(1701,1701,'Role');
await db.exec("update public.oasis_users set role='member' where user_id='admin'; set role service_role;");
check((await call('admin',request('role-demoted',{industry:'Role'}))).code,'NOT_AUTHORIZED','demotion checked live');
await db.exec("reset role; update public.oasis_users set role='admin',status='suspended' where user_id='admin'; set role service_role;");
check((await call('admin',request('role-suspended',{industry:'Role'}))).code,'NOT_AUTHORIZED','suspended admin checked live');
await db.exec("reset role; update public.oasis_users set status='approved' where user_id='admin';");
// Recent calls, unresolved dispatch, and agreed follow-ups are separate blockers.
await seed(1801,1804,'Cooldown');
await db.exec('set role service_role');
for(const n of [1801,1802,1803]) {
 const uid='business:12381'+String(n).padStart(5,'0');
 const queued=await action('enqueue',{request_id:'synthetic-cooldown-'+n,company_uids:[uid]});
 check(queued.ok,true,'cooldown fixture enqueue');
 if(n===1801) await db.query("update public.oasis_voice_jobs set status='completed',dispatch_at=public.voice_test_now()-interval '6 days' where id=$1",[queued.rows[0].id]);
 if(n===1802) await db.query("update public.oasis_voice_jobs set status='unknown',dispatch_at=public.voice_test_now()-interval '30 days' where id=$1",[queued.rows[0].id]);
 if(n===1803) await db.query("update public.oasis_voice_jobs set status='completed',outcome='callback_requested',dispatch_at=public.voice_test_now()-interval '30 days' where id=$1",[queued.rows[0].id]);
}
result=await call('admin',request('cooldown-filtered',{industry:'Cooldown'},4));
check(result.created_count,1,'recent, unknown and follow-up jobs never retried');check(result.excluded_counts.DUPLICATE,3,'all duplicate classes counted');
await db.exec('reset role');
// Raw-only companies are never silently assigned or given permission.
await db.exec(`insert into public.oasis_employment_contacts(contact_key,source_type,business_no,company_name,industry_name)
 values('business:12381'||lpad('9999',5,'0'),'synthetic_source','12381'||lpad('9999',5,'0'),'Synthetic raw only','Raw only');`);
const pCount=await scalar('select count(*)::int v from public.oasis_prospect_companies');
const aCount=await scalar('select count(*)::int v from public.oasis_company_sales_assignments');
await db.exec('set role service_role');result=await call('admin',request('raw-only-request',{industry:'Raw only'},1000));
check(result.code,'NO_ELIGIBLE_TARGETS','raw sources require explicit CRM linkage and consent');
check(await scalar('select count(*)::int v from public.oasis_prospect_companies'),pCount,'no implicit prospect creation');
check(await scalar('select count(*)::int v from public.oasis_company_sales_assignments'),aCount,'no implicit assignment');
await db.exec('reset role');
// Wildcards are literal and SQL-looking filter text is data, never SQL.
await seed(1901,1901,'Literal%_industry');
await db.exec('set role service_role');
check((await call('admin',request('literal-percent',{industry:'%_'}))).created_count,1,'literal wildcard matching');
check((await call('admin',request('sql-looking-filter',{industry:"'; drop table oasis_voice_jobs; --"}))).code,'NO_ELIGIBLE_TARGETS','SQL-looking filter never executed');
check(await scalar("select to_regclass('public.oasis_voice_jobs') is not null v"),true,'jobs preserved');
await db.exec('reset role');
await db.close();
console.log(JSON.stringify({result:'PASS',checks,with_manual_migration:withManual,thousand_target_enqueue_ms:elapsedMs,network_calls:0,real_phone_calls:0,concurrency_note:'PGlite serial transaction/replay test; hosted contention not tested'}));
