// Offline v9.14.3 cumulative migration tests. Only generated synthetic fixtures.
import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
process.on('uncaughtException',e=>{console.error('CATALOG TEST FAILED:',e.message,e.code||'',e.position||'',e.where||'');process.exit(1);});
const {PGlite}=await import(process.env.PGLITE_PACKAGE||'@electric-sql/pglite');
const db=new PGlite(); let checks=0;
const check=(a,b,label)=>{assert.deepEqual(a,b,label);checks++;};
const scalar=async sql=>(await db.query(sql)).rows[0].v;
const rpc=async(name,actor,action,payload={})=>(await db.query(`select public.${name}($1,$2,$3::jsonb) v`,[actor,action,JSON.stringify(payload)])).rows[0].v;
const catalog=async(actor,payload={})=>(await db.query('select public.oasis_voice_catalog($1,$2::jsonb) v',[actor,JSON.stringify(payload)])).rows[0].v;
// Reuse the existing synthetic, standalone fixture, not live data or a DB dump.
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
 insert into public.oasis_employment_contacts(contact_key,source_type,source_record_key,business_no,company_name,address,
  province,district,province_code,industry_name,industry_category,current_employee_count,employee_growth,previous_period,current_period,
  is_new_company,opening_signal_basis,mobile_phone,landline_phone,has_mobile_phone,has_landline_phone)
 select 'business:12381'||lpad(i::text,5,'0'),'synthetic_source',i::text,'12381'||lpad(i::text,5,'0'),
  'Synthetic company '||i,'Synthetic address '||i,'Synthetic region','Synthetic district','ZZ','Synthetic industry','Synthetic category',
  10,case when i%3=0 then 1 else 0 end,'2024','2025',i%5=0,case when i%5=0 then 'synthetic_new_signal' else '' end,
  case when i%2=0 then '01000000001' else '' end,case when i%3=0 then '0200000001' else '' end,i%2=0,i%3=0
 from generate_series(1,205) i;
 update public.oasis_prospect_companies set business_no='1238100002',source='synthetic_source',source_key='2',region='Synthetic region',industry_name='Synthetic industry' where company_uid='business:0000000001';
 update public.oasis_prospect_companies set company_uid='business:1238100002' where company_uid='business:0000000001';
 update public.oasis_company_sales_assignments set company_uid='business:1238100002' where company_uid='business:0000000001';
 update public.oasis_prospect_companies set business_no='9870100001',region='Supplemental region',industry_name='Supplemental industry' where company_uid='business:0000000002';
