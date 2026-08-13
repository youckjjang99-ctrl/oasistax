-- Store only compact recent licence signals for new-company confidence scoring.
-- Original company names, addresses, phone numbers, and API payloads are not
-- persisted in this table.

begin;

create table if not exists public.oasis_recent_license_signals (
    signal_key text primary key,
    match_key text not null,
    license_date date not null,
    is_active boolean not null default true,
    last_seen_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    constraint oasis_recent_license_signals_signal_key_check
        check (signal_key ~ '^[0-9a-f]{64}$'),
    constraint oasis_recent_license_signals_match_key_check
        check (match_key ~ '^[0-9a-f]{64}$')
);

create index if not exists oasis_recent_license_signals_active_match_idx
    on public.oasis_recent_license_signals (match_key, license_date desc)
    where is_active;

create index if not exists oasis_recent_license_signals_license_date_idx
    on public.oasis_recent_license_signals (license_date);

alter table public.oasis_recent_license_signals enable row level security;

revoke all on table public.oasis_recent_license_signals
    from public, anon, authenticated;
grant select, insert, update, delete
    on table public.oasis_recent_license_signals to service_role;

comment on table public.oasis_recent_license_signals is
    'Recent licence dates stored with irreversible matching hashes only; no original company, address, phone, or raw payload.';

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
        from prepared
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

create or replace function public.oasis_cleanup_recent_license_signals(
    p_retention_months integer default 12
)
returns integer
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
    v_deleted integer := 0;
    v_retention integer := greatest(1, least(24, coalesce(p_retention_months, 12)));
begin
    delete from public.oasis_recent_license_signals
    where license_date < current_date - make_interval(months => v_retention);
    get diagnostics v_deleted = row_count;
    return v_deleted;
end;
$$;

revoke all on function public.oasis_cleanup_recent_license_signals(integer)
    from public, anon, authenticated;
grant execute on function public.oasis_cleanup_recent_license_signals(integer)
    to service_role;

create or replace function public.oasis_search_recent_openings_v4(
    p_province_code text default '',
    p_province_name text default '',
    p_district text default '',
    p_min_employees integer default 1,
    p_max_employees integer default 300,
    p_industries text[] default '{}',
    p_contact_channels text[] default '{}',
    p_recent_months integer default 6,
    p_include_comwel_annual boolean default true,
    p_limit integer default 100,
    p_business_type text default 'all'
)
returns table(
    source_type text,
    source_record_key text,
    business_no text,
    company_name text,
    address text,
    province_name text,
    district_name text,
    province_code text,
    district_code text,
    industry_code text,
    industry_name text,
    industry_category text,
    current_employee_count integer,
    opening_signal_date date,
    opening_signal_year integer,
    opening_signal_basis text,
    opening_signal_precision text,
    source_period text,
    mobile_phone text,
    landline_phone text,
    email text,
    instagram text,
    instagram_url text,
    contact_status text,
    contact_checked_at timestamptz,
    new_company_score integer,
    new_company_confidence text,
    new_company_reason_codes text[],
    estimated_opening_date date,
    estimated_opening_year integer,
    estimated_opening_precision text,
    estimated_opening_source text,
    matched_license_date date
)
language sql
stable
security invoker
set search_path = public, extensions, pg_temp
as $$
    with base as (
        select *
        from public.oasis_search_recent_openings_v3(
            p_province_code => p_province_code,
            p_province_name => p_province_name,
            p_district => p_district,
            p_min_employees => p_min_employees,
            p_max_employees => p_max_employees,
            p_industries => p_industries,
            p_contact_channels => p_contact_channels,
            p_recent_months => p_recent_months,
            p_include_comwel_annual => p_include_comwel_annual,
            p_limit => p_limit,
            p_business_type => p_business_type
        )
    ),
    matched as (
        select
            b.*,
            compact.license_date as compact_license_date
        from base b
        left join lateral (
            select s.license_date
            from public.oasis_recent_license_signals s
            where s.is_active
              and s.match_key = encode(
                  extensions.digest(
                      convert_to(
                          public.oasis_normalize_company_name(b.company_name)
                          || '|'
                          || coalesce(
                              public.oasis_normalize_sales_address(b.address),
                              ''
                          ),
                          'UTF8'
                      ),
                      'sha256'
                  ),
                  'hex'
              )
              and s.license_date >= current_date - make_interval(
                  months => case
                      when p_recent_months in (3, 6, 12)
                          then p_recent_months
                      else 6
                  end
              )
            order by s.license_date desc
            limit 1
        ) compact on true
    ),
    scored as (
        select
            m.*,
            least(
                100,
                coalesce(m.new_company_score, 0)
                + case
                    when m.matched_license_date is null
                     and m.compact_license_date is not null then 20
                    else 0
                  end
            )::integer as compact_score
        from matched m
    )
    select
        s.source_type,
        s.source_record_key,
        s.business_no,
        s.company_name,
        s.address,
        s.province_name,
        s.district_name,
        s.province_code,
        s.district_code,
        s.industry_code,
        s.industry_name,
        s.industry_category,
        s.current_employee_count,
        s.opening_signal_date,
        s.opening_signal_year,
        s.opening_signal_basis,
        s.opening_signal_precision,
        s.source_period,
        s.mobile_phone,
        s.landline_phone,
        s.email,
        s.instagram,
        s.instagram_url,
        s.contact_status,
        s.contact_checked_at,
        s.compact_score,
        case
            when s.compact_score >= 80 then 'high'
            when s.compact_score >= 50 then 'medium'
            when s.compact_score >= 30 then 'low'
            else 'pending'
        end,
        case
            when s.compact_license_date is not null
             and not ('license_date_match' = any(coalesce(s.new_company_reason_codes, '{}')))
            then array_append(
                coalesce(s.new_company_reason_codes, '{}'),
                'license_date_match'
            )
            else coalesce(s.new_company_reason_codes, '{}')
        end,
        coalesce(
            s.matched_license_date,
            s.compact_license_date,
            s.estimated_opening_date
        ),
        coalesce(
            extract(year from coalesce(
                s.matched_license_date,
                s.compact_license_date,
                s.estimated_opening_date
            ))::integer,
            s.estimated_opening_year
        ),
        case
            when coalesce(
                s.matched_license_date,
                s.compact_license_date,
                s.estimated_opening_date
            ) is not null then 'day'
            when s.estimated_opening_year is not null then 'year'
            else ''
        end,
        case
            when s.matched_license_date is not null
              or s.compact_license_date is not null then 'license_date'
            else s.estimated_opening_source
        end,
        coalesce(s.matched_license_date, s.compact_license_date)
    from scored s;
$$;

revoke all on function public.oasis_search_recent_openings_v4(
    text, text, text, integer, integer, text[], text[], integer,
    boolean, integer, text
) from public, anon, authenticated;
grant execute on function public.oasis_search_recent_openings_v4(
    text, text, text, integer, integer, text[], text[], integer,
    boolean, integer, text
) to service_role;

comment on function public.oasis_search_recent_openings_v4(
    text, text, text, integer, integer, text[], text[], integer,
    boolean, integer, text
) is 'Scores recent-opening candidates with compact hashed licence-date signals.';

commit;
