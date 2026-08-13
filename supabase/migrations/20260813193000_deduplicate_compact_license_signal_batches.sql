-- Some LOCALDATA services return the same management number more than once
-- in a page. Collapse those duplicates before the atomic upsert.

begin;

create or replace function public.oasis_upsert_recent_license_signals(
    p_rows jsonb,
    p_retention_months integer default 12
)
returns integer
language plpgsql
security invoker
set search_path = public, extensions, pg_temp
as $$
declare
    v_affected integer := 0;
    v_retention integer := greatest(1, least(24, coalesce(p_retention_months, 12)));
begin
    if jsonb_typeof(p_rows) is distinct from 'array' then
        raise exception 'p_rows must be a JSON array';
    end if;

    with input_rows as (
        select
            nullif(btrim(r.source_key), '') as source_key,
            public.oasis_normalize_company_name(r.company_name) as normalized_name,
            public.oasis_normalize_sales_address(r.address) as normalized_address,
            case
                when replace(replace(btrim(r.license_date), '-', ''), '.', '')
                    ~ '^[0-9]{8}$'
                then to_date(
                    replace(replace(btrim(r.license_date), '-', ''), '.', ''),
                    'YYYYMMDD'
                )
                else null
            end as parsed_license_date,
            coalesce(r.is_active, false) as is_active
        from jsonb_to_recordset(p_rows) as r(
            source_key text,
            company_name text,
            address text,
            license_date text,
            is_active boolean
        )
    ),
    prepared as (
        select
            encode(
                extensions.digest(convert_to(source_key, 'UTF8'), 'sha256'),
                'hex'
            ) as signal_key,
            encode(
                extensions.digest(
                    convert_to(
                        normalized_name || '|' || normalized_address,
                        'UTF8'
                    ),
                    'sha256'
                ),
                'hex'
            ) as match_key,
            parsed_license_date as license_date,
            is_active
        from input_rows
        where source_key is not null
          and normalized_name <> ''
          and normalized_address is not null
          and parsed_license_date >= current_date - make_interval(months => v_retention)
          and parsed_license_date <= current_date
    ),
    deduplicated as (
        select distinct on (signal_key)
            signal_key,
            match_key,
            license_date,
            is_active
        from prepared
        order by signal_key, license_date desc, is_active desc
    ),
    upserted as (
        insert into public.oasis_recent_license_signals (
            signal_key,
            match_key,
            license_date,
            is_active,
            last_seen_at
        )
        select
            signal_key,
            match_key,
            license_date,
            is_active,
            now()
        from deduplicated
        on conflict (signal_key) do update
        set match_key = excluded.match_key,
            license_date = excluded.license_date,
            is_active = excluded.is_active,
            last_seen_at = excluded.last_seen_at
        returning 1
    )
    select count(*)::integer into v_affected from upserted;

    return v_affected;
end;
$$;

revoke all on function public.oasis_upsert_recent_license_signals(jsonb, integer)
    from public, anon, authenticated;
grant execute on function public.oasis_upsert_recent_license_signals(jsonb, integer)
    to service_role;

commit;