`);
for(const f of ['20261001235500_ai_visit_calling.sql','20261003010404_ai_visit_calling_clawops_provider.sql','20261006081850_ai_visit_calling_bulk_campaigns.sql'])
 await db.exec(await readFile(new URL('../supabase/migrations/'+f,import.meta.url),'utf8'));
const migration=await readFile(new URL('../supabase/migrations/20261006093201_ai_visit_calling_admin_catalog.sql',import.meta.url),'utf8');
const before=await scalar("select jsonb_build_object('source',(select jsonb_agg(to_jsonb(e) order by contact_key) from public.oasis_employment_contacts e),'prospects',(select jsonb_agg(to_jsonb(p) order by id) from public.oasis_prospect_companies p),'contacts',(select jsonb_agg(to_jsonb(c) order by id) from public.oasis_prospect_contacts c)) v");
const workerBefore=await scalar("select pg_get_functiondef('public.oasis_voice_worker(text,jsonb)'::regprocedure) v");
await db.exec(migration); await db.exec(migration);
check(await scalar("select pg_get_functiondef('public.oasis_voice_worker(text,jsonb)'::regprocedure) v"),workerBefore,'worker is byte-for-byte unchanged');
for(const role of ['anon','authenticated']) for(const signature of ['oasis_voice_catalog(text,jsonb)','oasis_voice_action(text,text,jsonb)','oasis_voice_campaign_action(text,text,jsonb)','oasis_voice_business_type(text)','oasis_voice_discovery_type(boolean,text,integer,text,text)'])
 check(await scalar(`select has_function_privilege('${role}','public.${signature}','execute') v`),false,role+' cannot execute '+signature);
for(const signature of ['oasis_voice_catalog(text,jsonb)','oasis_voice_action(text,text,jsonb)','oasis_voice_campaign_action(text,text,jsonb)']) {
 check(await scalar(`select prosecdef v from pg_proc where oid='public.${signature}'::regprocedure`),false,'security invoker '+signature);
 check(await scalar(`select proconfig @> ARRAY['search_path=""'] v from pg_proc where oid='public.${signature}'::regprocedure`),true,'empty search path '+signature);
}
check(await scalar("select provolatile='s' v from pg_proc where oid='public.oasis_voice_catalog(text,jsonb)'::regprocedure"),true,'catalog is STABLE/read-only');
await db.exec('set role service_role');
for(const actor of ['alice','bob','suspended','missing','']) {
 check((await catalog(actor)).code,'NOT_AUTHORIZED','catalog requires current active admin');
 for(const action of ['candidates','list_jobs','list_permissions','grant_permission','enqueue','approve','revoke_permission','suppress','cancel','confirm_visit'])
  check((await rpc('oasis_voice_action',actor,action)).code,'NOT_AUTHORIZED','legacy action requires current admin: '+actor+'/'+action);
 for(const action of ['campaign_stats','list_campaigns','campaign_jobs','legacy_jobs','create_campaign','start_campaign','pause_campaign','cancel_campaign'])
  check((await rpc('oasis_voice_campaign_action',actor,action)).code,'NOT_AUTHORIZED','campaign requires current admin: '+action);
}
check((await catalog(' ADMIN ')).ok,true,'canonical admin actor accepted');
let page=await catalog('admin'); let all=[...page.rows];
check(page.rows.length,100,'first page exactly 100'); check(page.has_more,true,'first page more');
check(page.rows.every(x=>!Object.hasOwn(x,'mobile')&&!Object.hasOwn(x,'landline')&&!Object.hasOwn(x,'business_no')&&!Object.hasOwn(x,'source_data')),true,'no raw contact/record payload');
check(page.rows.find(x=>x.company_uid==='business:1238100002').blocked_reason,'CONSENT_REQUIRED','linked target retains number-specific consent requirement');
check(page.rows.filter(x=>x.company_uid===null).every(x=>!x.eligible&&x.blocked_reason==='SOURCE_NOT_LINKED'),true,'raw sources do not become targets');
const firstCursor=page.next_cursor;
page=await catalog('admin',{cursor:page.next_cursor}); all.push(...page.rows); check(page.rows.length,100,'second page exactly 100');
page=await catalog('admin',{cursor:page.next_cursor}); all.push(...page.rows); check(page.rows.length,7,'tail has five source and two supplemental prospects');
check(page.has_more,false,'tail is final'); check(page.next_cursor,null,'no trailing cursor');
check(new Set(all.map(x=>x.row_id)).size,207,'all pages have no omitted or duplicate row identity');
check((await catalog('admin',{limit:1000})).rows.length,100,'server limit capped');
check((await catalog('admin',{cursor:firstCursor,phone_type:'mobile'})).code,'INVALID_CURSOR','changed filters invalidate old cursor');
check((await catalog('admin',{cursor:{source:'prospect',key:'bad',fingerprint:firstCursor.fingerprint}})).code,'INVALID_CURSOR','invalid UUID cursor rejected');
check((await catalog('admin',{cursor:{source:'unknown',key:'x',fingerprint:firstCursor.fingerprint}})).code,'INVALID_CURSOR','invalid source rejected');
check((await catalog('admin',{business_type:'malicious'})).code,'INVALID_INPUT','unknown business filter rejected');
check((await catalog('admin',{phone_type:'malicious'})).code,'INVALID_INPUT','unknown phone filter rejected');
check((await catalog('admin',{query:"'; drop table oasis_employment_contacts; --"})).rows.length,0,'SQL is not executable search input');
check((await catalog('admin',{query:'%'})).rows.length,0,'percent is literal not wildcard');
check((await catalog('admin',{query:'_'})).rows.length,0,'underscore is literal not wildcard');
check((await catalog('admin',{query:'not present anywhere'})).rows.length,0,'empty search is exact, not truncated');
for(const filter of ['mobile','landline','both','none']) {
 const result=await catalog('admin',{phone_type:filter});
 check(result.ok,true,'phone filter accepted '+filter);
 check(result.rows.every(x=>filter==='mobile'?Boolean(x.mobile_phone_masked):filter==='landline'?Boolean(x.landline_phone_masked):filter==='both'?Boolean(x.mobile_phone_masked&&x.landline_phone_masked):!x.mobile_phone_masked&&!x.landline_phone_masked),true,'phone filter before pagination '+filter);
}
check((await catalog('admin',{region:'Supplemental'})).rows.length,1,'supplemental region filter');
check((await catalog('admin',{industry:'Supplemental'})).rows.length,1,'supplemental industry filter');
check((await catalog('admin',{region:'ZZ'})).rows.length,100,'source province-code filter');
check((await catalog('admin',{business_type:'individual'})).rows.length,1,'registration-code individual includes supplemental row');
check((await catalog('admin',{business_type:'corporate'})).rows.length,100,'registration-code corporate independent of company name');
for(const filter of ['employment_growth','new','other','unknown']) {
 const result=await catalog('admin',{discovery_type:filter});
 check(result.rows.every(x=>x.discovery_type===filter),true,'exclusive discovery filter '+filter);
}
for(const [number,expected] of [['1238100000','corporate'],['1238400000','corporate'],['1238500000','corporate'],['1238800000','corporate'],['1230100000','individual'],['1239900000','individual'],['1238000000','unknown'],['1238200000','unknown'],['1238300000','unknown'],['1238900000','unknown'],['12345','unknown']])
 check(await scalar(`select public.oasis_voice_business_type('${number}') v`),expected,'conservative NTS business-code classification');
check(await scalar("select jsonb_build_object('source',(select jsonb_agg(to_jsonb(e) order by contact_key) from public.oasis_employment_contacts e),'prospects',(select jsonb_agg(to_jsonb(p) order by id) from public.oasis_prospect_companies p),'contacts',(select jsonb_agg(to_jsonb(c) order by id) from public.oasis_prospect_contacts c)) v"),before,'all catalog reads preserve source/prospect/contact values');
check(await scalar('select count(*)::int v from public.oasis_voice_permissions'),0,'catalog never infers consent');
check(await scalar('select count(*)::int v from public.oasis_voice_jobs'),0,'catalog never queues calls');
check(await scalar('select count(*)::int v from public.oasis_voice_events'),0,'catalog never records call attempts');
check(await scalar('select count(*)::int v from public.oasis_company_sales_assignments'),3,'catalog never claims raw sources');
// Explicit admin permission still works, but public-number filtering never changes target.
const consent={company_uids:['business:1238100002'],kind:'explicit_consent',evidence_ref:'synthetic-only',granted_at:new Date(Date.now()-60000).toISOString(),expires_at:new Date(Date.now()+86400000).toISOString()};
check((await rpc('oasis_voice_action','admin','grant_permission',consent)).ok,true,'admin legacy consent workflow retained');
let linked=(await catalog('admin',{phone_type:'mobile'})).rows.find(x=>x.company_uid==='business:1238100002');
check(linked.eligible,true,'explicit permission enables existing target');
check(linked.contact_id,'00000000-0000-4000-8000-000000000011','catalog preserves existing exact contact ID');
await db.exec("update public.oasis_prospect_contacts set do_not_contact=true where id='00000000-0000-4000-8000-000000000011'");
linked=(await catalog('admin')).rows.find(x=>x.company_uid==='business:1238100002');
check(linked.blocked_reason,'DO_NOT_CALL','current contact opt-out blocks immediately');
check(linked.do_not_call,true,'actual target suppression field is explicit');
check((await catalog('admin')).rows.find(x=>x.company_uid===null).do_not_call,null,'unlinked target suppression is unknown, never safe');
// A reused source identity MUST NOT silently bind a different strong legal entity.
await db.exec("update public.oasis_prospect_companies set source_key='3' where company_uid='business:1238100002'");
let conflict=(await catalog('admin',{query:'Synthetic company 3'})).rows.find(x=>x.company_name==='Synthetic company 3');
check(conflict.blocked_reason,'IDENTITY_CONFLICT','source match cannot override conflicting business number');
check(conflict.company_uid,null,'conflicting source UID is not selectable');
await db.exec("update public.oasis_prospect_companies set source_key='2',company_uid='business:9999900000' where company_uid='business:1238100002'");
conflict=(await catalog('admin',{query:'Synthetic company 2'})).rows.find(x=>x.company_name==='Synthetic company 2');
check(conflict.blocked_reason,'IDENTITY_CONFLICT','business UID must agree with source legal identity');
await db.exec("reset role; update public.oasis_employment_contacts set business_no='' where contact_key='business:1238100002'; set role service_role;");
conflict=(await catalog('admin',{query:'Synthetic company 2'})).rows.find(x=>x.company_name==='Synthetic company 2');
check(conflict.blocked_reason,'IDENTITY_CONFLICT','saved business/UID conflict is blocked even when source number is missing');
check(conflict.company_uid,null,'missing source number cannot legitimize conflicting saved UID');
await db.exec("reset role; update public.oasis_employment_contacts set business_no='1238100002' where contact_key='business:1238100002'; set role service_role;");
await db.exec("update public.oasis_prospect_companies set company_uid='garbage-unvalidated-uid' where company_uid='business:9999900000'");
conflict=(await catalog('admin',{query:'Synthetic company 2'})).rows.find(x=>x.company_name==='Synthetic company 2');
check(conflict.company_uid,null,'invalid saved UID is never exposed as a call target');
check(conflict.eligible,false,'invalid UID cannot become eligible');
// Empty provenance never matches unrelated empty provenance.
await db.exec("reset role; update public.oasis_employment_contacts set business_no='',source_type='',source_record_key='' where contact_key='business:1238100004'; update public.oasis_prospect_companies set business_no='',source='',source_key='',company_uid='business:9999900000' where company_uid='garbage-unvalidated-uid'; set role service_role;");
conflict=(await catalog('admin',{query:'Synthetic company 4'})).rows.find(x=>x.company_name==='Synthetic company 4');
check(conflict.company_uid,null,'empty source identity cannot link unrelated entities');
check(conflict.blocked_reason,'SOURCE_NOT_LINKED','empty provenance remains unlinked');
// Same name/place with a conflicting known number remains visible as a separate row.
await db.exec("reset role; insert into public.oasis_employment_contacts(contact_key,source_type,source_record_key,business_no,company_name,address,province,district,industry_name) values('place:'||md5('syntheticb|syntheticvisitlocationb'),'synthetic','separate-place','1238109999','Synthetic B','Synthetic visit location B','Synthetic region','',''); set role service_role;");
const separate=(await catalog('admin',{query:'Synthetic B'})).rows;
check(separate.length,2,'different strong identities at the same normalized name/address are preserved');
await db.exec("update public.oasis_users set role='member' where user_id='admin'");
check((await catalog('admin')).code,'NOT_AUTHORIZED','role downgrade immediately blocks catalogue');
check((await rpc('oasis_voice_action','admin','list_jobs')).code,'NOT_AUTHORIZED','role downgrade immediately blocks legacy RPC');
check((await rpc('oasis_voice_campaign_action','admin','campaign_stats')).code,'NOT_AUTHORIZED','role downgrade immediately blocks campaign RPC');
await db.exec("update public.oasis_users set role='admin',status='suspended' where user_id='admin'");
check((await catalog('admin')).code,'NOT_AUTHORIZED','suspended admin denied');
await db.close();
console.log(`PASS: ${checks} synthetic catalogue/admin/RPC checks; cumulative migration applied twice; no network or live data.`);
