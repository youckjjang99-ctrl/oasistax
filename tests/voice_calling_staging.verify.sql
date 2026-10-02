-- Run only against the operator-approved EMPTY staging branch containing the
-- actual application schema and the AI voice migration. Never use production.
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
        'oasis_voice_jobs', 'oasis_voice_events'
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

do $real_schema_voice_verification$
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
    synthetic_call_id text := 'CA' || repeat('a', 32);
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
            'oasis_voice_jobs', 'oasis_voice_events'
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
            'public.oasis_voice_worker(text,jsonb)'
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

    result := public.oasis_voice_action(owner_a, 'candidates', '{}');
    if (result->>'ok')::boolean is distinct from true
       or jsonb_array_length(result->'rows') is distinct from 2
       or exists(select 1 from jsonb_array_elements(result->'rows') r
                 where r->>'assigned_user_id' is distinct from owner_a or r ? 'phone_e164') then
        raise exception 'OWNER_A_CANDIDATE_ISOLATION_FAILED';
    end if;
    result := public.oasis_voice_action(owner_b, 'candidates', '{}');
    if (result->>'ok')::boolean is distinct from true
       or jsonb_array_length(result->'rows') is distinct from 1
       or result #>> '{rows,0,company_uid}' is distinct from uid_b then
        raise exception 'OWNER_B_CANDIDATE_ISOLATION_FAILED';
    end if;
    result := public.oasis_voice_action(admin_actor, 'candidates', '{}');
    if jsonb_array_length(result->'rows') is distinct from 3 then
        raise exception 'ADMIN_CANDIDATE_SCOPE_FAILED';
    end if;
    result := public.oasis_voice_action(owner_a, 'enqueue', jsonb_build_object(
        'company_uids', jsonb_build_array(uid_a), 'request_id', 'synthetic-no-consent'));
    if result->>'code' is distinct from 'CONSENT_REQUIRED' then
        raise exception 'CONSENT_REQUIREMENT_FAILED';
    end if;

    consent := jsonb_build_object('kind', 'explicit_consent',
        'evidence_ref', 'synthetic-staging-consent-not-real',
        'granted_at', now() - interval '1 hour', 'expires_at', now() + interval '30 days');
    result := public.oasis_voice_action(owner_a, 'grant_permission',
        consent || jsonb_build_object('company_uids', jsonb_build_array(uid_a, uid_b)));
    if result->>'code' is distinct from 'NOT_AUTHORIZED'
       or exists(select 1 from public.oasis_voice_permissions) then
        raise exception 'BATCH_OWNERSHIP_OR_ROLLBACK_FAILED';
    end if;
    result := public.oasis_voice_action(admin_actor, 'grant_permission',
        consent || jsonb_build_object('company_uids', jsonb_build_array(uid_a, uid_b, uid_c)));
    if (result->>'ok')::boolean is distinct from true
       or (select count(*) from public.oasis_voice_permissions) <> 3 then
        raise exception 'REAL_CONSENT_INSERT_FAILED';
    end if;
    result := public.oasis_voice_action(owner_a, 'candidates', '{}');
    if exists(select 1 from jsonb_array_elements(result->'rows') r
              where (r->>'eligible')::boolean is distinct from true) then
        raise exception 'CONSENT_ELIGIBILITY_FAILED';
    end if;
    result := public.oasis_voice_action(owner_b, 'list_permissions', '{}');
    if jsonb_array_length(result->'rows') is distinct from 1
       or result #>> '{rows,0,company_uid}' is distinct from uid_b then
        raise exception 'PERMISSION_OWNERSHIP_FAILED';
    end if;

    result := public.oasis_voice_action(owner_a, 'enqueue', jsonb_build_object(
        'company_uids', jsonb_build_array(uid_a), 'request_id', 'synthetic-stage-request-a'));
    if (result->>'ok')::boolean is distinct from true then raise exception 'ENQUEUE_FAILED'; end if;
    job_a := (result #>> '{rows,0,id}')::uuid;
    if job_a is null then raise exception 'ENQUEUE_MISSING_JOB_ID'; end if;
    result := public.oasis_voice_action(owner_a, 'enqueue', jsonb_build_object(
        'company_uids', jsonb_build_array(uid_a), 'request_id', 'synthetic-stage-request-a'));
    if (result #>> '{rows,0,id}')::uuid is distinct from job_a
       or (select count(*) from public.oasis_voice_jobs) <> 1 then
        raise exception 'ENQUEUE_IDEMPOTENCY_FAILED';
    end if;
    result := public.oasis_voice_action(owner_a, 'enqueue', jsonb_build_object(
        'company_uids', jsonb_build_array(uid_a), 'request_id', 'synthetic-stage-duplicate'));
    if result->>'code' is distinct from 'DUPLICATE' then raise exception 'DUPLICATE_GUARD_FAILED'; end if;
    result := public.oasis_voice_action(owner_b, 'list_jobs', '{}');
    if jsonb_array_length(result->'rows') is distinct from 0 then raise exception 'JOB_OWNERSHIP_FAILED'; end if;
    result := public.oasis_voice_action(owner_a, 'approve', jsonb_build_object('job_id', job_a));
    if result->>'code' is distinct from 'NOT_AUTHORIZED' then raise exception 'ADMIN_APPROVAL_GUARD_FAILED'; end if;
    result := public.oasis_voice_action(admin_actor, 'approve', jsonb_build_object('job_id', job_a));
    if (result->>'ok')::boolean is distinct from true
       or (select status from public.oasis_voice_jobs where id = job_a) is distinct from 'approved' then
        raise exception 'ADMIN_APPROVAL_FAILED';
    end if;

    result := public.oasis_voice_action(owner_a, 'enqueue', jsonb_build_object(
        'company_uids', jsonb_build_array(uid_c), 'request_id', 'synthetic-stage-request-c'));
    if (result->>'ok')::boolean is distinct from true then raise exception 'OPTOUT_FIXTURE_ENQUEUE_FAILED'; end if;
    job_c := (result #>> '{rows,0,id}')::uuid;
    result := public.oasis_voice_action(owner_a, 'do_not_call', jsonb_build_object('company_uid', uid_c));
    if (result->>'ok')::boolean is distinct from true
       or (select status from public.oasis_voice_jobs where id = job_c) is distinct from 'cancelled'
       or exists(select 1 from public.oasis_voice_permissions where company_uid = uid_c and revoked_at is null)
       or public.oasis_voice_blocked(uid_c, public.oasis_voice_phone(phone_c)) is distinct from true then
        raise exception 'OPTOUT_PERSISTENCE_FAILED';
    end if;
    result := public.oasis_voice_action(admin_actor, 'grant_permission',
        consent || jsonb_build_object('company_uids', jsonb_build_array(uid_c)));
    if result->>'code' is distinct from 'DO_NOT_CALL' then raise exception 'OPTOUT_REGRANT_GUARD_FAILED'; end if;
    result := public.oasis_voice_action(owner_a, 'enqueue', jsonb_build_object(
        'company_uids', jsonb_build_array(uid_c), 'request_id', 'synthetic-stage-after-optout'));
    if result->>'code' is distinct from 'DO_NOT_CALL' then raise exception 'OPTOUT_ENQUEUE_GUARD_FAILED'; end if;

    -- Simulate only the stored state of our own approved fixture. This is NOT a
    -- dispatch: no claim RPC, provider, gateway, or external network is invoked.
    update public.oasis_voice_jobs
    set status = 'in_progress', connected_at = now(), dispatch_at = now(),
        dispatch_nonce = gen_random_uuid(), provider_call_id = synthetic_call_id
    where id = job_a and status = 'approved' and requested_by = owner_a;
    if not found then raise exception 'SYNTHETIC_CONNECTED_STATE_FAILED'; end if;

    while extract(isodow from visit_day) > 5 loop visit_day := visit_day + 1; end loop;
    next_visit := (visit_day + time '10:00') at time zone 'Asia/Seoul';
    visit_payload := jsonb_build_object('job_id', job_a, 'provider_call_id', synthetic_call_id,
        'outcome', 'visit_requested', 'customer_confirmed', true,
        'visit_at', to_char(next_visit at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
        'address', 'Synthetic staging visit location', 'summary', 'Synthetic staging visit request');
    result := public.oasis_voice_worker('result', visit_payload);
    if (result->>'ok')::boolean is distinct from true
       or not exists(select 1 from public.oasis_voice_jobs where id = job_a
                     and outcome = 'visit_requested' and visit_at = next_visit
                     and contact_recorded_at is not null and visit_confirmed_at is null
                     and safe_error_code is null)
       or (select count(*) from public.oasis_company_sales_contact_logs
           where assignment_id = assignment_a and contact_result = 'connected') <> 1 then
        raise exception 'REAL_CONTACT_RPC_INTEGRATION_FAILED';
    end if;
    result := public.oasis_voice_worker('result', visit_payload);
    if result->>'code' is distinct from 'DUPLICATE'
       or (select count(*) from public.oasis_company_sales_contact_logs where assignment_id = assignment_a) <> 1 then
        raise exception 'CONTACT_LOG_REPLAY_IDEMPOTENCY_FAILED';
    end if;
    result := public.oasis_voice_action(owner_a, 'confirm_visit',
        jsonb_build_object('job_id', job_a, 'reason', 'Synthetic staging expert availability confirmed'));
    if (result->>'ok')::boolean is distinct from true
       or not exists(select 1 from public.oasis_voice_jobs where id = job_a
                     and visit_confirmed_at is not null and visit_confirmed_by = owner_a)
       or (select count(*) from public.oasis_company_sales_contact_logs
           where assignment_id = assignment_a and contact_result = 'consultation_scheduled') <> 1
       or (select count(*) from public.oasis_company_sales_contact_logs where assignment_id = assignment_a) <> 2
       or not exists(select 1 from public.oasis_company_sales_assignments
                     where id = assignment_a and status = 'consulting'
                       and next_contact_at = next_visit and contact_count = 2) then
        raise exception 'REAL_VISIT_CONFIRMATION_INTEGRATION_FAILED';
    end if;
    result := public.oasis_voice_action(owner_a, 'confirm_visit',
        jsonb_build_object('job_id', job_a, 'reason', 'Synthetic repeated confirmation'));
    if result->>'code' is distinct from 'INVALID_STATE'
       or (select count(*) from public.oasis_company_sales_contact_logs where assignment_id = assignment_a) <> 2 then
        raise exception 'VISIT_CONFIRMATION_REPLAY_GUARD_FAILED';
    end if;
    result := public.oasis_voice_worker('status', jsonb_build_object('job_id', job_a,
        'provider_call_id', synthetic_call_id, 'status', 'completed', 'sequence_number', 3, 'duration_seconds', 60));
    if (result->>'ok')::boolean is distinct from true then raise exception 'COMPLETION_STATUS_FAILED'; end if;
    result := public.oasis_voice_worker('result', jsonb_build_object('job_id', job_a,
        'provider_call_id', synthetic_call_id, 'outcome', 'do_not_call', 'customer_confirmed', true));
    if (result->>'ok')::boolean is distinct from true
       or not exists(select 1 from public.oasis_voice_jobs where id = job_a
                     and outcome = 'do_not_call' and visit_confirmed_at is null and visit_confirmed_by is null)
       or public.oasis_voice_blocked(uid_a, public.oasis_voice_phone(phone_a)) is distinct from true
       or (select count(*) from public.oasis_company_sales_contact_logs where assignment_id = assignment_a) <> 2 then
        raise exception 'LATE_OPTOUT_CLEAR_CONFIRMATION_FAILED';
    end if;
    result := public.oasis_voice_action(admin_actor, 'grant_permission',
        consent || jsonb_build_object('company_uids', jsonb_build_array(uid_a)));
    if result->>'code' is distinct from 'DO_NOT_CALL' then raise exception 'LATE_OPTOUT_REGRANT_GUARD_FAILED'; end if;

    if (select count(*) from public.oasis_users) <> 3
       or (select count(*) from public.oasis_prospect_companies) <> 3
       or (select count(*) from public.oasis_voice_jobs) <> 2 then
        raise exception 'SYNTHETIC_FIXTURE_SCOPE_CHANGED';
    end if;
    raise notice 'PASS: real-schema voice actions, ownership, consent, idempotency, approval, CRM integration, opt-out and ACL assertions';
end;
$real_schema_voice_verification$;

-- Exercise denied access as the browser roles, not just catalog ACL inspection.
set local role anon;
do $anon_access_guard$
begin
    begin
        perform public.oasis_voice_action('synthetic-voice-stage-admin', 'candidates', '{}');
        raise exception 'ANON_RPC_EXECUTION_WAS_ALLOWED';
    exception when insufficient_privilege then null;
    end;
    begin
        perform 1 from public.oasis_voice_jobs;
        raise exception 'ANON_TABLE_READ_WAS_ALLOWED';
    exception when insufficient_privilege then null;
    end;
end;
$anon_access_guard$;

set local role authenticated;
do $authenticated_access_guard$
begin
    begin
        perform public.oasis_voice_worker('result', '{}');
        raise exception 'AUTHENTICATED_WORKER_EXECUTION_WAS_ALLOWED';
    exception when insufficient_privilege then null;
    end;
    begin
        perform 1 from public.oasis_voice_permissions;
        raise exception 'AUTHENTICATED_TABLE_READ_WAS_ALLOWED';
    exception when insufficient_privilege then null;
    end;
end;
$authenticated_access_guard$;

reset role;
select 'PASS: all staging assertions completed; synthetic rows are rolled back by the next statement' as verification_result;
rollback;
