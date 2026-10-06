-- Run only against the operator-approved EMPTY staging branch containing the
-- actual application schema plus BOTH carrier and bulk-campaign migrations. Never use production.
-- No replacement functions, mock schemas, provider requests, or worker claims.
-- Execute this whole file on one connection; any error is a failed verification.
-- All synthetic rows roll back. PostgreSQL sequence increments do not roll back.
begin;
set local statement_timeout = '90s';
set local lock_timeout = '5s';

do $empty_staging_guard$
declare
    relation_name text;
    has_rows boolean;
begin
    -- Fail before inserting anything if a schema-only copy contains user data.
    foreach relation_name in array array[
        'oasis_users', 'oasis_customers', 'oasis_crm',
        'oasis_prospect_companies', 'oasis_prospect_contacts',
        'oasis_company_sales_assignments', 'oasis_company_sales_contact_logs',
        'oasis_voice_permissions', 'oasis_voice_suppressions',
        'oasis_voice_jobs', 'oasis_voice_events', 'oasis_voice_campaigns'
    ] loop
        if to_regclass('public.' || relation_name) is null then
            raise exception 'STAGING_GUARD_MISSING_TABLE: %', relation_name;
        end if;
        execute format('select exists(select 1 from public.%I)', relation_name)
            into has_rows;
        if has_rows then
            raise exception 'STAGING_GUARD_REQUIRES_EMPTY_TABLE: %', relation_name;
        end if;
    end loop;
end;
$empty_staging_guard$;

-- Test the same database role used by the real server-side repository.
set local role service_role;

do $real_schema_bulk_verification$
declare
    admin_actor constant text := 'synthetic-voice-stage-admin';
    owner_a constant text := 'synthetic-voice-stage-owner-a';
    owner_b constant text := 'synthetic-voice-stage-owner-b';
    business_a text := repeat('0', 9) || '1';
    business_b text := repeat('0', 9) || '2';
    business_c text := repeat('0', 9) || '3';
    phone_a text := '010' || repeat('0', 7) || '1';
    phone_b text := '010' || repeat('0', 7) || '2';
    phone_c text := '010' || repeat('0', 7) || '3';
    uid_a text;
    uid_b text;
    uid_c text;
    prospect_a uuid;
    prospect_b uuid;
    prospect_c uuid;
    assignment_a uuid;
    job_a uuid;
    job_c uuid;
    synthetic_call_id text := 'clawops:CAstaging_bulk_12345678';
    campaign_a uuid;
    campaign_b uuid;
    nonce_a uuid:=gen_random_uuid();
    created_result jsonb;
    result jsonb;
    consent jsonb;
    visit_payload jsonb;
    visit_day date := (now() at time zone 'Asia/Seoul')::date + 1;
    next_visit timestamptz;
    relation_name text;
    client_role text;
    privilege_name text;
    function_identity text;
