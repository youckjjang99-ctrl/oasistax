-- v9.14.3: isolated hosted PostgreSQL verification passed on 2026-10-07.
-- Only an explicitly approved EMPTY schema-only staging project is permitted.
-- Apply base voice + carrier + campaigns + admin catalog migrations first;
-- apply the NEW admin catalog migration a second time, then run this whole file.
-- Never run in production. Synthetic inserts are rolled back; no provider/worker RPC.
begin;
set local statement_timeout='90s';
set local lock_timeout='5s';
do $empty_guard$
declare relation_name text; occupied boolean;
begin
 foreach relation_name in array array['oasis_users','oasis_customers','oasis_crm','oasis_employment_contacts',
  'oasis_prospect_companies','oasis_prospect_contacts','oasis_company_sales_assignments',
  'oasis_voice_permissions','oasis_voice_jobs','oasis_voice_events','oasis_voice_campaigns'] loop
  if to_regclass('public.'||relation_name) is null then raise exception 'CATALOG_STAGING_MISSING_TABLE'; end if;
  execute format('select exists(select 1 from public.%I)',relation_name) into occupied;
  if occupied then raise exception 'CATALOG_STAGING_REQUIRES_EMPTY_DATABASE'; end if;
 end loop;
end $empty_guard$;
set local role service_role;
do $verify$
declare
 admin_actor constant text:='synthetic-catalog-admin'; member_actor constant text:='synthetic-catalog-member';
 -- Construct a synthetic all-zero subscriber number; never dial or import a real contact.
 synthetic_mobile_phone constant text:='010'||repeat('0',8);
 response jsonb; cursor_value jsonb; item jsonb; source_before text; source_after text; before_assignments bigint;
 before_permissions bigint; before_jobs bigint; before_events bigint; all_ids text[]:='{}'; action_name text;
 client_role text; signature text; n integer:=0; pages integer:=0; checks integer:=0;
