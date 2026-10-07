// Isolated, in-memory PostgreSQL tests. Synthetic fixtures only; no provider I/O.
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
process.on('uncaughtException', error => {
 console.error('MANUAL SQL TEST FAILED:', error.message, error.code || '', error.position || '', error.where || '');
 process.exit(1);
});
const { PGlite } = await import(process.env.PGLITE_PACKAGE || '@electric-sql/pglite');
const db = new PGlite();
let checks=0;
function check(actual,expected,label) { assert.deepEqual(actual,expected,label); checks++; }
async function scalar(sql,params=[]) { return (await db.query(sql,params)).rows[0].v; }
async function rpc(name,args) { return (await db.query(`select public.${name}(${args.map((_,i)=>'$'+(i+1)).join(',')}) v`,args.map(x=>typeof x==='object'?JSON.stringify(x):x))).rows[0].v; }
const manual=(actor,payload)=>rpc('oasis_voice_manual_call',[actor,payload]);
const action=(name,payload={},actor='admin')=>rpc('oasis_voice_action',[actor,name,payload]);
const campaign=(name,payload={})=>rpc('oasis_voice_campaign_action',['admin',name,payload]);
const worker=(name,payload={})=>rpc('oasis_voice_worker',[name,payload]);
async function rejects(sql,params,label) {
 let rejected=false; try { await db.query(sql,params); } catch { rejected=true; }
 check(rejected,true,label);
}
// Reuse the established synthetic CRM fixture without running its older assertions.
const fixtureSource=await readFile(new URL('./voice_calling_campaigns.integration.mjs',import.meta.url),'utf8');
const fixture=fixtureSource.match(/await db\.exec\(`([\s\S]*?)`\);/)[1];
await db.exec(fixture);
// Use production normalization rather than the older fixture's digits-only stub.
const identitySql=await readFile(new URL('../supabase_v1032_company_sales_assignments.sql',import.meta.url),'utf8');
for (const name of ['oasis_sales_digits','oasis_normalize_sales_phone']) {
 let definition=identitySql.match(new RegExp('create or replace function public\\.'+name+'\\([\\s\\S]*?\\$\\$;','i'))[0];
 if(name==='oasis_normalize_sales_phone') definition=definition.replace(/\bp_value\b/g,'v');
 await db.exec(definition);
}
await db.exec(`create or replace function public.voice_test_now() returns timestamptz language sql stable as $$
 select coalesce(nullif(current_setting('voice.test_clock',true),''),'2030-01-07T01:00:00Z')::timestamptz $$;`);
const files=[
 '20261001235500_ai_visit_calling.sql',
 '20261003010404_ai_visit_calling_clawops_provider.sql',
 '20261006081850_ai_visit_calling_bulk_campaigns.sql',
 '20261006093201_ai_visit_calling_admin_catalog.sql',
 '20261007051303_ai_visit_calling_simple_campaigns.sql',
 '20261007051446_ai_visit_calling_manual_targets.sql',
];
let manualSql;
for (const file of files) {
 const sql=(await readFile(new URL('../supabase/migrations/'+file,import.meta.url),'utf8')).replace(/\bnow\(\)/g,'public.voice_test_now()');
 await db.exec(sql);
 if(file===files.at(-1)) manualSql=sql;
}
await db.exec(manualSql);
check(await scalar("select relrowsecurity v from pg_class where relname='oasis_voice_manual_targets'"),true,'manual RLS');
for (const role of ['anon','authenticated']) {
 check(await scalar(`select has_function_privilege('${role}','public.oasis_voice_manual_call(text,jsonb)','execute') v`),false,'browser RPC denied');
 check(await scalar(`select has_table_privilege('${role}','public.oasis_voice_manual_targets','select') v`),false,'browser snapshots denied');
 check(await scalar(`select has_function_privilege('${role}','public.oasis_voice_job_target(uuid)','execute') v`),false,'browser resolver denied');
}
check(await scalar("select has_table_privilege('service_role','public.oasis_voice_manual_targets','delete') v"),false,'no snapshot delete grant');
for (const signature of ['oasis_voice_manual_call(text,jsonb)','oasis_voice_job_target(uuid)','oasis_voice_job_contact_ready(uuid)']) {
 check(await scalar(`select prosecdef v from pg_proc where oid='public.${signature}'::regprocedure`),false,'invoker only');
 check(await scalar(`select proconfig @> ARRAY['search_path=""'] v from pg_proc where oid='public.${signature}'::regprocedure`),true,'empty search path');
}
await db.exec('set role service_role');
let counter=100;
function payload(overrides={}) {
 const number=counter++;
 return {request_id:'manual-synthetic-'+number,company_name:'Synthetic manual '+number,business_type:'corporate',
  phone:'010'+String(number).padStart(8,'0'),purpose:'customer_guidance',
  representative_name:'Synthetic person',region:'Synthetic region',industry:'Synthetic industry',address:'Synthetic visit location',
  consent_confirmed:true,evidence_ref:'synthetic-explicit-consent-evidence',consent_kind:'explicit_consent',
  granted_at:'2030-01-06T00:00:00Z',expires_at:'2030-02-01T00:00:00Z',approval_confirmed:true,...overrides};
}
const sourceBefore=await scalar('select jsonb_agg(to_jsonb(p) order by id) v from public.oasis_prospect_companies p');
const assignmentBefore=await scalar('select jsonb_agg(to_jsonb(a) order by id) v from public.oasis_company_sales_assignments a');
const legacyPermissionCount=await scalar('select count(*)::int v from public.oasis_voice_permissions');
check((await manual('alice',payload())).code,'NOT_AUTHORIZED','member cannot submit manual');
check((await manual('suspended',payload())).code,'NOT_AUTHORIZED','suspended cannot submit manual');
for(const change of [{consent_confirmed:false},{consent_confirmed:'true'}])
 check((await manual('admin',payload(change))).code,'CONSENT_REQUIRED','explicit consent boolean required');
