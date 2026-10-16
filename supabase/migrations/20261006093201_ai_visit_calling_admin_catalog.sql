-- v9.14.3: administrator-only voice operations and read-only source catalogue.
-- No source rows, assignment, consent, collector state or worker function are changed.
-- Large source indexes are deliberately NOT built in this migration.
begin;
set local lock_timeout='5s';
set local statement_timeout='120s';

-- Preserve the complete installed action bodies, adding an unconditional gate before
-- any read or write. No renamed, less-restricted helper is left callable by PostgREST.
do $guard$
declare sig text; definition text; marker text:='-- oasis_voice_admin_only_v9143';
begin
 foreach sig in array array['public.oasis_voice_action(text,text,jsonb)','public.oasis_voice_campaign_action(text,text,jsonb)'] loop
  select replace(pg_get_functiondef(sig::regprocedure),chr(13),'') into definition;
  if position(marker in definition)=0 then
   if position(E'\nbegin\n' in definition)=0 then raise exception 'VOICE_ADMIN_GATE_SOURCE_NOT_RECOGNIZED'; end if;
   definition:=regexp_replace(definition,E'\nbegin\n',$inject$
begin
 -- oasis_voice_admin_only_v9143
 if not coalesce(public.oasis_sales_actor_is_active(lower(btrim(coalesce(p_current_user_id,'')))),false)
  or not coalesce(public.oasis_sales_actor_is_admin(lower(btrim(coalesce(p_current_user_id,'')))),false) then
  return jsonb_build_object('ok',false,'code','NOT_AUTHORIZED'); end if;
$inject$);
   execute definition;
  end if;
 end loop;
end $guard$;
revoke all on function public.oasis_voice_action(text,text,jsonb),public.oasis_voice_campaign_action(text,text,jsonb) from public,anon,authenticated;
grant execute on function public.oasis_voice_action(text,text,jsonb),public.oasis_voice_campaign_action(text,text,jsonb) to service_role;

-- NTS registration-code classification, not a company-name heuristic.
-- 80/82/83/89 include non-corporate associations/public entities: remain unknown.
-- https://www.nts.go.kr/nts/na/ntt/selectNttInfo.do?mi=2448&nttSn=1386
create or replace function public.oasis_voice_business_type(p_business_no text)
returns text language sql immutable parallel safe security invoker set search_path='' as $$
 select case when b !~ '^[0-9]{10}$' then 'unknown'
  when substring(b from 4 for 2) between '01' and '79' or substring(b from 4 for 2) between '90' and '99' then 'individual'
  when substring(b from 4 for 2) in ('81','84','85','86','87','88') then 'corporate' else 'unknown' end
 from (select regexp_replace(coalesce(p_business_no,''),'[^0-9]','','g') b) q;
$$;
create or replace function public.oasis_voice_discovery_type(p_new boolean,p_basis text,p_growth integer,p_period text,p_source text)
returns text language sql immutable parallel safe security invoker set search_path='' as $$
 select case when p_new and nullif(btrim(p_basis),'') is not null then 'new'
 when p_growth>0 then 'employment_growth'
 when nullif(btrim(p_period),'') is not null or p_source='comwel_all_employers' then 'other' else 'unknown' end;
$$;
revoke all on function public.oasis_voice_business_type(text),public.oasis_voice_discovery_type(boolean,text,integer,text,text) from public,anon,authenticated;
grant execute on function public.oasis_voice_business_type(text),public.oasis_voice_discovery_type(boolean,text,integer,text,text) to service_role;

create or replace function public.oasis_voice_catalog(p_current_user_id text,p_payload jsonb default '{}')
returns jsonb language plpgsql stable security invoker set search_path='' set statement_timeout='15s' as $catalog$
declare
 actor text:=lower(btrim(coalesce(p_current_user_id,''))); lim integer; request_filters jsonb; fingerprint text;
 search_text text; region_text text; industry_text text; business_filter text; phone_filter text; discovery_filter text;
 source_cursor text:='employment'; key_cursor text:=''; next_value jsonb; has_more boolean:=false;
 source_where text:='true'; prospect_where text:='true'; statement text; source_page jsonb:='[]'; prospect_page jsonb:='[]';
 rows_out jsonb:='[]'; r jsonb; target jsonb; uid text; uid_count integer; matched_uids jsonb;
 blocked boolean; valid_permission boolean; blocked_reason text; mobile text; landline text; n integer:=0;
