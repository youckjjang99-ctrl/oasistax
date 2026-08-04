-- OASIS CRM v10.3.2 - company-wide assignment RLS/grant companion
-- Run after supabase_v1032_company_sales_assignments.sql.
-- This script is idempotent and intentionally touches only objects introduced
-- by the company assignment migration.  Existing project grants are preserved.

do $$
declare
    v_table text;
begin
    foreach v_table in array array[
        'oasis_company_sales_assignments',
        'oasis_sales_assignment_settings',
        'oasis_user_prospect_notes',
        'oasis_company_sales_contact_logs',
        'oasis_company_view_history',
        'oasis_company_assignment_audit_logs',
        'oasis_company_assignment_conflicts'
    ] loop
        if to_regclass(format('public.%I', v_table)) is not null then
            execute format('alter table public.%I enable row level security', v_table);
            execute format(
                'revoke all on table public.%I from PUBLIC, anon, authenticated',
                v_table
            );
            execute format(
                'grant select, insert, update, delete on table public.%I to service_role',
                v_table
            );
        end if;
    end loop;
end;
$$;

-- Identity sequences used only by the two bigint audit/history tables.
do $$
declare
    v_sequence text;
begin
    foreach v_sequence in array array[
        'oasis_company_view_history_id_seq',
        'oasis_company_assignment_audit_logs_id_seq'
    ] loop
        if to_regclass(format('public.%I', v_sequence)) is not null then
            execute format(
                'revoke all on sequence public.%I from PUBLIC, anon, authenticated',
                v_sequence
            );
            execute format(
                'grant usage, select on sequence public.%I to service_role',
                v_sequence
            );
        end if;
    end loop;
end;
$$;

-- Custom application authentication is validated inside the RPCs against
-- approved oasis_users. Browser roles cannot call helpers or RPCs; Railway's
-- service_role is the only direct executor and therefore remains subject to
-- the application's actor/owner/admin checks before each mutation or read.
do $$
declare
    v_function_names text[] := array[
        'oasis_admin_change_company_assignee',
        'oasis_admin_permanent_exclude_company',
        'oasis_admin_reactivate_company_assignment',
        'oasis_admin_release_company_assignment',
        'oasis_admin_set_sales_user_limit',
        'oasis_claim_and_save_company_sales_assignment',
        'oasis_claim_save_and_promote_prospect_contacts',
        'oasis_claim_company_sales_assignment',
        'oasis_company_sales_assignment_feature_ready',
        'oasis_company_sales_phone_fingerprint',
        'oasis_filter_blocked_company_uids',
        'oasis_is_valid_company_uid',
        'oasis_list_admin_company_assignments',
        'oasis_list_company_assignment_admin_metrics',
        'oasis_list_company_assignment_audit',
        'oasis_list_company_sales_contacts',
        'oasis_list_user_company_assignments',
        'oasis_make_company_uid',
        'oasis_normalize_sales_address',
        'oasis_normalize_sales_company_name',
        'oasis_normalize_sales_phone',
        'oasis_record_company_sales_contact',
        'oasis_record_company_views',
        'oasis_release_company_sales_assignment',
        'oasis_release_expired_company_assignments',
        'oasis_resolve_candidate_company_uids',
        'oasis_resolve_company_sales_uid',
        'oasis_sales_actor_is_active',
        'oasis_sales_actor_is_admin',
        'oasis_sales_digits',
        'oasis_sales_session_fingerprint',
        'oasis_save_user_prospect_note',
        'oasis_write_company_assignment_audit'
    ];
    v_proc record;
begin
    for v_proc in
        select p.oid::regprocedure as signature
        from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'public'
          and p.proname = any(v_function_names)
    loop
        execute format(
            'revoke all on function %s from PUBLIC, anon, authenticated',
            v_proc.signature
        );
        execute format(
            'grant execute on function %s to service_role',
            v_proc.signature
        );
    end loop;
end;
$$;
