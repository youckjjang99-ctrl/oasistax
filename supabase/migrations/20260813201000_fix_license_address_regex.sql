create or replace function public.oasis_compact_address_core(p_value text)
returns text
language plpgsql
immutable
parallel safe
set search_path = public, pg_temp
as $$
declare
    v_clean text;
    v_core text;
begin
    v_clean := normalize(btrim(coalesce(p_value, '')), NFKC);
    v_clean := regexp_replace(
        v_clean,
        '^[[:space:]]*[(]?[0-9]{5}[)]?[[:space:]]*',
        '',
        'g'
    );
    v_clean := regexp_replace(v_clean, '[(][^)]*[)]', ' ', 'g');
    v_clean := regexp_replace(v_clean, '[[:space:]]+', ' ', 'g');

    v_core := substring(
        v_clean from '^(.+?(로|길)[[:space:]]*[0-9]+(-[0-9]+)?)'
    );
    if v_core is null then
        v_core := substring(
            v_clean from '^(.+?(동|리|읍|면)[[:space:]]*(산[[:space:]]*)?[0-9]+(-[0-9]+)?)'
        );
    end if;

    return public.oasis_normalize_sales_address(coalesce(v_core, v_clean));
end;
$$;

revoke all on function public.oasis_compact_address_core(text)
    from public, anon, authenticated;
grant execute on function public.oasis_compact_address_core(text)
    to service_role;
