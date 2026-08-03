-- Fix ordinary-member DB assignment failures caused by PL/pgSQL resolving
-- the RETURNS TABLE variable `company_uid` against the ON CONFLICT arbiter.
-- Referencing the named unique constraint removes that ambiguity without
-- changing assignment ownership, limits, expiry, or audit behavior.

begin;

do $oasis_fix_member_assignment_conflict$
declare
    v_function_oid oid;
    v_definition text;
    v_fixed_definition text;
    v_ambiguous_clause constant text :=
        'on conflict (company_uid) do nothing';
    v_constraint_clause constant text :=
        'on conflict on constraint oasis_company_sales_assignments_company_uid_key'
        || chr(10) || '        do nothing';
begin
    select p.oid
    into v_function_oid
    from pg_catalog.pg_proc p
    join pg_catalog.pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public'
      and p.proname = 'oasis_claim_company_sales_assignment'
      and pg_catalog.pg_get_function_identity_arguments(p.oid) =
          'p_current_user_id text, p_company_id uuid, p_company_uid text, p_session_id text';

    if v_function_oid is null then
        raise exception 'OASIS_MEMBER_ASSIGNMENT_FUNCTION_MISSING';
    end if;

    v_definition := pg_catalog.pg_get_functiondef(v_function_oid);
    if position(v_constraint_clause in v_definition) > 0 then
        return;
    end if;
    if position(v_ambiguous_clause in v_definition) = 0 then
        raise exception 'OASIS_MEMBER_ASSIGNMENT_CONFLICT_CLAUSE_UNEXPECTED';
    end if;

    v_fixed_definition := replace(
        v_definition,
        v_ambiguous_clause,
        v_constraint_clause
    );
    execute v_fixed_definition;

    select pg_catalog.pg_get_functiondef(p.oid)
    into v_definition
    from pg_catalog.pg_proc p
    where p.oid = v_function_oid;
    if position(v_constraint_clause in v_definition) = 0
       or position(v_ambiguous_clause in v_definition) > 0 then
        raise exception 'OASIS_MEMBER_ASSIGNMENT_CONFLICT_FIX_NOT_APPLIED';
    end if;
end;
$oasis_fix_member_assignment_conflict$;

select pg_catalog.pg_notify('pgrst', 'reload schema');

commit;
