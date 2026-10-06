// Offline campaign contract tests; synthetic data only. No network or paid services.
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
process.on('uncaughtException', error => {
 console.error('ISOLATED CAMPAIGN TEST FAILED:', error.message, error.code || '', error.position || '', error.where || '');
 process.exit(1);
});
const { PGlite } = await import(process.env.PGLITE_PACKAGE || '@electric-sql/pglite');
const db = new PGlite();
const migration = await readFile(new URL('../supabase/migrations/20261001235500_ai_visit_calling.sql', import.meta.url),'utf8');
const carrierMigration = await readFile(new URL('../supabase/migrations/20261003010404_ai_visit_calling_clawops_provider.sql', import.meta.url),'utf8');
const campaignMigration = await readFile(new URL('../supabase/migrations/20261006081850_ai_visit_calling_bulk_campaigns.sql', import.meta.url),'utf8');
let checks=0;
function check(actual,expected,label) { assert.deepEqual(actual,expected,label); checks++; }
async function scalar(sql) { return (await db.query(sql)).rows[0].v; }
async function action(actor,name,payload={}) { return (await db.query('select public.oasis_voice_action($1,$2,$3::jsonb) v',[actor,name,JSON.stringify(payload)])).rows[0].v; }
async function campaign(actor,name,payload={}) { return (await db.query('select public.oasis_voice_campaign_action($1,$2,$3::jsonb) v',[actor,name,JSON.stringify(payload)])).rows[0].v; }
async function worker(name,payload={}) { return (await db.query('select public.oasis_voice_worker($1,$2::jsonb) v',[name,JSON.stringify(payload)])).rows[0].v; }
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
await db.exec(carrierMigration);
const beforeSources=await scalar('select jsonb_agg(to_jsonb(p) order by id) v from public.oasis_prospect_companies p');
const beforeContacts=await scalar('select jsonb_agg(to_jsonb(p) order by id) v from public.oasis_prospect_contacts p');
await db.exec(campaignMigration);
await db.exec(campaignMigration);
check(await scalar('select jsonb_agg(to_jsonb(p) order by id) v from public.oasis_prospect_companies p'),beforeSources,'migration twice preserves source data');
check(await scalar('select jsonb_agg(to_jsonb(p) order by id) v from public.oasis_prospect_contacts p'),beforeContacts,'migration twice preserves contacts');
check(await scalar("select relrowsecurity v from pg_class where relname='oasis_voice_campaigns'"),true,'campaigns RLS');
for (const role of ['anon','authenticated']) {
 check(await scalar(`select has_function_privilege('${role}','public.oasis_voice_campaign_action(text,text,jsonb)','execute') v`),false,'browser cannot forge campaign actor');
 check(await scalar(`select has_table_privilege('${role}','public.oasis_voice_campaigns','select') v`),false,'browser cannot read campaigns directly');
}
check(await scalar("select has_table_privilege('service_role','public.oasis_voice_campaigns','delete') v"),false,'no campaign hard-delete grant');
check(await scalar("select prosecdef v from pg_proc where oid='public.oasis_voice_campaign_action(text,text,jsonb)'::regprocedure"),false,'campaign invoker');
check(await scalar("select proconfig @> ARRAY['search_path=\"\"'] v from pg_proc where oid='public.oasis_voice_campaign_action(text,text,jsonb)'::regprocedure"),true,'empty search path');
for (const sql of [migration,carrierMigration,campaignMigration]) await db.exec(sql.replace(/\bnow\(\)/g,'public.voice_test_now()'));
await db.exec('set role service_role');
const a='business:0000000001', b='business:0000000002', c='business:0000000003';
const consent={kind:'explicit_consent',evidence_ref:'synthetic-explicit-consent',granted_at:'2030-01-06T00:00:00Z',expires_at:'2030-02-01T00:00:00Z'};
check((await campaign('suspended','campaign_stats')).code,'NOT_AUTHORIZED','inactive stats denied');
check((await campaign('alice','create_campaign',{name:'Empty',request_id:'empty-test',company_uids:[]})).code,'INVALID_INPUT','empty request denied');
check((await campaign('alice','create_campaign',{name:'Bad',request_id:'bad-test',company_uids:[null]})).code,'INVALID_INPUT','null uid denied');
check((await campaign('alice','create_campaign',{name:'Too many',request_id:'too-many',company_uids:Array(31).fill(a)})).code,'INVALID_INPUT','member selection max 30');
check((await campaign('admin','create_campaign',{name:'Too many',request_id:'too-many',company_uids:Array(101).fill(a)})).code,'INVALID_INPUT','admin selection max 100');
check((await action('alice','grant_permission',{...consent,company_uids:[a]})).ok,true,'explicit permission fixture');
const created=await campaign('alice','create_campaign',{name:'First campaign',request_id:'campaign-create-a',company_uids:[a,b,c,a]});
check(created.ok,true,'partial create succeeds');
check(created.created_count,1,'one valid job');
check(created.skipped_count,2,'unauthorized and missing permission safely skipped');
check(created.skipped.map(x=>x.code).sort(),['CONSENT_REQUIRED','NOT_AUTHORIZED'],'safe exclusion reasons');
const cid=created.campaign_id;
const replay=await campaign('alice','create_campaign',{name:'Different selection',request_id:'campaign-create-a',company_uids:[c]});
check(replay.campaign_id,cid,'creation retry keeps same campaign');
check(replay.created_count,1,'creation retry returns original result');
check(await scalar('select count(*)::int v from public.oasis_voice_jobs'),1,'replay never queues additional selection');
const rows=(await campaign('alice','campaign_jobs',{campaign_id:cid})).rows;
check(rows.length,1,'owner campaign jobs');
check(rows[0].campaign_id,cid,'campaign association');
check(rows[0].duration_seconds,null,'duration shaped');
check(Object.hasOwn(rows[0],'phone_e164'),false,'raw phone not exposed');
check((await campaign('bob','campaign_jobs',{campaign_id:cid})).code,'NOT_AUTHORIZED','other owner cannot view campaign');
check((await campaign('bob','list_campaigns')).rows.length,0,'other owner campaign list hidden');
check((await campaign('alice','legacy_jobs')).rows.length,0,'campaign jobs absent from legacy controls');
check((await campaign('alice','campaign_stats')).metrics.queued,1,'exact scoped stats');
check((await campaign('bob','campaign_stats')).metrics.total,0,'other owner stats excludes private jobs');
check((await campaign('alice','start_campaign',{campaign_id:cid})).code,'NOT_AUTHORIZED','member cannot approve bulk dialing');
const id=rows[0].id;
// Even a stale old UI's per-job approval cannot bypass campaign state.
check((await action('admin','approve',{job_id:id})).ok,true,'legacy admin approval still backward compatible');
check((await worker('claim',{provider:'clawops'})).code,'EMPTY','draft campaign not dialed despite individual approval');
check((await campaign('admin','start_campaign',{campaign_id:cid})).approved_count,1,'admin bulk approve');
check((await campaign('alice','pause_campaign',{campaign_id:cid})).ok,true,'creator may pause own campaign');
check((await worker('claim',{provider:'clawops'})).code,'EMPTY','paused approvals not dialed');
check((await campaign('admin','start_campaign',{campaign_id:cid})).approved_count,1,'resume approved only');
const claimed=await worker('claim',{provider:'clawops'});
check(claimed.job.id,id,'running campaign claim');
check((await worker('claim',{provider:'clawops'})).code,'BUSY','global single call guard');
await campaign('admin','pause_campaign',{campaign_id:cid});
check((await worker('get_job',{job_id:id,provider:'clawops'})).code,'CAMPAIGN_NOT_RUNNING','pause rechecked before external call');
check((await worker('connect',{job_id:id,provider_call_id:'clawops:CAcampaign_12345678',dispatch_nonce:claimed.job.dispatch_nonce})).code,'CAMPAIGN_NOT_RUNNING','pause rechecked before media');
await campaign('admin','start_campaign',{campaign_id:cid});
check((await worker('connect',{job_id:id,provider_call_id:'clawops:CAcampaign_12345678',dispatch_nonce:claimed.job.dispatch_nonce})).ok,true,'resumed in-flight claim connects');
await campaign('admin','pause_campaign',{campaign_id:cid});
check((await worker('get_job',{job_id:id,provider_call_id:'clawops:CAcampaign_12345678'})).ok,true,'pause does not interrupt connected call');
check((await campaign('admin','cancel_campaign',{campaign_id:cid})).cancelled_count,0,'cancel never rewrites active call');
check((await worker('result',{job_id:id,provider_call_id:'clawops:CAcampaign_12345678',outcome:'not_representative'})).ok,true,'active outcome persists after campaign cancel');
await worker('status',{job_id:id,provider_call_id:'clawops:CAcampaign_12345678',status:'completed',sequence_number:1,duration_seconds:65});
check((await campaign('alice','campaign_jobs',{campaign_id:cid})).rows[0].duration_seconds,65,'actual call duration');
check((await campaign('admin','start_campaign',{campaign_id:cid})).code,'INVALID_STATE','cancelled campaign cannot auto retry');
check((await campaign('alice','campaign_stats')).metrics.completed,1,'completed aggregate');
// Ownership moves revoke job detail and aggregate access immediately.
await db.exec(`update public.oasis_company_sales_assignments set assigned_user_id='bob' where company_uid='${a}'`);
check((await campaign('alice','campaign_jobs',{campaign_id:cid})).rows.length,0,'former owner loses details');
check((await campaign('alice','campaign_stats')).metrics.total,0,'former owner loses metrics');
check((await campaign('alice','list_campaigns')).rows.find(x=>x.id===cid).total,0,'former owner campaign metadata does not leak moved job counts');
check((await campaign('alice','cancel_campaign',{campaign_id:cid})).code,'NOT_AUTHORIZED','former owner cannot mutate transferred campaign');
// Permission revoked or contact changed between create and start fails closed.
await action('admin','grant_permission',{...consent,company_uids:[b,c]});
const changed=await campaign('admin','create_campaign',{name:'Changed target',request_id:'changed-target',company_uids:[b,c]});
check(changed.created_count,2,'admin can create for multiple owners');
await db.exec(`update public.oasis_prospect_contacts set contact_value='01000000099' where prospect_id='00000000-0000-4000-8000-000000000002'`);
await db.exec(`update public.oasis_voice_permissions set revoked_at=public.voice_test_now() where company_uid='${c}'`);
const invalidStart=await campaign('admin','start_campaign',{campaign_id:changed.campaign_id});
check(invalidStart.approved_count,0,'invalid start approves none');
check(invalidStart.skipped_count,2,'contact update and revocation detected');
check((await campaign('admin','campaign_jobs',{campaign_id:changed.campaign_id})).rows.every(x=>x.status==='cancelled'),true,'invalid jobs cancelled without deleting');
// Additional fixtures allow independent pause, duplicate, quota and no-retry checks.
await db.exec(`
 insert into public.oasis_prospect_companies(id,company_uid,company_name,address,owner_user_id)
 select gen_random_uuid(),'synthetic:'||i,'Synthetic '||i,'Synthetic address','alice' from generate_series(10,114) i;
 insert into public.oasis_company_sales_assignments(id,company_uid,company_id,assigned_user_id)
 select id,company_uid,id,owner_user_id from public.oasis_prospect_companies where company_uid like 'synthetic:%';
 insert into public.oasis_prospect_contacts(id,prospect_id,contact_type,contact_value)
 select gen_random_uuid(),id,'phone','01000000'||lpad(split_part(company_uid,':',2),3,'0') from public.oasis_prospect_companies where company_uid like 'synthetic:%';
`);
const many=Array.from({length:100},(_,i)=>'synthetic:'+(i+10));
await action('admin','grant_permission',{...consent,company_uids:many});
const hundred=await campaign('admin','create_campaign',{name:'Hundred',request_id:'admin-hundred',company_uids:many});
check(hundred.created_count,100,'admin 100 target selection supported');
check((await campaign('admin','campaign_jobs',{campaign_id:hundred.campaign_id,limit:100})).rows.length,100,'admin page max100');
check((await campaign('alice','campaign_jobs',{campaign_id:hundred.campaign_id,limit:100})).rows.length,100,'read pagination independent of creation cap');
check((await campaign('alice','campaign_jobs',{campaign_id:hundred.campaign_id,limit:30})).has_more,true,'job pagination');
const duplicate=await campaign('alice','create_campaign',{name:'Already queued',request_id:'duplicate-selection',company_uids:[many[0]]});
check(duplicate.created_count,0,'same company cannot be enqueued across campaign owners');
check(duplicate.skipped[0].code,'DUPLICATE','cross campaign duplication reason');
check((await campaign('alice','pause_campaign',{campaign_id:hundred.campaign_id})).code,'NOT_AUTHORIZED','owner cannot pause another creator mixed campaign');
await campaign('admin','start_campaign',{campaign_id:hundred.campaign_id});
await campaign('admin','pause_campaign',{campaign_id:hundred.campaign_id});
const tail='synthetic:110';
await action('admin','grant_permission',{...consent,company_uids:[tail]});
const last=await campaign('admin','create_campaign',{name:'Later running',request_id:'later-running',company_uids:[tail]});
await campaign('admin','start_campaign',{campaign_id:last.campaign_id});
const laterClaim=await worker('claim',{provider:'clawops'});
check(laterClaim.job.company_uid,tail,'earlier paused approvals never block later running campaign');
await worker('mark_unknown',{job_id:laterClaim.job.id,provider:'clawops'});
check((await worker('claim',{provider:'clawops'})).code,'BUSY','unknown outcomes quarantine all campaigns');
check((await campaign('admin','start_campaign',{campaign_id:last.campaign_id})).approved_count,0,'start never retries unknown outcomes');
await action('admin','reconcile',{job_id:laterClaim.job.id,resolution:'confirmed_not_sent',reason:'Synthetic provider verified no call'});
check((await campaign('admin','start_campaign',{campaign_id:last.campaign_id})).approved_count,0,'start never retries failed outcomes');
const preCancel=await scalar('select count(*)::int v from public.oasis_voice_jobs');
check((await campaign('admin','cancel_campaign',{campaign_id:hundred.campaign_id})).cancelled_count,100,'cancel 100 undispatched jobs');
check(await scalar('select count(*)::int v from public.oasis_voice_jobs'),preCancel,'cancel preserves all history rows');
const replayEvents=await scalar('select count(*)::int v from public.oasis_voice_events');
await campaign('admin','create_campaign',{name:'Hundred',request_id:'admin-hundred',company_uids:many});
check(await scalar('select count(*)::int v from public.oasis_voice_events'),replayEvents,'creation retry adds no duplicate audit');
// Global suppression affects campaign creation and live claim, regardless of collector phone changes.
const dnc='synthetic:111';
await action('admin','grant_permission',{...consent,company_uids:[dnc]});
const noCall=await campaign('admin','create_campaign',{name:'Suppression',request_id:'suppression-test',company_uids:[dnc]});
await action('admin','do_not_call',{company_uid:dnc});
check((await campaign('admin','start_campaign',{campaign_id:noCall.campaign_id})).approved_count,0,'DNC cancelled job cannot restart');
check((await campaign('admin','create_campaign',{name:'Suppression again',request_id:'suppression-again',company_uids:[dnc]})).skipped[0].code,'DO_NOT_CALL','campaign cannot override optout');
// Legacy null-campaign jobs still work.
const legacy='synthetic:112';
await action('admin','grant_permission',{...consent,company_uids:[legacy]});
const legacyReply=await action('admin','enqueue',{request_id:'legacy-request',company_uids:[legacy]});
await action('admin','approve',{job_id:legacyReply.rows[0].id});
check((await campaign('admin','legacy_jobs')).rows.length,1,'legacy-only listing');
const parallel=await Promise.all([worker('claim',{provider:'clawops'}),worker('claim',{provider:'clawops'})]);
check(parallel.filter(x=>x.job).length,1,'repeated simultaneous claim requests allocate once');
check(parallel.find(x=>x.job).job.campaign_id,null,'legacy null campaign claim retained');
check((await campaign('admin','list_campaigns',{limit:1})).has_more,true,'campaign pagination');
check(await scalar('select count(*)::int v from public.oasis_prospect_companies'),108,'all synthetic source rows preserved');
// Reapplying the exact production DDL to populated/in-flight state changes no data.
const preservation={};
for (const table of ['oasis_voice_jobs','oasis_voice_permissions','oasis_voice_suppressions','oasis_voice_events','oasis_voice_campaigns']) {
 preservation[table]=await scalar(`select jsonb_agg(to_jsonb(t) order by id) v from public.${table} t`);
}
await db.exec('reset role');
await db.exec(campaignMigration);
await db.exec(campaignMigration);
for (const [table,before] of Object.entries(preservation)) {
 check(await scalar(`select jsonb_agg(to_jsonb(t) order by id) v from public.${table} t`),before,`repeat migration preserves populated ${table}`);
}
console.log(JSON.stringify({ok:true,checks,mode:'isolated-postgres-campaigns',production_calls:0,customer_rows:0}));
await db.close();