begin
 if not coalesce(public.oasis_sales_actor_is_active(actor),false) or not coalesce(public.oasis_sales_actor_is_admin(actor),false) then
  return jsonb_build_object('ok',false,'code','NOT_AUTHORIZED'); end if;
 if p_payload is null or jsonb_typeof(p_payload)<>'object' then raise exception 'INVALID_INPUT'; end if;
 lim:=greatest(1,least(100,coalesce((p_payload->>'limit')::integer,100)));
 search_text:=btrim(coalesce(p_payload->>'query','')); region_text:=btrim(coalesce(p_payload->>'region',''));
 industry_text:=btrim(coalesce(p_payload->>'industry',''));
 business_filter:=coalesce(p_payload->>'business_type','all'); phone_filter:=coalesce(p_payload->>'phone_type','all');
 discovery_filter:=coalesce(p_payload->>'discovery_type','all');
 if length(search_text)>100 or length(region_text)>100 or length(industry_text)>100
  or business_filter not in ('all','individual','corporate','unknown') or phone_filter not in ('all','mobile','landline','both','none')
  or discovery_filter not in ('all','employment_growth','new','other','unknown') then raise exception 'INVALID_INPUT'; end if;
 request_filters:=jsonb_build_object('query',search_text,'region',region_text,'industry',industry_text,'business_type',business_filter,'phone_type',phone_filter,'discovery_type',discovery_filter);
 fingerprint:=md5(request_filters::text);
 if p_payload ? 'cursor' and p_payload->'cursor'<>'null'::jsonb then
  if jsonb_typeof(p_payload->'cursor')<>'object' or p_payload->'cursor'->>'fingerprint' is distinct from fingerprint
   or coalesce(p_payload->'cursor'->>'source','') not in ('employment','prospect')
   or nullif(p_payload->'cursor'->>'key','') is null or length(p_payload->'cursor'->>'key')>1000 then raise exception 'INVALID_CURSOR'; end if;
  source_cursor:=p_payload->'cursor'->>'source'; key_cursor:=p_payload->'cursor'->>'key';
  if source_cursor='prospect' and key_cursor !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then raise exception 'INVALID_CURSOR'; end if;
 end if;
 -- Format only quoted literals, never caller SQL. Escape SQL wildcard characters.
 search_text:=replace(replace(replace(search_text,E'\\',E'\\\\'),'%',E'\\%'),'_',E'\\_');
 industry_text:=replace(replace(replace(industry_text,E'\\',E'\\\\'),'%',E'\\%'),'_',E'\\_');
 region_text:=replace(replace(replace(region_text,E'\\',E'\\\\'),'%',E'\\%'),'_',E'\\_');
 if search_text<>'' then
  source_where:=source_where||format(' and e.company_name ilike %L','%'||search_text||'%');
  prospect_where:=prospect_where||format(' and p.company_name ilike %L','%'||search_text||'%'); end if;
 if region_text<>'' then
  source_where:=source_where||format(' and (e.province_code=%L or e.province ilike %L or e.address ilike %L)',region_text,region_text||'%',region_text||'%');
  prospect_where:=prospect_where||format(' and (p.region ilike %L or p.address ilike %L)',region_text||'%',region_text||'%'); end if;
 if industry_text<>'' then
  source_where:=source_where||format(' and (e.industry_name ilike %L or e.industry_category ilike %L)','%'||industry_text||'%','%'||industry_text||'%');
  prospect_where:=prospect_where||format(' and p.industry_name ilike %L','%'||industry_text||'%'); end if;
 if business_filter<>'all' then
  source_where:=source_where||format(' and public.oasis_voice_business_type(e.business_no)=%L',business_filter);
  prospect_where:=prospect_where||format(' and public.oasis_voice_business_type(p.business_no)=%L',business_filter); end if;
 if phone_filter='mobile' then source_where:=source_where||' and e.has_mobile_phone'; prospect_where:=prospect_where||' and pc.mobile is not null';
 elsif phone_filter='landline' then source_where:=source_where||' and e.has_landline_phone'; prospect_where:=prospect_where||' and pc.landline is not null';
 elsif phone_filter='both' then source_where:=source_where||' and e.has_mobile_phone and e.has_landline_phone'; prospect_where:=prospect_where||' and pc.mobile is not null and pc.landline is not null';
 elsif phone_filter='none' then source_where:=source_where||' and not e.has_mobile_phone and not e.has_landline_phone'; prospect_where:=prospect_where||' and pc.mobile is null and pc.landline is null'; end if;
 if discovery_filter<>'all' then
  source_where:=source_where||format(' and public.oasis_voice_discovery_type(e.is_new_company,e.opening_signal_basis,e.employee_growth,e.current_period,e.source_type)=%L',discovery_filter);
  -- Saved prospects alone do not prove a dated growth/opening signal.
  if discovery_filter<>'unknown' then prospect_where:=prospect_where||' and false'; end if;
 end if;
 if source_cursor='employment' then
  if key_cursor<>'' then source_where:=source_where||format(' and e.contact_key>%L',key_cursor); end if;
  -- Bound the source page BEFORE connecting any prospects/assignments/permissions.
  statement:=format($sql$
   with page as materialized (
    select e.contact_key,e.source_type,e.source_record_key,e.business_no,e.company_name,e.address,
     e.province,e.district,e.industry_name,e.industry_category,e.current_employee_count,e.employee_growth,e.previous_period,e.current_period,
     e.is_new_company,e.opening_signal_basis,e.mobile_phone,e.landline_phone
    from public.oasis_employment_contacts e where %s order by e.contact_key limit %s
   ), links as (
    select e.contact_key,jsonb_agg(distinct p.company_uid) filter(where public.oasis_is_valid_company_uid(p.company_uid)) uids,
     bool_or((e.business_no ~ '^[0-9]{10}$' and (
      (regexp_replace(p.business_no,'[^0-9]','','g') ~ '^[0-9]{10}$' and regexp_replace(p.business_no,'[^0-9]','','g')<>e.business_no)
      or (p.company_uid ~ '^business:[0-9]{10}$' and p.company_uid<>'business:'||e.business_no)))
      or (regexp_replace(p.business_no,'[^0-9]','','g') ~ '^[0-9]{10}$' and p.company_uid ~ '^business:[0-9]{10}$'
       and p.company_uid<>'business:'||regexp_replace(p.business_no,'[^0-9]','','g'))) identity_conflict
    from page e left join public.oasis_prospect_companies p on
     (e.business_no ~ '^[0-9]{10}$' and regexp_replace(p.business_no,'[^0-9]','','g')=e.business_no)
     or (nullif(btrim(e.source_type),'') is not null and nullif(btrim(e.source_record_key),'') is not null and p.source=e.source_type
      and p.source_key in (e.source_record_key,e.source_type||':'||e.source_record_key,'recent_opening:'||e.source_type||':'||e.source_record_key))
     or p.source_data->>'contact_key'=e.contact_key
    group by e.contact_key
   )
   select coalesce(jsonb_agg(jsonb_build_object('source','employment','key',e.contact_key,'row_id','employment:'||md5(e.contact_key),
    'company_name',e.company_name,'address',e.address,'region',concat_ws(' ',nullif(e.province,''),nullif(e.district,'')),
    'industry',coalesce(nullif(e.industry_name,''),nullif(e.industry_category,''),''),'business_type',public.oasis_voice_business_type(e.business_no),
    'employee_count',case when e.current_employee_count>0 or nullif(e.current_period,'') is not null then e.current_employee_count else null end,
    'employment_change',case when nullif(e.current_period,'') is not null and nullif(e.previous_period,'') is not null then e.employee_growth else null end,
    'previous_period',e.previous_period,'current_period',e.current_period,
    'discovery_type',public.oasis_voice_discovery_type(e.is_new_company,e.opening_signal_basis,e.employee_growth,e.current_period,e.source_type),
    'discovery_basis',e.opening_signal_basis,'mobile',public.oasis_voice_phone(e.mobile_phone),'landline',public.oasis_voice_phone(e.landline_phone),
    'uids',coalesce(l.uids,'[]'::jsonb),'identity_conflict',coalesce(l.identity_conflict,false)) order by e.contact_key),'[]'::jsonb)
   from page e join links l using(contact_key)
  $sql$,source_where,lim+1);
  execute statement into source_page;
 end if;
 -- Only visit the small supplemental table when the source page is exhausted.
 -- Source identity is resolved by strong business number, explicit source key,
 -- or the existing normalized name+address PLACE cache key (not company name alone).
 if jsonb_array_length(source_page)<=lim then
  if source_cursor='prospect' then prospect_where:=prospect_where||format(' and p.id>%L::uuid',key_cursor); end if;
  statement:=format($sql$
   select coalesce(jsonb_agg(to_jsonb(q) order by q.key),'[]'::jsonb) from (
    select 'prospect'::text source,p.id::text key,'prospect:'||p.id::text row_id,p.company_name,p.address,p.region,p.industry_name industry,
     public.oasis_voice_business_type(p.business_no) business_type,p.employee_count,null::integer employment_change,
     ''::text previous_period,''::text current_period,'unknown'::text discovery_type,''::text discovery_basis,
     pc.mobile,pc.landline,case when public.oasis_is_valid_company_uid(p.company_uid) then jsonb_build_array(p.company_uid) else '[]'::jsonb end uids,
     coalesce(regexp_replace(p.business_no,'[^0-9]','','g') ~ '^[0-9]{10}$' and p.company_uid ~ '^business:[0-9]{10}$'
      and p.company_uid<>'business:'||regexp_replace(p.business_no,'[^0-9]','','g'),false) identity_conflict
    from public.oasis_prospect_companies p
    left join lateral (select min(public.oasis_voice_phone(c.contact_value)) filter(where public.oasis_voice_phone(c.contact_value) like '+821%%') mobile,
     min(public.oasis_voice_phone(c.contact_value)) filter(where public.oasis_voice_phone(c.contact_value) not like '+821%%') landline
     from public.oasis_prospect_contacts c where c.prospect_id=p.id and c.contact_type in ('phone','mobile','landline','mobile_phone','landline_phone') and c.verification_status<>'rejected') pc on true
    where %s and not exists(select 1 from public.oasis_employment_contacts e where e.contact_key=any(array[
     case when regexp_replace(p.business_no,'[^0-9]','','g') ~ '^[0-9]{10}$' then 'business:'||regexp_replace(p.business_no,'[^0-9]','','g') end,
     nullif(p.source_data->>'contact_key',''),nullif(p.company_uid,''),nullif(p.source_key,''),
     case when nullif(regexp_replace(lower(p.company_name),'[^0-9a-z가-힣]','','g'),'') is not null and nullif(regexp_replace(lower(p.address),'[^0-9a-z가-힣]','','g'),'') is not null
      then 'place:'||md5(regexp_replace(lower(p.company_name),'[^0-9a-z가-힣]','','g')||'|'||regexp_replace(lower(p.address),'[^0-9a-z가-힣]','','g')) end])
     and not (e.business_no ~ '^[0-9]{10}$' and regexp_replace(coalesce(p.business_no,''),'[^0-9]','','g') ~ '^[0-9]{10}$'
      and e.business_no<>regexp_replace(p.business_no,'[^0-9]','','g')))
    order by p.id limit %s
   ) q
  $sql$,prospect_where,lim+1-jsonb_array_length(source_page));
  execute statement into prospect_page;
 end if;
 has_more:=jsonb_array_length(source_page)+jsonb_array_length(prospect_page)>lim;
 for r in select value from jsonb_array_elements(source_page||prospect_page) loop
  n:=n+1; if n>lim then exit; end if;
  matched_uids:=r->'uids'; uid_count:=jsonb_array_length(matched_uids);
  uid:=case when uid_count=1 and not coalesce((r->>'identity_conflict')::boolean,false) then matched_uids->>0 else null end;
  target:=case when uid is not null then public.oasis_voice_target(uid) else null end;
  blocked:=false; valid_permission:=false;
  if target is not null then
   blocked:=public.oasis_voice_blocked(uid,target->>'phone_e164');
   select exists(select 1 from public.oasis_voice_permissions p where p.company_uid=uid and p.phone_e164=target->>'phone_e164'
    and p.revoked_at is null and p.granted_at<=now() and p.expires_at>now()) into valid_permission;
  end if;
  blocked_reason:=case when uid_count>1 or coalesce((r->>'identity_conflict')::boolean,false) then 'IDENTITY_CONFLICT' when uid is null then 'SOURCE_NOT_LINKED'
   when target is null then 'TARGET_NOT_READY' when blocked then 'DO_NOT_CALL' when not valid_permission then 'CONSENT_REQUIRED' else null end;
  rows_out:=rows_out||jsonb_build_array((r-'source'-'key'-'uids'-'mobile'-'landline'-'identity_conflict')||jsonb_build_object(
   'company_uid',uid,'business_type_basis',case when r->>'business_type'='unknown' then 'unverified' else 'nts_registration_code' end,
   'mobile_phone_masked',case when nullif(r->>'mobile','') is not null then '***-****-'||right(r->>'mobile',4) else '' end,
   'landline_phone_masked',case when nullif(r->>'landline','') is not null then '***-****-'||right(r->>'landline',4) else '' end,
   'phone_masked',case when target is not null then '***-****-'||right(target->>'phone_e164',4) else '' end,
   'contact_id',target->>'contact_id','do_not_call',case when target is not null then blocked else null end,
   'permission_valid',valid_permission,'eligible',blocked_reason is null,'blocked_reason',blocked_reason));
  next_value:=jsonb_build_object('source',r->>'source','key',r->>'key','fingerprint',fingerprint);
 end loop;
 return jsonb_build_object('ok',true,'rows',rows_out,'has_more',has_more,'next_cursor',case when has_more then next_value else null end);
exception when query_canceled then return jsonb_build_object('ok',false,'code','SEARCH_TIMEOUT');
 when invalid_text_representation or numeric_value_out_of_range then return jsonb_build_object('ok',false,'code','INVALID_INPUT');
 when raise_exception then return jsonb_build_object('ok',false,'code',case when sqlerrm='INVALID_CURSOR' then 'INVALID_CURSOR' else 'INVALID_INPUT' end);
end $catalog$;
revoke all on function public.oasis_voice_catalog(text,jsonb) from public,anon,authenticated;
grant execute on function public.oasis_voice_catalog(text,jsonb) to service_role;
commit;