begin
 insert into public.oasis_users(user_id,name,role,status,created_at,approved_at,approved_by) values
  (admin_actor,'Synthetic catalog admin','admin','approved',now()::text,now()::text,admin_actor),
  (member_actor,'Synthetic catalog member','member','approved',now()::text,now()::text,admin_actor);
 insert into public.oasis_employment_contacts(contact_key,source_type,source_record_key,business_no,company_name,address,
  province,district,province_code,industry_name,industry_category,current_employee_count,employee_growth,
  previous_period,current_period,mobile_phone,has_mobile_phone)
 select 'business:00081'||lpad(i::text,5,'0'),'nps_monthly','synthetic-catalog-'||i,'00081'||lpad(i::text,5,'0'),
  'Synthetic catalog company '||i,'Synthetic catalog address','Synthetic province','Synthetic district','ZZ',
  'Synthetic industry','Synthetic category',10,1,'2024','2025',synthetic_mobile_phone,true from generate_series(1,205) i;
 select md5(jsonb_agg(to_jsonb(e) order by contact_key)::text) into source_before from public.oasis_employment_contacts e;
 select count(*) into before_assignments from public.oasis_company_sales_assignments;
 select count(*) into before_permissions from public.oasis_voice_permissions;
 select count(*) into before_jobs from public.oasis_voice_jobs;
 select count(*) into before_events from public.oasis_voice_events;
 foreach client_role in array array['anon','authenticated'] loop
  foreach signature in array array['public.oasis_voice_catalog(text,jsonb)','public.oasis_voice_action(text,text,jsonb)',
   'public.oasis_voice_campaign_action(text,text,jsonb)','public.oasis_voice_business_type(text)',
   'public.oasis_voice_discovery_type(boolean,text,integer,text,text)'] loop
   if has_function_privilege(client_role,signature,'EXECUTE') then raise exception 'CATALOG_CLIENT_RPC_PRIVILEGE_LEAK'; end if;
   checks:=checks+1;
  end loop;
 end loop;
 response:=public.oasis_voice_catalog(member_actor,'{}');
 if response->>'code' is distinct from 'NOT_AUTHORIZED' then raise exception 'CATALOG_MEMBER_READ_NOT_BLOCKED'; end if;
 checks:=checks+1;
 foreach action_name in array array['candidates','list_jobs','list_permissions','grant_permission','enqueue','approve','suppress','cancel'] loop
  response:=public.oasis_voice_action(member_actor,action_name,'{}');
  if response->>'code' is distinct from 'NOT_AUTHORIZED' then raise exception 'CATALOG_MEMBER_LEGACY_NOT_BLOCKED'; end if;
  checks:=checks+1;
 end loop;
 foreach action_name in array array['campaign_stats','list_campaigns','campaign_jobs','legacy_jobs','create_campaign','start_campaign','pause_campaign','cancel_campaign'] loop
  response:=public.oasis_voice_campaign_action(member_actor,action_name,'{}');
  if response->>'code' is distinct from 'NOT_AUTHORIZED' then raise exception 'CATALOG_MEMBER_CAMPAIGN_NOT_BLOCKED'; end if;
  checks:=checks+1;
 end loop;
 loop
  response:=public.oasis_voice_catalog(admin_actor,jsonb_build_object('limit',100,'cursor',cursor_value));
  if response->>'ok' is distinct from 'true' then raise exception 'CATALOG_ADMIN_PAGE_FAILED'; end if;
  pages:=pages+1;
  if pages>3 or jsonb_array_length(response->'rows')<>(case when pages<3 then 100 else 5 end) then raise exception 'CATALOG_PAGE_SIZE_INVALID'; end if;
  for item in select value from jsonb_array_elements(response->'rows') loop
   if item->>'row_id'=any(all_ids) then raise exception 'CATALOG_PAGE_DUPLICATE'; end if;
   all_ids:=array_append(all_ids,item->>'row_id'); n:=n+1;
   if item->>'company_uid' is not null or item->>'eligible' is distinct from 'false'
    or item->>'blocked_reason' is distinct from 'SOURCE_NOT_LINKED' then raise exception 'CATALOG_RAW_SOURCE_BECAME_CALL_TARGET'; end if;
   if item ? 'phone_e164' or item ? 'mobile_phone' or item ? 'landline_phone' or item ? 'business_no' then raise exception 'CATALOG_RAW_CONTACT_EXPOSED'; end if;
  end loop;
  exit when response->>'has_more'='false';
  cursor_value:=response->'next_cursor';
 end loop;
 if n<>205 or response->'next_cursor'<>'null'::jsonb then raise exception 'CATALOG_PAGINATION_INCOMPLETE'; end if;
 checks:=checks+5;
 response:=public.oasis_voice_catalog(admin_actor,jsonb_build_object('cursor',cursor_value,'phone_type','none'));
 if response->>'code' is distinct from 'INVALID_CURSOR' then raise exception 'CATALOG_FILTER_CURSOR_NOT_INVALIDATED'; end if;
 checks:=checks+1;
 response:=public.oasis_voice_catalog(admin_actor,'{"phone_type":"none"}');
 if jsonb_array_length(response->'rows')<>0 then raise exception 'CATALOG_PHONE_FILTER_FAILED'; end if;
 checks:=checks+1;
 response:=public.oasis_voice_catalog(admin_actor,'{"query":"%"}');
 if jsonb_array_length(response->'rows')<>0 then raise exception 'CATALOG_WILDCARD_NOT_ESCAPED'; end if;
 checks:=checks+1;
 response:=public.oasis_voice_catalog(admin_actor,'{"business_type":"corporate"}');
 if jsonb_array_length(response->'rows')<>100 then raise exception 'CATALOG_CORPORATE_FILTER_FAILED'; end if;
 checks:=checks+1;
 select md5(jsonb_agg(to_jsonb(e) order by contact_key)::text) into source_after from public.oasis_employment_contacts e;
 if source_after is distinct from source_before or (select count(*) from public.oasis_company_sales_assignments)<>before_assignments
  or (select count(*) from public.oasis_voice_permissions)<>before_permissions or (select count(*) from public.oasis_voice_jobs)<>before_jobs
  or (select count(*) from public.oasis_voice_events)<>before_events then raise exception 'CATALOG_READ_HAS_SIDE_EFFECTS'; end if;
 checks:=checks+1;
 update public.oasis_users set role='member' where user_id=admin_actor;
 response:=public.oasis_voice_catalog(admin_actor,'{}');
 if response->>'code' is distinct from 'NOT_AUTHORIZED' then raise exception 'CATALOG_ADMIN_DOWNGRADE_NOT_ENFORCED'; end if;
 checks:=checks+1;
 raise notice 'PASS: % hosted staging assertions; 205 synthetic rows across 3 pages; rollback follows.',checks;
end $verify$;
rollback;
