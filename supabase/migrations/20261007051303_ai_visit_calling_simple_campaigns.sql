-- v9.14.4: bounded, administrator-only filter/count campaigns.
-- No source/CRM/consent rows are created or changed. No daily limit is increased.
-- Uses the existing queue transaction lock and the existing enqueue/approve rules.
begin;
set local lock_timeout='5s';
set local statement_timeout='120s';

alter table public.oasis_voice_campaigns
 add column if not exists selection_filters jsonb,
 add column if not exists requested_count integer,
 add column if not exists request_fingerprint text,
 add column if not exists selection_kind text not null default 'explicit';
do $constraints$
begin
 if not exists(select 1 from pg_constraint where conrelid='public.oasis_voice_campaigns'::regclass and conname='oasis_voice_campaign_selection_check') then
  alter table public.oasis_voice_campaigns add constraint oasis_voice_campaign_selection_check check(
   selection_kind in ('explicit','filtered') and
   (selection_kind<>'filtered' or (selection_filters is not null and requested_count is not null and request_fingerprint is not null
    and jsonb_typeof(selection_filters)='object' and requested_count between 1 and 1000 and request_fingerprint ~ '^[0-9a-f]{32}$')));
 end if;
end $constraints$;

create or replace function public.oasis_voice_filtered_campaign(p_current_user_id text,p_payload jsonb default '{}')
returns jsonb language plpgsql security invoker set search_path='' set statement_timeout='30s' as $filtered$
declare
 actor text:=lower(btrim(coalesce(p_current_user_id,''))); filters jsonb; request_key text; wanted integer; fingerprint text;
 business_filter text; region_filter text; industry_filter text; phone_filter text; discovery_filter text;
 campaign public.oasis_voice_campaigns%rowtype; candidate jsonb; reply jsonb; job public.oasis_voice_jobs%rowtype;
 selected jsonb:='[]'; exclusions jsonb:='{}'; eligible_count integer:=0; evaluated_count integer:=0; matched_count integer:=0;
 made integer:=0; affected integer; code text; result jsonb;