for(const change of [{approval_confirmed:false},{approval_confirmed:null}])
 check((await manual('admin',payload(change))).code,'INVALID_INPUT','explicit approval boolean required');
for(const change of [{target_kind:'manual'},{purpose:'arbitrary'},{purpose:''},{purpose:null},{business_type:'fake'},
 {company_name:''},{company_name:'x'.repeat(161)},{evidence_ref:''},{consent_kind:'public_number'},
 {granted_at:'2031-01-01T00:00:00Z'},{expires_at:'2030-01-01T00:00:00Z'},
 {granted_at:'2030-01-06T00:00:00'},{expires_at:'infinity'},
 {expires_at:'2033-01-01T00:00:00Z'},{consent_kind:'callback_request',expires_at:'2030-04-01T00:00:00Z'},
 {region:{injected:true}},{address:'x'.repeat(301)},{representative_name:'x'.repeat(81)},
 {purpose:'test\nspoof'}]) check((await manual('admin',payload(change))).code,'INVALID_INPUT','invalid manual snapshot rejected');
for(const phone of ['','112','1544','090'+String(1).padStart(8,'0')])
 check((await manual('admin',payload({phone}))).code,'INVALID_INPUT','unsafe destination rejected');
check(await scalar('select count(*)::int v from public.oasis_voice_manual_targets'),0,'invalid requests write nothing');

// Exact phone reverse-linking preserves every existing company-wide opt-out.
const crmPhone='010'+String(1).padStart(8,'0');
for (const status of ['opted_out','admin_blocked']) {
 await db.exec('begin');
 await db.query('insert into public.oasis_company_kakao_contact_controls values($1,$2)',['business:0000000001',status]);
 check((await manual('admin',payload({phone:crmPhone}))).code,'DO_NOT_CALL','manual respects exact-phone linked company '+status);
 await db.exec('rollback');
}
await db.exec('begin');
await db.query('insert into public.oasis_voice_suppressions(company_uid,phone_e164,reason,created_by) values($1,$2,$3,$4)',
 ['business:0000000001','+82'+String(1099999999),'do_not_call','admin']);
check((await manual('admin',payload({phone:crmPhone}))).code,'DO_NOT_CALL','company-wide suppression on another number blocks manual');
await db.exec('rollback');
await db.exec('begin');
await db.query("insert into public.oasis_prospect_contacts(id,prospect_id,contact_type,contact_value,do_not_contact) values(gen_random_uuid(),$1,'phone',$2,true)",
 ['00000000-0000-4000-8000-000000000001','010'+String(888).padStart(8,'0')]);
check((await manual('admin',payload({phone:crmPhone}))).code,'DO_NOT_CALL','another opted-out contact of same company blocks manual');
await db.exec('rollback');
await db.exec('begin');
await db.query('insert into public.oasis_company_kakao_contact_controls values($1,$2)',['business:0000000002','opted_out']);
check(await scalar('select public.oasis_voice_blocked(null,public.oasis_voice_phone($1)) v',[crmPhone]),false,'unrelated company optout does not block another phone');
await db.exec('rollback');

