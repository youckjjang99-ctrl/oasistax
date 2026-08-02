-- OASIS CRM v10.3.1 - owner-scoped claim case soft deletion.
-- Customer rows disappear from the CRM list immediately while documents and
-- audit records remain subject to the existing retention policy.

begin;

alter table public.oasis_claim_cases
    add column if not exists deleted_at timestamptz;

create index if not exists oasis_claim_cases_owner_active_requested_idx
    on public.oasis_claim_cases (owner_user_id, requested_at desc)
    where deleted_at is null;

create or replace function public.oasis_claim_list_cases(
    p_owner_user_id text,
    p_limit integer default 500
)
returns setof public.oasis_claim_cases
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select c.*
    from public.oasis_claim_cases c
    where c.owner_user_id = lower(trim(p_owner_user_id))
      and c.deleted_at is null
    order by c.requested_at desc
    limit greatest(1, least(coalesce(p_limit, 500), 1000));
$$;

create or replace function public.oasis_claim_get_case(
    p_owner_user_id text,
    p_case_id uuid
)
returns setof public.oasis_claim_cases
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select c.*
    from public.oasis_claim_cases c
    where c.owner_user_id = lower(trim(p_owner_user_id))
      and c.id = p_case_id
      and c.deleted_at is null
    limit 1;
$$;

create or replace function public.oasis_claim_list_documents(
    p_owner_user_id text,
    p_case_id uuid,
    p_limit integer default 500,
    p_offset integer default 0
)
returns setof public.oasis_claim_documents
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select d.*
    from public.oasis_claim_documents d
    join public.oasis_claim_cases c
      on c.id = d.case_id
     and c.owner_user_id = d.owner_user_id
    where d.owner_user_id = lower(trim(p_owner_user_id))
      and d.case_id = p_case_id
      and c.deleted_at is null
    order by
        d.source asc,
        d.document_code asc,
        d.period_year desc nulls last,
        d.collection_key asc,
        d.id asc
    limit greatest(1, least(coalesce(p_limit, 500), 500))
    offset greatest(0, coalesce(p_offset, 0));
$$;

create or replace function public.oasis_claim_soft_delete_case(
    p_owner_user_id text,
    p_case_id uuid
)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_owner_user_id text := lower(trim(p_owner_user_id));
begin
    update public.oasis_claim_cases c
    set deleted_at = now(), updated_at = now()
    where c.id = p_case_id
      and c.owner_user_id = v_owner_user_id
      and c.deleted_at is null;

    if not found then
        return false;
    end if;

    insert into public.oasis_claim_audit_events (
        owner_user_id,
        case_id,
        action,
        source,
        outcome,
        metadata
    )
    values (
        v_owner_user_id,
        p_case_id,
        'case_soft_deleted',
        'user',
        'success',
        '{}'::jsonb
    );

    return true;
end;
$$;

revoke all on function public.oasis_claim_list_cases(text, integer)
from public, anon, authenticated, service_role;
grant execute on function public.oasis_claim_list_cases(text, integer)
to service_role;

revoke all on function public.oasis_claim_get_case(text, uuid)
from public, anon, authenticated, service_role;
grant execute on function public.oasis_claim_get_case(text, uuid)
to service_role;

revoke all on function public.oasis_claim_list_documents(
    text,
    uuid,
    integer,
    integer
)
from public, anon, authenticated, service_role;
grant execute on function public.oasis_claim_list_documents(
    text,
    uuid,
    integer,
    integer
)
to service_role;

revoke all on function public.oasis_claim_soft_delete_case(text, uuid)
from public, anon, authenticated, service_role;
grant execute on function public.oasis_claim_soft_delete_case(text, uuid)
to service_role;

commit;