begin
 if not coalesce(public.oasis_sales_actor_is_active(actor),false) or not coalesce(public.oasis_sales_actor_is_admin(actor),false) then
  return jsonb_build_object('ok',false,'code','NOT_AUTHORIZED'); end if;
 if p_payload is null or jsonb_typeof(p_payload)<>'object' or jsonb_typeof(p_payload->'filters') is distinct from 'object'
  or p_payload->'approval_confirmed' is distinct from 'true'::jsonb
  or jsonb_typeof(p_payload->'requested_count') is distinct from 'number'
  or coalesce(p_payload->>'requested_count','') !~ '^[1-9][0-9]{0,3}$'
  or exists(select 1 from jsonb_object_keys(p_payload) k where k not in ('request_id','filters','requested_count','approval_confirmed'))
  or exists(select 1 from jsonb_each(p_payload->'filters') f where f.key not in ('business_type','region','industry','phone_type','discovery_type')
   or jsonb_typeof(f.value) not in ('string','null')) then raise exception 'INVALID_INPUT'; end if;
 request_key:=p_payload->>'request_id'; wanted:=(p_payload->>'requested_count')::integer;
 if jsonb_typeof(p_payload->'request_id') is distinct from 'string' or request_key !~ '^[A-Za-z0-9._:-]{8,120}$' or wanted>1000 then raise exception 'INVALID_INPUT'; end if;
 business_filter:=lower(btrim(coalesce(p_payload->'filters'->>'business_type','all')));
 region_filter:=lower(regexp_replace(btrim(coalesce(p_payload->'filters'->>'region','')),'\s+',' ','g'));
 industry_filter:=lower(regexp_replace(btrim(coalesce(p_payload->'filters'->>'industry','')),'\s+',' ','g'));
 phone_filter:=lower(btrim(coalesce(p_payload->'filters'->>'phone_type','all')));
 discovery_filter:=lower(btrim(coalesce(p_payload->'filters'->>'discovery_type','all')));
 if business_filter not in ('all','individual','corporate','unknown') or phone_filter not in ('all','mobile','landline')
  or discovery_filter not in ('all','employment_growth','new','other','unknown') or length(region_filter)>80 or length(industry_filter)>120 then raise exception 'INVALID_INPUT'; end if;
 filters:=jsonb_build_object('business_type',business_filter,'region',region_filter,'industry',industry_filter,'phone_type',phone_filter,'discovery_type',discovery_filter);
 fingerprint:=md5(jsonb_build_object('filters',filters,'requested_count',wanted)::text);
 perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('oasis-voice-queue',0));
 select * into campaign from public.oasis_voice_campaigns where created_by=actor and request_id=request_key for update;
 if campaign.id is not null then
  if campaign.selection_kind<>'filtered' or campaign.request_fingerprint is distinct from fingerprint then
   return jsonb_build_object('ok',false,'code','IDEMPOTENCY_CONFLICT'); end if;
  -- Never expand an old request to newly eligible companies or restart stopped jobs.
  return campaign.creation_result||jsonb_build_object('campaign_id',campaign.id,'status',campaign.status,'replayed',true);
 end if;

 -- Start at the assigned CRM target set, not millions of raw source rows. Source
 -- enrichment is an indexed contact_key lookup only. Unknown/unlinked source
 -- attributes remain unknown; a company name is NEVER an identity join.
 with uids as materialized (
  select distinct a.company_uid from public.oasis_company_sales_assignments a
  where public.oasis_is_valid_company_uid(a.company_uid) and a.assigned_user_id is not null
 ), targets as materialized (
  select u.company_uid,public.oasis_voice_target(u.company_uid) target from uids u
 ), details as materialized (
  select t.company_uid,t.target,t.target->>'phone_e164' phone,
   regexp_replace(coalesce(p.business_no,''),'[^0-9]','','g') business_no,
   p.source,p.source_key,p.source_data,p.region,p.address,p.industry_name,
   coalesce(regexp_replace(p.business_no,'[^0-9]','','g') ~ '^[0-9]{10}$' and p.company_uid ~ '^business:[0-9]{10}$'
    and p.company_uid<>'business:'||regexp_replace(p.business_no,'[^0-9]','','g'),false) own_conflict
  from targets t join public.oasis_prospect_companies p on p.id=(t.target->>'prospect_id')::uuid
  where t.target is not null
 ), enriched as materialized (
  select d.*,s.source_row,s.identity_conflict,
   case when d.business_no ~ '^[0-9]{10}$' then d.business_no
    when d.company_uid ~ '^business:[0-9]{10}$' then substr(d.company_uid,10)
    when s.source_row->>'business_no' ~ '^[0-9]{10}$' then s.source_row->>'business_no' else '' end effective_business_no
  from details d left join lateral (
   select (array_agg(jsonb_build_object('business_no',e.business_no,'province',e.province,'province_code',e.province_code,'address',e.address,
    'industry_name',e.industry_name,'industry_category',e.industry_category,'is_new_company',e.is_new_company,
    'opening_signal_basis',e.opening_signal_basis,'employee_growth',e.employee_growth,'current_period',e.current_period,'source_type',e.source_type)
    order by e.current_period desc nulls last,e.contact_key))[1] source_row,
    bool_or(e.business_no ~ '^[0-9]{10}$' and (
     (d.business_no ~ '^[0-9]{10}$' and e.business_no<>d.business_no)
     or (d.company_uid ~ '^business:[0-9]{10}$' and d.company_uid<>'business:'||e.business_no))) identity_conflict
   from public.oasis_employment_contacts e
   where e.contact_key=any(array[
    case when d.business_no ~ '^[0-9]{10}$' then 'business:'||d.business_no end,
    case when d.company_uid ~ '^business:[0-9]{10}$' then d.company_uid end,
    nullif(d.source_data->>'contact_key',''),nullif(d.source_key,'')])
    and ((e.business_no ~ '^[0-9]{10}$' and (e.business_no=d.business_no or d.company_uid='business:'||e.business_no))
     or e.contact_key=nullif(d.source_data->>'contact_key','')
     or (nullif(btrim(d.source),'') is not null and nullif(btrim(e.source_record_key),'') is not null and d.source=e.source_type
      and d.source_key in(e.source_record_key,e.source_type||':'||e.source_record_key,'recent_opening:'||e.source_type||':'||e.source_record_key)))
  ) s on true
 ), classified as materialized (
  select e.*,public.oasis_voice_business_type(e.effective_business_no) business_type,
   case when e.source_row is null then 'unknown' else public.oasis_voice_discovery_type(
    (e.source_row->>'is_new_company')::boolean,e.source_row->>'opening_signal_basis',(e.source_row->>'employee_growth')::integer,
    e.source_row->>'current_period',e.source_row->>'source_type') end discovery_type
  from enriched e
 ), matching as materialized (
  select c.* from classified c where (business_filter='all' or c.business_type=business_filter)
   and (phone_filter='all' or (phone_filter='mobile' and c.phone like '+8210%') or (phone_filter='landline' and c.phone not like '+8210%'))
   and (discovery_filter='all' or c.discovery_type=discovery_filter)
   and (region_filter='' or lower(coalesce(c.source_row->>'province_code',''))=region_filter
    or starts_with(lower(coalesce(nullif(c.source_row->>'province',''),c.region,'')),region_filter)
    or starts_with(lower(coalesce(nullif(c.source_row->>'address',''),c.address,'')),region_filter))
   and (industry_filter='' or strpos(lower(coalesce(nullif(c.source_row->>'industry_name',''),nullif(c.source_row->>'industry_category',''),c.industry_name,'')),industry_filter)>0)
 ), assessed as materialized (
  select m.*,case when m.own_conflict or coalesce(m.identity_conflict,false) then 'IDENTITY_CONFLICT'
   when public.oasis_voice_blocked(m.company_uid,m.phone) then 'DO_NOT_CALL'
   when not exists(select 1 from public.oasis_voice_permissions p where p.company_uid=m.company_uid and p.phone_e164=m.phone
    and p.kind in ('explicit_consent','callback_request') and p.revoked_at is null and p.granted_at<=now() and p.expires_at>now()) then 'CONSENT_REQUIRED'
   when exists(select 1 from public.oasis_voice_jobs j where (j.company_uid=m.company_uid or j.phone_e164=m.phone)
    and (j.status in ('queued','approved','dispatching','accepted','in_progress','unknown') or j.dispatch_at>now()-interval '7 days'
     or j.outcome in ('visit_requested','callback_requested'))) then 'DUPLICATE' else null end reason
  from matching m
 ), ranked as materialized (
  select a.*,row_number() over(partition by a.phone order by a.company_uid) phone_rank from assessed a where a.reason is null
 ), eligible as materialized (select * from ranked where phone_rank=1), picked as (
  select company_uid,phone,target->>'contact_id' contact_id from eligible order by company_uid limit wanted
 )
 select (select count(*)::integer from classified),(select count(*)::integer from matching),(select count(*)::integer from eligible),
  coalesce((select jsonb_agg(to_jsonb(p) order by p.company_uid) from picked p),'[]'::jsonb),
  coalesce((select jsonb_object_agg(reason,n) from (select reason,count(*)::integer n from assessed where reason is not null group by reason) q),'{}'::jsonb)
   ||jsonb_build_object('DUPLICATE_PHONE',(select count(*)::integer from ranked where phone_rank>1))
 into evaluated_count,matched_count,eligible_count,selected,exclusions;

 if eligible_count=0 then return jsonb_build_object('ok',false,'code','NO_ELIGIBLE_TARGETS','campaign_id',null,'requested_count',wanted,
  'created_count',0,'eligible_count',0,'evaluated_count',evaluated_count,'matched_count',matched_count,
  'shortage_count',wanted,'excluded_counts',exclusions,'status',null,'replayed',false); end if;
 insert into public.oasis_voice_campaigns(request_id,created_by,name,selection_kind,selection_filters,requested_count,request_fingerprint)
  values(request_key,actor,'AI 전화 안내 '||to_char(now() at time zone 'Asia/Seoul','YYYY-MM-DD HH24:MI'),'filtered',filters,wanted,fingerprint)
  returning * into campaign;
 for candidate in select value from jsonb_array_elements(selected) loop
  begin
   reply:=public.oasis_voice_action(actor,'enqueue',jsonb_build_object('request_id','campaign:'||campaign.id::text,'company_uids',jsonb_build_array(candidate->>'company_uid')));
   if not coalesce((reply->>'ok')::boolean,false) then
    code:=case when reply->>'code' in ('NOT_AUTHORIZED','DO_NOT_CALL','CONSENT_REQUIRED','DUPLICATE') then reply->>'code' else 'CAMPAIGN_CREATE_FAILED' end;
    raise exception '%',code;
   end if;
   select * into job from public.oasis_voice_jobs where id=(reply->'rows'->0->>'id')::uuid for update;
   -- A phone collector/admin could update the contact while filtering. Never
   -- substitute another phone type or silently use the new number for this request.
   if job.id is null or job.requested_by is distinct from actor or job.campaign_id is not null or job.status<>'queued'
    or job.phone_e164 is distinct from candidate->>'phone' or job.contact_id::text is distinct from candidate->>'contact_id' then raise exception 'TARGET_CHANGED'; end if;
   update public.oasis_voice_jobs set campaign_id=campaign.id,updated_at=now() where id=job.id;
   reply:=public.oasis_voice_action(actor,'approve',jsonb_build_object('job_id',job.id));
   if not coalesce((reply->>'ok')::boolean,false) then raise exception 'CAMPAIGN_CREATE_FAILED'; end if;
   made:=made+1;
  exception when raise_exception then
   if sqlerrm not in ('NOT_AUTHORIZED','DO_NOT_CALL','CONSENT_REQUIRED','DUPLICATE','TARGET_CHANGED') then raise; end if;
   exclusions:=jsonb_set(exclusions,array[sqlerrm],to_jsonb(coalesce((exclusions->>sqlerrm)::integer,0)+1),true);
  end;
 end loop;
 -- If every candidate changed mid-request, roll back the campaign and all audit
 -- writes as well, rather than persisting a successful-looking empty campaign.
 if made=0 then raise exception 'NO_ELIGIBLE_TARGETS'; end if;
 result:=jsonb_build_object('ok',true,'code','OK','campaign_id',campaign.id,'requested_count',wanted,'created_count',made,
  'eligible_count',eligible_count,'evaluated_count',evaluated_count,'matched_count',matched_count,'shortage_count',greatest(0,wanted-made),
  'excluded_counts',exclusions,'status','running','replayed',false,'approval_expires_at',now()+interval '24 hours');
 update public.oasis_voice_campaigns set status='running',creation_result=result,updated_at=now() where id=campaign.id;
 insert into public.oasis_voice_events(actor,action,safe_detail) values(actor,'filtered_campaign_created',
  jsonb_build_object('campaign_id',campaign.id,'requested_count',wanted,'created_count',made,'filter_fingerprint',fingerprint));
 return result;
exception when query_canceled then return jsonb_build_object('ok',false,'code','SEARCH_TIMEOUT');
 when lock_not_available then return jsonb_build_object('ok',false,'code','BUSY');
 when invalid_text_representation or numeric_value_out_of_range then return jsonb_build_object('ok',false,'code','INVALID_INPUT');
 when raise_exception then return jsonb_build_object('ok',false,'code',case when sqlerrm in ('INVALID_INPUT','NO_ELIGIBLE_TARGETS') then sqlerrm else 'CAMPAIGN_CREATE_FAILED' end);
 when others then return jsonb_build_object('ok',false,'code','CAMPAIGN_CREATE_FAILED');
end $filtered$;
revoke all on function public.oasis_voice_filtered_campaign(text,jsonb) from public,anon,authenticated;
grant execute on function public.oasis_voice_filtered_campaign(text,jsonb) to service_role;
commit;
