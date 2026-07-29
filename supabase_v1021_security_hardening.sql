-- OASIS CRM v10.2.1 security hardening
-- Server-side service_role access remains available.

begin;

alter function public.set_oasis_updated_at()
set search_path = public, pg_temp;

alter function public.oasis_preserve_enriched_phone()
set search_path = public, pg_temp;

revoke execute on function public.set_oasis_updated_at()
from public, anon, authenticated;
revoke execute on function public.oasis_preserve_enriched_phone()
from public, anon, authenticated;

do $$
declare
    relation record;
begin
    for relation in
        select c.relname, c.relkind
        from pg_class c
        join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = 'public'
          and c.relname like 'oasis\_%' escape '\'
          and c.relkind in ('r', 'p', 'v', 'm')
    loop
        if relation.relkind in ('r', 'p') then
            execute format(
                'alter table public.%I enable row level security',
                relation.relname
            );
            execute format(
                'revoke all on table public.%I from anon, authenticated',
                relation.relname
            );
            execute format(
                'grant select, insert, update, delete on table public.%I '
                'to service_role',
                relation.relname
            );
        else
            execute format(
                'revoke all on table public.%I from anon, authenticated',
                relation.relname
            );
            execute format(
                'grant select on table public.%I to service_role',
                relation.relname
            );
        end if;
    end loop;
end;
$$;

do $$
declare
    function_record record;
begin
    for function_record in
        select
            p.proname,
            pg_get_function_identity_arguments(p.oid) as identity_arguments
        from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'public'
          and p.proname like 'oasis\_%' escape '\'
    loop
        execute format(
            'revoke all on function public.%I(%s) '
            'from public, anon, authenticated',
            function_record.proname,
            function_record.identity_arguments
        );
        execute format(
            'grant execute on function public.%I(%s) to service_role',
            function_record.proname,
            function_record.identity_arguments
        );
    end loop;
end;
$$;

revoke all on all sequences in schema public from anon, authenticated;
grant usage, select on all sequences in schema public to service_role;

commit;