begin
    if current_user <> 'service_role' then
        raise exception 'SERVER_ROLE_NOT_ACTIVE';
    end if;

    foreach client_role in array array['anon', 'authenticated'] loop
        foreach relation_name in array array[
            'oasis_voice_permissions', 'oasis_voice_suppressions',
            'oasis_voice_jobs', 'oasis_voice_events', 'oasis_voice_campaigns'
        ] loop
            foreach privilege_name in array array['SELECT', 'INSERT', 'UPDATE', 'DELETE'] loop
                if has_table_privilege(client_role, 'public.' || relation_name, privilege_name) then
                    raise exception 'CLIENT_TABLE_PRIVILEGE_LEAK: % % %',
                        client_role, relation_name, privilege_name;
                end if;
            end loop;
            if not (select c.relrowsecurity from pg_class c
                    where c.oid = to_regclass('public.' || relation_name)) then
                raise exception 'VOICE_RLS_DISABLED: %', relation_name;
            end if;
        end loop;
        foreach function_identity in array array[
            'public.oasis_voice_phone(text)',
            'public.oasis_voice_target(text)',
            'public.oasis_voice_blocked(text,text)',
            'public.oasis_voice_action(text,text,jsonb)',
            'public.oasis_voice_worker(text,jsonb)',
            'public.oasis_voice_campaign_action(text,text,jsonb)'
        ] loop
            if has_function_privilege(client_role, function_identity, 'EXECUTE') then
                raise exception 'CLIENT_RPC_PRIVILEGE_LEAK: % %', client_role, function_identity;
            end if;
        end loop;
    end loop;
    if has_table_privilege('service_role', 'public.oasis_voice_jobs', 'DELETE')
       or has_table_privilege('service_role', 'public.oasis_voice_events', 'DELETE')
       or has_table_privilege('service_role', 'public.oasis_voice_events', 'UPDATE') then
        raise exception 'VOICE_HISTORY_NOT_APPEND_ONLY';
    end if;

    insert into public.oasis_users(user_id, name, role, status, created_at, approved_at, approved_by)
    values
        (admin_actor, 'Synthetic staging admin', 'admin', 'approved', now()::text, now()::text, admin_actor),
        (owner_a, 'Synthetic staging owner A', 'member', 'approved', now()::text, now()::text, admin_actor),
        (owner_b, 'Synthetic staging owner B', 'member', 'approved', now()::text, now()::text, admin_actor);

    uid_a := public.oasis_make_company_uid(business_a, null, null, null, null, null, null, null);
    uid_b := public.oasis_make_company_uid(business_b, null, null, null, null, null, null, null);
    uid_c := public.oasis_make_company_uid(business_c, null, null, null, null, null, null, null);
    insert into public.oasis_prospect_companies(
        source, source_key, business_no, company_uid, company_name, address, owner_user_id, source_data
    ) values (
        'nps_workplace_v2', 'synthetic-voice-stage-a', business_a, uid_a,
        'Synthetic staging company A', 'Synthetic staging location A', owner_a,
        jsonb_build_object('synthetic_fixture', true)
    ) returning id into prospect_a;
    insert into public.oasis_prospect_companies(
        source, source_key, business_no, company_uid, company_name, address, owner_user_id, source_data
    ) values (
        'nps_workplace_v2', 'synthetic-voice-stage-b', business_b, uid_b,
        'Synthetic staging company B', 'Synthetic staging location B', owner_b,
        jsonb_build_object('synthetic_fixture', true)
    ) returning id into prospect_b;
    insert into public.oasis_prospect_companies(
        source, source_key, business_no, company_uid, company_name, address, owner_user_id, source_data
    ) values (
        'nps_workplace_v2', 'synthetic-voice-stage-c', business_c, uid_c,
        'Synthetic staging company C', 'Synthetic staging location C', owner_a,
        jsonb_build_object('synthetic_fixture', true)
    ) returning id into prospect_c;

    insert into public.oasis_prospect_contacts(
        prospect_id, contact_type, contact_value, source_type, confidence,
        verification_status, is_primary, owner_user_id, metadata
    ) values
        (prospect_a, 'phone', phone_a, 'synthetic_staging', 100, 'manual_verified', true, owner_a, '{"synthetic_fixture":true}'),
        (prospect_b, 'phone', phone_b, 'synthetic_staging', 100, 'manual_verified', true, owner_b, '{"synthetic_fixture":true}'),
        (prospect_c, 'phone', phone_c, 'synthetic_staging', 100, 'manual_verified', true, owner_a, '{"synthetic_fixture":true}');

    insert into public.oasis_company_sales_assignments(
        company_id, company_uid, assigned_user_id, status, assigned_at,
        assignment_expires_at, first_assigned_by_user_id, first_assigned_at
    ) values
        (prospect_a, uid_a, owner_a, 'assigned', now(), now() + interval '72 hours', admin_actor, now()),
        (prospect_b, uid_b, owner_b, 'assigned', now(), now() + interval '72 hours', admin_actor, now()),
        (prospect_c, uid_c, owner_a, 'assigned', now(), now() + interval '72 hours', admin_actor, now());
    select a.id into strict assignment_a from public.oasis_company_sales_assignments a where a.company_uid = uid_a;


    if has_table_privilege('service_role','public.oasis_voice_campaigns','DELETE') then
        raise exception 'CAMPAIGN_HISTORY_DELETE_GRANTED';
    end if;
    if exists(select 1 from pg_proc where oid='public.oasis_voice_campaign_action(text,text,jsonb)'::regprocedure and prosecdef)
       or exists(select 1 from pg_proc where oid='public.oasis_voice_worker(text,jsonb)'::regprocedure and prosecdef) then
        raise exception 'CAMPAIGN_RPC_NOT_INVOKER';
    end if;
    if not exists(select 1 from pg_proc where oid='public.oasis_voice_campaign_action(text,text,jsonb)'::regprocedure
                  and proconfig @> array['search_path=""']) then
        raise exception 'CAMPAIGN_RPC_SEARCH_PATH_NOT_EMPTY';
    end if;
    result:=public.oasis_voice_campaign_action(owner_a,'create_campaign',
        jsonb_build_object('name','Synthetic no consent','request_id','synthetic-no-consent','company_uids',jsonb_build_array(uid_a)));
    if (result->>'ok')::boolean is distinct from true
       or (result->>'created_count')::int is distinct from 0
       or result #>> '{skipped,0,code}' is distinct from 'CONSENT_REQUIRED' then
        raise exception 'BULK_NO_IMPLICIT_CONSENT_FAILED';
    end if;
    consent:=jsonb_build_object('kind','explicit_consent','evidence_ref','synthetic-staging-consent-not-real',
        'granted_at',now()-interval '1 hour','expires_at',now()+interval '30 days');
    result:=public.oasis_voice_action(owner_a,'grant_permission',
        consent||jsonb_build_object('company_uids',jsonb_build_array(uid_a)));
    if (result->>'ok')::boolean is distinct from true then raise exception 'BULK_CONSENT_FIXTURE_FAILED'; end if;
    result:=public.oasis_voice_campaign_action(owner_a,'create_campaign',
        jsonb_build_object('name','Synthetic campaign A','request_id','synthetic-campaign-a','company_uids',jsonb_build_array(uid_a,uid_b,uid_c)));
    created_result:=result;
    campaign_a:=(result->>'campaign_id')::uuid;
    if (result->>'ok')::boolean is distinct from true or campaign_a is null
       or (result->>'created_count')::int is distinct from 1 or (result->>'skipped_count')::int is distinct from 2 then
        raise exception 'BULK_PARTIAL_CREATION_FAILED';
    end if;
    if not exists(select 1 from jsonb_array_elements(result->'skipped') x where x->>'code'='NOT_AUTHORIZED')
       or not exists(select 1 from jsonb_array_elements(result->'skipped') x where x->>'code'='CONSENT_REQUIRED') then
        raise exception 'BULK_SAFE_EXCLUSION_REASONS_FAILED';
    end if;
    select id into strict job_a from public.oasis_voice_jobs where campaign_id=campaign_a;
    result:=public.oasis_voice_campaign_action(owner_a,'create_campaign',
        jsonb_build_object('name','Changed name','request_id','synthetic-campaign-a','company_uids',jsonb_build_array(uid_c)));
    if (result->>'campaign_id')::uuid is distinct from campaign_a
       or (result->>'created_count')::int is distinct from 1
       or result->'skipped' is distinct from created_result->'skipped'
       or (select count(*) from public.oasis_voice_jobs)<>1 then
        raise exception 'BULK_IDEMPOTENCY_FAILED';
    end if;
    result:=public.oasis_voice_campaign_action(owner_b,'campaign_jobs',jsonb_build_object('campaign_id',campaign_a));
    if result->>'code' is distinct from 'NOT_AUTHORIZED' then raise exception 'BULK_OWNER_ISOLATION_FAILED'; end if;
    result:=public.oasis_voice_campaign_action(owner_a,'campaign_jobs',jsonb_build_object('campaign_id',campaign_a));
    if jsonb_array_length(result->'rows') is distinct from 1
       or result #>> '{rows,0,company_uid}' is distinct from uid_a
       or (result->'rows'->0) ? 'phone_e164' then raise exception 'BULK_MASKED_DETAIL_FAILED'; end if;
    result:=public.oasis_voice_campaign_action(owner_a,'legacy_jobs','{}');
    if jsonb_array_length(result->'rows') is distinct from 0 then raise exception 'LEGACY_QUEUE_MIXED_WITH_CAMPAIGN'; end if;
    result:=public.oasis_voice_campaign_action(owner_b,'campaign_stats','{}');
    if (result #>> '{metrics,total}')::int is distinct from 0 then raise exception 'BULK_STATS_OWNER_LEAK'; end if;
    result:=public.oasis_voice_campaign_action(owner_a,'start_campaign',jsonb_build_object('campaign_id',campaign_a));
    if result->>'code' is distinct from 'NOT_AUTHORIZED' then raise exception 'BULK_ADMIN_START_GUARD_FAILED'; end if;
    result:=public.oasis_voice_campaign_action(admin_actor,'start_campaign',jsonb_build_object('campaign_id',campaign_a));
    if (result->>'approved_count')::int is distinct from 1 then raise exception 'BULK_ADMIN_START_FAILED'; end if;
    result:=public.oasis_voice_campaign_action(owner_a,'pause_campaign',jsonb_build_object('campaign_id',campaign_a));
    if (result->>'ok')::boolean is distinct from true then raise exception 'BULK_OWNER_PAUSE_FAILED'; end if;

    -- Only our own synthetic stored state is simulated. No claim RPC or provider I/O.
    update public.oasis_voice_jobs set status='dispatching',provider='clawops',dispatch_at=now(),dispatch_nonce=nonce_a
      where id=job_a and status='approved';
    if not found then raise exception 'BULK_SYNTHETIC_DISPATCH_STATE_FAILED'; end if;
    result:=public.oasis_voice_worker('get_job',jsonb_build_object('job_id',job_a,'provider','clawops'));
    if result->>'code' is distinct from 'CAMPAIGN_NOT_RUNNING' then raise exception 'BULK_PAUSE_PREFLIGHT_FAILED'; end if;
    result:=public.oasis_voice_worker('connect',
        jsonb_build_object('job_id',job_a,'provider_call_id',synthetic_call_id,'dispatch_nonce',nonce_a));
    if result->>'code' is distinct from 'CAMPAIGN_NOT_RUNNING' then raise exception 'BULK_PAUSE_MEDIA_GUARD_FAILED'; end if;
    result:=public.oasis_voice_campaign_action(admin_actor,'start_campaign',jsonb_build_object('campaign_id',campaign_a));
    if (result->>'ok')::boolean is distinct from true then raise exception 'BULK_RESUME_FAILED'; end if;
    result:=public.oasis_voice_worker('connect',
        jsonb_build_object('job_id',job_a,'provider_call_id','CA'||repeat('b',32),'dispatch_nonce',nonce_a));
    if result->>'code' is distinct from 'PROVIDER_MISMATCH' then raise exception 'CARRIER_ISOLATION_FAILED'; end if;
    result:=public.oasis_voice_worker('get_job',
        jsonb_build_object('job_id',job_a,'provider_call_id',synthetic_call_id,'provider','clawops'));
    if (result->>'ok')::boolean is distinct from true then raise exception 'CLAWOPS_PREFLIGHT_FAILED'; end if;
    result:=public.oasis_voice_worker('connect',
        jsonb_build_object('job_id',job_a,'provider_call_id',synthetic_call_id,'dispatch_nonce',nonce_a));
    if (result->>'ok')::boolean is distinct from true then raise exception 'CLAWOPS_CONNECT_FAILED'; end if;
    result:=public.oasis_voice_worker('connect',
        jsonb_build_object('job_id',job_a,'provider_call_id',synthetic_call_id,'dispatch_nonce',nonce_a));
    if result->>'code' is distinct from 'PROVIDER_MISMATCH' then raise exception 'CLAWOPS_REPLAY_GUARD_FAILED'; end if;
    result:=public.oasis_voice_campaign_action(admin_actor,'cancel_campaign',jsonb_build_object('campaign_id',campaign_a));
    if (result->>'cancelled_count')::int is distinct from 0
       or (select status from public.oasis_voice_jobs where id=job_a) is distinct from 'in_progress' then
        raise exception 'BULK_CANCEL_CHANGED_ACTIVE_CALL';
    end if;

    while extract(isodow from visit_day)>5 loop visit_day:=visit_day+1; end loop;
    next_visit:=(visit_day+time '10:00') at time zone 'Asia/Seoul';
    visit_payload:=jsonb_build_object('job_id',job_a,'provider_call_id',synthetic_call_id,
        'outcome','visit_requested','customer_confirmed',true,
        'visit_at',to_char(next_visit at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"'),
        'address','Synthetic staging visit location','summary','Synthetic staging visit request');
    result:=public.oasis_voice_worker('result',visit_payload);
    if (result->>'ok')::boolean is distinct from true
       or not exists(select 1 from public.oasis_voice_jobs where id=job_a and contact_recorded_at is not null
                     and outcome='visit_requested' and safe_error_code is null)
       or (select count(*) from public.oasis_company_sales_contact_logs where assignment_id=assignment_a)<>1 then
        raise exception 'BULK_REAL_CONTACT_RPC_FAILED';
    end if;
    result:=public.oasis_voice_worker('result',visit_payload);
    if result->>'code' is distinct from 'DUPLICATE'
       or (select count(*) from public.oasis_company_sales_contact_logs where assignment_id=assignment_a)<>1 then
        raise exception 'BULK_REAL_CONTACT_IDEMPOTENCY_FAILED';
    end if;
    result:=public.oasis_voice_campaign_action(owner_a,'campaign_stats','{}');
    if (result #>> '{metrics,visit_requests}')::int is distinct from 1 then raise exception 'BULK_VISIT_STATS_FAILED'; end if;
    result:=public.oasis_voice_worker('status',jsonb_build_object('job_id',job_a,'provider_call_id',synthetic_call_id,
        'status','completed','sequence_number',3,'duration_seconds',75));
    if (result->>'ok')::boolean is distinct from true then raise exception 'BULK_TERMINAL_CALLBACK_FAILED'; end if;
    result:=public.oasis_voice_campaign_action(owner_a,'campaign_jobs',jsonb_build_object('campaign_id',campaign_a));
    if (result #>> '{rows,0,duration_seconds}')::int is distinct from 75 then raise exception 'BULK_DURATION_FAILED'; end if;

    -- Assignment transfer removes details and aggregate counts from the former owner.
    update public.oasis_company_sales_assignments set assigned_user_id=owner_b where id=assignment_a;
    result:=public.oasis_voice_campaign_action(owner_a,'campaign_jobs',jsonb_build_object('campaign_id',campaign_a));
    if jsonb_array_length(result->'rows') is distinct from 0 then raise exception 'BULK_FORMER_OWNER_DETAIL_LEAK'; end if;
    result:=public.oasis_voice_campaign_action(owner_a,'campaign_stats','{}');
    if (result #>> '{metrics,total}')::int is distinct from 0 then raise exception 'BULK_FORMER_OWNER_STATS_LEAK'; end if;
    result:=public.oasis_voice_campaign_action(owner_a,'cancel_campaign',jsonb_build_object('campaign_id',campaign_a));
    if result->>'code' is distinct from 'NOT_AUTHORIZED' then raise exception 'BULK_FORMER_OWNER_MUTATION_FAILED'; end if;
    result:=public.oasis_voice_campaign_action(admin_actor,'start_campaign',jsonb_build_object('campaign_id',campaign_a));
    if result->>'code' is distinct from 'INVALID_STATE' then raise exception 'BULK_CANCELLED_RESTART_ALLOWED'; end if;

    result:=public.oasis_voice_action(admin_actor,'grant_permission',
        consent||jsonb_build_object('company_uids',jsonb_build_array(uid_b,uid_c)));
    if (result->>'ok')::boolean is distinct from true then raise exception 'BULK_SECOND_FIXTURE_CONSENT_FAILED'; end if;
    result:=public.oasis_voice_campaign_action(admin_actor,'create_campaign',
        jsonb_build_object('name','Synthetic campaign B','request_id','synthetic-campaign-b','company_uids',jsonb_build_array(uid_b,uid_c)));
    campaign_b:=(result->>'campaign_id')::uuid;
    if (result->>'created_count')::int is distinct from 2 then raise exception 'BULK_ADMIN_MULTI_OWNER_FAILED'; end if;
    update public.oasis_prospect_contacts set contact_value='010'||repeat('0',6)||'99' where prospect_id=prospect_b;
    result:=public.oasis_voice_action(owner_a,'do_not_call',jsonb_build_object('company_uid',uid_c));
    if (result->>'ok')::boolean is distinct from true then raise exception 'BULK_DNC_FIXTURE_FAILED'; end if;
    result:=public.oasis_voice_campaign_action(admin_actor,'start_campaign',jsonb_build_object('campaign_id',campaign_b));
    if (result->>'approved_count')::int is distinct from 0 or (result->>'skipped_count')::int is distinct from 1
       or exists(select 1 from public.oasis_voice_jobs where campaign_id=campaign_b and status<>'cancelled') then
        raise exception 'BULK_PHONE_CHANGE_AND_DNC_FAILED';
    end if;
    result:=public.oasis_voice_campaign_action(owner_a,'create_campaign',
        jsonb_build_object('name','Synthetic suppressed','request_id','synthetic-suppressed','company_uids',jsonb_build_array(uid_c)));
    if result #>> '{skipped,0,code}' is distinct from 'DO_NOT_CALL' then raise exception 'BULK_DNC_RECREATE_ALLOWED'; end if;
    if (select count(*) from public.oasis_users)<>3
       or (select count(*) from public.oasis_prospect_companies)<>3
       or (select count(*) from public.oasis_voice_jobs)<>3 then raise exception 'BULK_FIXTURE_SCOPE_CHANGED'; end if;
    raise notice 'PASS: real-schema carrier and bulk campaign ACL, consent, partial enqueue, idempotency, ownership, pause/cancel, callback and CRM integration';
end;
$real_schema_bulk_verification$;

set local role anon;
do $anon_campaign_access$
begin
    begin
        perform public.oasis_voice_campaign_action('synthetic-voice-stage-admin','list_campaigns','{}');
        raise exception 'ANON_CAMPAIGN_RPC_WAS_ALLOWED';
    exception when insufficient_privilege then null;
    end;
    begin
        perform 1 from public.oasis_voice_campaigns;
        raise exception 'ANON_CAMPAIGN_READ_WAS_ALLOWED';
    exception when insufficient_privilege then null;
    end;
end;
$anon_campaign_access$;
set local role authenticated;
do $authenticated_campaign_access$
begin
    begin
        perform public.oasis_voice_campaign_action('synthetic-voice-stage-admin','start_campaign','{}');
        raise exception 'AUTHENTICATED_CAMPAIGN_RPC_WAS_ALLOWED';
    exception when insufficient_privilege then null;
    end;
    begin
        perform 1 from public.oasis_voice_campaigns;
        raise exception 'AUTHENTICATED_CAMPAIGN_READ_WAS_ALLOWED';
    exception when insufficient_privilege then null;
    end;
end;
$authenticated_campaign_access$;
reset role;
rollback;
select 'PASS: bulk staging assertions completed and all synthetic rows rolled back; no provider I/O or worker claims' as verification_result;