const original=payload();
const created=await manual('admin',original);
check(created.ok,true,'manual call atomically creates approved job');
check(created.created_count,1,'one created job');
check(created.status,'approved','explicit submit approved');
check(created.replayed,false,'first request not replay');
check(Object.hasOwn(created,'phone_e164'),false,'no raw phone response');
const id=created.job_id;
check(await scalar('select company_uid is null and assignment_id is null and prospect_id is null and contact_id is null and permission_id is null and manual_target_id is not null v from public.oasis_voice_jobs where id=$1',[id]),true,'no fake CRM ids');
check(await scalar('select count(*)::int v from public.oasis_voice_permissions'),legacyPermissionCount,'no legacy consent pollution');
check((await manual('admin',original)).job_id,id,'idempotent request returns same job');
check((await manual('admin',original)).replayed,true,'retry clearly labelled');
check((await manual('admin',{...original,phone:'+82'+original.phone.slice(1)})).job_id,id,'E164 normalized by production SQL matches domestic input');
check((await manual('admin',{...original,phone:'0082'+original.phone.slice(1)})).job_id,id,'international prefix preserves idempotent phone');
await db.exec("set timezone='Asia/Seoul'");
check((await manual('admin',original)).job_id,id,'retry fingerprint independent of DB timezone');
await db.exec("set timezone='UTC'");
check((await manual('admin',{...original,company_name:'changed'})).code,'IDEMPOTENCY_CONFLICT','same key changed data blocked');
check((await manual('admin',payload({phone:original.phone}))).code,'DUPLICATE','different request same phone blocked');
check(await scalar('select count(*)::int v from public.oasis_voice_jobs'),1,'no duplicate jobs');
await rejects('update public.oasis_voice_manual_targets set company_name=$1 where id=(select manual_target_id from public.oasis_voice_jobs where id=$2)',['changed',id],'snapshot immutable');
await rejects('update public.oasis_voice_jobs set target_kind=$1 where id=$2',['crm',id],'null CRM links cannot become CRM job');
await rejects('update public.oasis_voice_jobs set company_uid=$1 where id=$2',['business:synthetic',id],'manual cannot be linked by fake company UID');
check((await action('list_jobs')).rows.find(x=>x.id===id).company_name,original.company_name,'manual name shown in legacy list');
check((await campaign('legacy_jobs')).rows.find(x=>x.id===id).purpose,'customer_guidance','manual purpose shown in results');
check((await campaign('campaign_stats')).metrics.total,1,'manual included in summary');
check((await action('list_jobs',{},'alice')).code,'NOT_AUTHORIZED','general member cannot read manual results');
check(await scalar('select jsonb_agg(to_jsonb(p) order by id) v from public.oasis_prospect_companies p'),sourceBefore,'manual preserves CRM sources');
check(await scalar('select jsonb_agg(to_jsonb(a) order by id) v from public.oasis_company_sales_assignments a'),assignmentBefore,'manual preserves assignments');

