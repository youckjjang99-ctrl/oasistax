-- OASIS CRM: use service-role-only RPCs for Kakao guard hold checks.
-- The collector must not depend on direct REST table access during a guarded
-- resume, and these helpers intentionally expose no contact data.

create or replace function public.oasis_has_kakao_no_match_holds()
returns boolean
language sql
stable
security invoker
set search_path = public, pg_temp
as $$
    select exists (
        select 1
        from public.oasis_employment_contacts
        where phone_provider_stage = 'kakao'
          and phone_status = 'pending'
          and phone_last_error = 'KAKAO_NO_MATCH_HELD'
    );
$$;

create or replace function public.oasis_clear_kakao_no_match_holds()
returns integer
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
    v_updated_count integer := 0;
begin
    update public.oasis_employment_contacts
    set
        phone_last_error = '',
        last_error = '',
        updated_at = now()
    where phone_provider_stage = 'kakao'
      and phone_status = 'pending'
      and phone_last_error = 'KAKAO_NO_MATCH_HELD';

    get diagnostics v_updated_count = row_count;
    return v_updated_count;
end;
$$;

revoke all on function public.oasis_has_kakao_no_match_holds()
from public, anon, authenticated, service_role;
grant execute on function public.oasis_has_kakao_no_match_holds()
to service_role;

revoke all on function public.oasis_clear_kakao_no_match_holds()
from public, anon, authenticated, service_role;
grant execute on function public.oasis_clear_kakao_no_match_holds()
to service_role;

comment on function public.oasis_has_kakao_no_match_holds()
is 'Service-role-only Kakao guard safety check. Returns no contact data.';

comment on function public.oasis_clear_kakao_no_match_holds()
is 'Service-role-only Kakao guard restart helper. Clears only the internal hold marker.';

notify pgrst, 'reload schema';