// Approval expiry returns the job to a durable queue, never silently deletes it.
await db.query("update public.oasis_voice_jobs set approved_at=public.voice_test_now()-interval '25 hours' where id=$1",[id]);
check((await worker('claim',{provider:'clawops'})).code,'EMPTY','expired approval not dispatched');
check(await scalar('select status v from public.oasis_voice_jobs where id=$1',[id]),'queued','expired approval returned to queued');
check(await scalar('select safe_error_code v from public.oasis_voice_jobs where id=$1',[id]),'APPROVAL_EXPIRED','expiry visible');
check((await action('approve',{job_id:id})).ok,true,'manual explicit reapproval');
check(await scalar('select safe_error_code v from public.oasis_voice_jobs where id=$1',[id]),null,'reapproval clears expiry');
const disallowed=await worker('claim',{provider:'clawops',test_phone_allowlist:['+82'+String(1999999999)]});
check(disallowed.code,'EMPTY','test allowlist not bypassed');
check(await scalar('select status v from public.oasis_voice_jobs where id=$1',[id]),'approved','test allowlist leaves customer approval untouched');
const claim=await worker('claim',{provider:'clawops'});
check(claim.job.id,id,'manual job claimed normally');
check(claim.job.company_name,original.company_name,'manual snapshot resolves for worker');
check(claim.job.address,original.address,'manual address reaches voice worker context');
check((await worker('claim',{provider:'clawops'})).code,'BUSY','global single call rule remains');
check((await worker('get_job',{job_id:id,provider:'clawops'})).ok,true,'manual preflight valid');
check((await worker('connect',{job_id:id,provider_call_id:'clawops:CAmanualsynthetic1',dispatch_nonce:claim.job.dispatch_nonce})).ok,true,'manual provider connection');
check((await worker('get_job',{job_id:id,provider_call_id:'CA'+'1'.repeat(32)})).code,'PROVIDER_MISMATCH','provider isolation unchanged');
const visit='2030-01-08T01:00:00Z';
check((await worker('result',{job_id:id,provider_call_id:'clawops:CAmanualsynthetic1',outcome:'visit_requested',visit_at:visit,address:'Synthetic location',customer_confirmed:true,summary:'Synthetic visit request'})).ok,true,'manual visit request persists');
check((await action('confirm_visit',{job_id:id,reason:'Synthetic confirmed schedule'})).ok,true,'manual visit confirmed without fake CRM contact');
check(await scalar('select count(*)::int v from public.test_contact_logs'),0,'manual never adds contact to unrelated CRM');
await worker('status',{job_id:id,provider_call_id:'clawops:CAmanualsynthetic1',status:'completed',sequence_number:1,duration_seconds:30});
check((await campaign('legacy_jobs')).rows.find(x=>x.id===id).duration_seconds,30,'manual call duration visible');
check((await manual('admin',payload({phone:original.phone}))).code,'DUPLICATE','visit outcome blocks duplicate calling');

const revokedPayload=payload({purpose:'test'});
const revoked=await manual('admin',revokedPayload);
check(revoked.ok,true,'test call still records consent');
check((await action('revoke_permission',{job_id:revoked.job_id})).ok,true,'manual consent revocation');
check(await scalar('select status v from public.oasis_voice_jobs where id=$1',[revoked.job_id]),'cancelled','manual revoke cancels queued approval');
check((await manual('admin',payload({phone:revokedPayload.phone}))).code,'DO_NOT_CALL','manual optout applies across new requests');
check((await manual('admin',revokedPayload)).status,'cancelled','idempotent replay cannot restore consent');
await rejects('update public.oasis_voice_manual_targets set revoked_at=null where id=(select manual_target_id from public.oasis_voice_jobs where id=$1)',[revoked.job_id],'revocation cannot be removed');

const expired=await manual('admin',payload({expires_at:'2030-01-07T01:30:00Z'}));
await db.exec("set voice.test_clock='2030-01-07T02:00:00Z'");
check((await worker('claim',{provider:'clawops'})).code,'TARGET_CHANGED','expired manual consent blocks worker');
check((await action('approve',{job_id:expired.job_id})).code,'INVALID_STATE','cancelled expired consent cannot reapprove');
await db.exec("set voice.test_clock='2030-01-07T01:00:00Z'");
const inactive=await manual('admin',payload());
await db.exec("update public.oasis_users set status='suspended' where user_id='admin'");
check((await worker('claim',{provider:'clawops'})).code,'TARGET_CHANGED','suspended manual owner blocks claim');
await db.exec("update public.oasis_users set status='approved' where user_id='admin'");
const inFlight=await manual('admin',payload());
const inFlightClaim=await worker('claim',{provider:'clawops'});
check(inFlightClaim.job.id,inFlight.job_id,'next manual job selected');
check((await action('do_not_call',{job_id:inFlight.job_id})).ok,true,'in-flight manual optout stored');
check((await worker('get_job',{job_id:inFlight.job_id,provider:'clawops'})).code,'TARGET_CHANGED','manual revocation rechecked before network');
await worker('mark_failed',{job_id:inFlight.job_id,provider:'clawops'});

const customerOptout=await manual('admin',payload());
const customerOptoutClaim=await worker('claim',{provider:'clawops'});
await worker('connect',{job_id:customerOptout.job_id,provider_call_id:'clawops:CAmanualoptout0001',dispatch_nonce:customerOptoutClaim.job.dispatch_nonce});
check((await worker('result',{job_id:customerOptout.job_id,provider_call_id:'clawops:CAmanualoptout0001',outcome:'do_not_call'})).ok,true,'manual customer optout persisted with null company UID');
check(await scalar('select public.oasis_voice_blocked(null,phone_e164) v from public.oasis_voice_jobs where id=$1',[customerOptout.job_id]),true,'customer optout blocks manual and CRM number globally');
await worker('status',{job_id:customerOptout.job_id,provider_call_id:'clawops:CAmanualoptout0001',status:'completed',sequence_number:1});

// Existing CRM campaigns retain strict consent/assignment behavior and resume.
const uid='business:0000000001';
await action('grant_permission',{company_uids:[uid],kind:'explicit_consent',evidence_ref:'synthetic-existing-consent',granted_at:'2030-01-06T00:00:00Z',expires_at:'2030-02-01T00:00:00Z'});
const crmCampaign=await campaign('create_campaign',{request_id:'manual-regression-crm',name:'Synthetic CRM campaign',company_uids:[uid]});
check(crmCampaign.created_count,1,'CRM campaign still creates job');
check((await campaign('start_campaign',{campaign_id:crmCampaign.campaign_id})).approved_count,1,'CRM campaign approves');
await db.query("update public.oasis_voice_jobs set approved_at=public.voice_test_now()-interval '25 hours' where campaign_id=$1",[crmCampaign.campaign_id]);
await worker('claim',{provider:'clawops'});
check((await campaign('start_campaign',{campaign_id:crmCampaign.campaign_id})).approved_count,1,'campaign resume reapproves expired queued jobs');
const crmJob=(await campaign('campaign_jobs',{campaign_id:crmCampaign.campaign_id})).rows[0];
check(crmJob.company_name,'Synthetic A','CRM result name unchanged');
check(crmJob.target_kind,'crm','existing CRM job shape retained');
await db.query('update public.oasis_voice_jobs set permission_id=(select id from public.oasis_voice_permissions limit 1) where id=$1',[crmJob.id]);
check(await scalar('select public.oasis_voice_job_contact_ready($1) v',[crmJob.id]),true,'CRM target helper accepts valid consent');
await db.exec("update public.oasis_voice_permissions set revoked_at=public.voice_test_now() where company_uid='business:0000000001'");
check(await scalar('select public.oasis_voice_job_contact_ready($1) v',[crmJob.id]),false,'CRM revoked permission still denied');
check((await worker('claim',{provider:'clawops'})).code,'TARGET_CHANGED','invalid CRM approval still cancelled');

const limits=await manual('admin',payload());
check((await worker('claim',{provider:'clawops',daily_limit:1})).code,'DAILY_LIMIT','manual jobs never bypass shared daily cap');
await db.exec("set voice.test_clock='2030-01-12T01:00:00Z'");
check((await worker('claim',{provider:'clawops'})).code,'OUTSIDE_HOURS','manual jobs never bypass weekend guard');
await db.exec("set voice.test_clock='2030-01-07T01:00:00Z'");
const limitsClaim=await worker('claim',{provider:'clawops'});
check(limitsClaim.job.id,limits.job_id,'limits preserve queued approved target');
await worker('mark_unknown',{job_id:limits.job_id,provider:'clawops'});
check((await worker('claim',{provider:'clawops'})).code,'BUSY','ambiguous manual dispatch blocks any later call');
check((await manual('admin',payload({phone:limitsClaim.job.phone_e164.replace('+82','0')}))).code,'DUPLICATE','ambiguous manual call cannot be recreated');
await action('reconcile',{job_id:limits.job_id,reason:'Synthetic provider review',resolution:'confirmed_not_sent'});
const demoted=await manual('admin',payload());
const demotedClaim=await worker('claim',{provider:'clawops'});
await db.exec("update public.oasis_users set role='member' where user_id='admin'");
check((await worker('get_job',{job_id:demoted.job_id,provider:'clawops'})).code,'TARGET_CHANGED','owner role change checked immediately before network');
check((await worker('connect',{job_id:demoted.job_id,provider_call_id:'clawops:CAmanualdemoted01',dispatch_nonce:demotedClaim.job.dispatch_nonce})).code,'TARGET_CHANGED','owner role change checked before media');
await db.exec("update public.oasis_users set role='admin' where user_id='admin'");
await worker('mark_failed',{job_id:demoted.job_id,provider:'clawops'});

await db.exec('reset role');
const finalCount=await scalar('select count(*)::int v from public.oasis_voice_jobs');
await db.exec(manualSql);
check(await scalar('select count(*)::int v from public.oasis_voice_jobs'),finalCount,'migration replay preserves all jobs/results');
check(await scalar('select jsonb_agg(to_jsonb(p) order by id) v from public.oasis_prospect_companies p'),sourceBefore,'no CRM source rewrite after all manual operations');
console.log(`MANUAL CALL SQL PASS: ${checks} synthetic checks; no network or actual calls.`);
await db.close();
