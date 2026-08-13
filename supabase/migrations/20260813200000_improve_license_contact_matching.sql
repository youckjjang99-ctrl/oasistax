-- Improve recent-licence/contact linkage without retaining source PII.

alter table public.oasis_recent_license_signals
    add column if not exists contact_ref_key text,
    add column if not exists contact_match_method text not null default '',
    add column if not exists has_mobile_phone boolean not null default false,
    add column if not exists has_landline_phone boolean not null default false;

do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conname = 'oasis_recent_license_signals_contact_ref_key_check'
          and conrelid = 'public.oasis_recent_license_signals'::regclass
    ) then
        alter table public.oasis_recent_license_signals
            add constraint oasis_recent_license_signals_contact_ref_key_check
            check (
                contact_ref_key is null
                or contact_ref_key ~ '^[0-9a-f]{64}$'
            );
    end if;

    if not exists (
        select 1 from pg_constraint
        where conname = 'oasis_recent_license_signals_match_method_check'
          and conrelid = 'public.oasis_recent_license_signals'::regclass
    ) then
        alter table public.oasis_recent_license_signals
            add constraint oasis_recent_license_signals_match_method_check
            check (
                contact_match_method in (
                    '', 'exact_address', 'address_core', 'unique_region_name'
                )
            );
    end if;
end $$;

create index if not exists oasis_recent_license_signals_contact_ref_idx
    on public.oasis_recent_license_signals (contact_ref_key, license_date desc)
    where contact_ref_key is not null and is_active;

create index if not exists oasis_contacts_phone_name_match_idx
    on public.oasis_employment_contacts (
        public.oasis_normalize_company_name(company_name),
        source_record_key
    )
    where trim(coalesce(source_record_key, '')) <> ''
      and (has_mobile_phone or has_landline_phone);

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
    v_clean := regexp_replace(v_clean, '^\\s*\\(?[0-9]{5}\\)?\\s*', '', 'g');
    v_clean := regexp_replace(v_clean, '\\([^)]*\\)', ' ', 'g');
    v_clean := regexp_replace(v_clean, '\\s+', ' ', 'g');

    v_core := substring(
        v_clean from '^(.+?(로|길)\\s*[0-9]+(-[0-9]+)?)'
    );
    if v_core is null then
        v_core := substring(
            v_clean from '^(.+?(동|리|읍|면)\\s*(산\\s*)?[0-9]+(-[0-9]+)?)'
        );
    end if;

    return public.oasis_normalize_sales_address(coalesce(v_core, v_clean));
end;
$$;

create or replace function public.oasis_address_province_code(p_value text)
returns text
language sql
immutable
parallel safe
set search_path = public, pg_temp
as $$
    select case
        when coalesce(p_value, '') ~ '(서울특별시|서울시)' then '11'
        when coalesce(p_value, '') ~ '(부산광역시|부산시)' then '26'
        when coalesce(p_value, '') ~ '(대구광역시|대구시)' then '27'
        when coalesce(p_value, '') ~ '(인천광역시|인천시)' then '28'
        when coalesce(p_value, '') ~ '(광주광역시|광주시)' then '29'
        when coalesce(p_value, '') ~ '(대전광역시|대전시)' then '30'
        when coalesce(p_value, '') ~ '(울산광역시|울산시)' then '31'
        when coalesce(p_value, '') ~ '(세종특별자치시|세종시)' then '36'
        when coalesce(p_value, '') ~ '경기도' then '41'
        when coalesce(p_value, '') ~ '(강원특별자치도|강원도)' then '51'
        when coalesce(p_value, '') ~ '충청북도' then '43'
        when coalesce(p_value, '') ~ '충청남도' then '44'
        when coalesce(p_value, '') ~ '(전북특별자치도|전라북도)' then '52'
        when coalesce(p_value, '') ~ '전라남도' then '46'
        when coalesce(p_value, '') ~ '경상북도' then '47'
        when coalesce(p_value, '') ~ '경상남도' then '48'
        when coalesce(p_value, '') ~ '(제주특별자치도|제주도)' then '50'
        else ''
    end;
$$;

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
            public.oasis_compact_address_core(r.address) as address_core,
            public.oasis_address_province_code(r.address) as province_code,
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
            normalized_name,
            normalized_address,
            address_core,
            province_code,
            parsed_license_date as license_date,
            is_active
        from input_rows
        where source_key is not null
          and normalized_name <> ''
          and normalized_address is not null
          and parsed_license_date >= current_date - make_interval(months => v_retention)
          and parsed_license_date <= current_date
    ),
    candidate_rows as (
        select
            p.signal_key,
            c.source_record_key,
            bool_or(c.has_mobile_phone) as has_mobile_phone,
            bool_or(c.has_landline_phone) as has_landline_phone,
            max(
                case
                    when public.oasis_normalize_sales_address(c.address)
                        = p.normalized_address then 300
                    when public.oasis_compact_address_core(c.address)
                        = p.address_core then 200
                    when p.province_code <> ''
                     and c.province_code = p.province_code
                     and nullif(public.oasis_normalize_sales_address(c.district), '')
                        is not null
                     and position(
                        public.oasis_normalize_sales_address(c.district)
                        in p.normalized_address
                     ) > 0 then 100
                    else 0
                end
            ) as match_score
        from prepared p
        join public.oasis_employment_contacts c
          on public.oasis_normalize_company_name(c.company_name)
             = p.normalized_name
         and trim(coalesce(c.source_record_key, '')) <> ''
         and (c.has_mobile_phone or c.has_landline_phone)
        where public.oasis_normalize_sales_address(c.address)
                = p.normalized_address
           or public.oasis_compact_address_core(c.address) = p.address_core
           or (
                p.province_code <> ''
                and c.province_code = p.province_code
                and nullif(
                    public.oasis_normalize_sales_address(c.district), ''
                ) is not null
                and position(
                    public.oasis_normalize_sales_address(c.district)
                    in p.normalized_address
                ) > 0
           )
        group by p.signal_key, c.source_record_key
    ),
    candidate_scored as (
        select
            cr.*,
            count(*) over (partition by cr.signal_key) as candidate_count
        from candidate_rows cr
        where cr.match_score > 0
    ),
    selected_contact as (
        select distinct on (signal_key)
            signal_key,
            encode(
                extensions.digest(
                    convert_to(source_record_key, 'UTF8'),
                    'sha256'
                ),
                'hex'
            ) as contact_ref_key,
            case match_score
                when 300 then 'exact_address'
                when 200 then 'address_core'
                else 'unique_region_name'
            end as contact_match_method,
            has_mobile_phone,
            has_landline_phone
        from candidate_scored
        where match_score >= 200
           or (match_score = 100 and candidate_count = 1)
        order by
            signal_key,
            match_score desc,
            (has_mobile_phone::integer + has_landline_phone::integer) desc,
            source_record_key
    ),
    deduplicated as (
        select distinct on (p.signal_key)
            p.signal_key,
            p.match_key,
            p.license_date,
            p.is_active,
            sc.contact_ref_key,
            coalesce(sc.contact_match_method, '') as contact_match_method,
            coalesce(sc.has_mobile_phone, false) as has_mobile_phone,
            coalesce(sc.has_landline_phone, false) as has_landline_phone
        from prepared p
        left join selected_contact sc using (signal_key)
        order by p.signal_key, p.license_date desc, p.is_active desc
    ),
    upserted as (
        insert into public.oasis_recent_license_signals (
            signal_key,
            match_key,
            license_date,
            is_active,
            contact_ref_key,
            contact_match_method,
            has_mobile_phone,
            has_landline_phone,
            last_seen_at
        )
        select
            signal_key,
            match_key,
            license_date,
            is_active,
            contact_ref_key,
            contact_match_method,
            has_mobile_phone,
            has_landline_phone,
            now()
        from deduplicated
        on conflict (signal_key) do update
        set match_key = excluded.match_key,
            license_date = excluded.license_date,
            is_active = excluded.is_active,
            contact_ref_key = coalesce(
                excluded.contact_ref_key,
                public.oasis_recent_license_signals.contact_ref_key
            ),
            contact_match_method = case
                when excluded.contact_ref_key is not null
                    then excluded.contact_match_method
                else public.oasis_recent_license_signals.contact_match_method
            end,
            has_mobile_phone = public.oasis_recent_license_signals.has_mobile_phone
                or excluded.has_mobile_phone,
            has_landline_phone = public.oasis_recent_license_signals.has_landline_phone
                or excluded.has_landline_phone,
            last_seen_at = excluded.last_seen_at
        returning 1
    )
    select count(*)::integer into v_affected from upserted;

    return v_affected;
end;
$$;

revoke all on function public.oasis_compact_address_core(text)
    from public, anon, authenticated;
grant execute on function public.oasis_compact_address_core(text)
    to service_role;
revoke all on function public.oasis_address_province_code(text)
    from public, anon, authenticated;
grant execute on function public.oasis_address_province_code(text)
    to service_role;
revoke all on function public.oasis_upsert_recent_license_signals(jsonb, integer)
    from public, anon, authenticated;
grant execute on function public.oasis_upsert_recent_license_signals(jsonb, integer)
    to service_role;

-- Make the existing search RPC consume the staged contact linkage as well.
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
        select b.*, compact.license_date as compact_license_date
        from base b
        left join lateral (
            select s.license_date
            from public.oasis_recent_license_signals s
            where s.is_active
              and (
                    s.match_key = encode(
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
                    or s.contact_ref_key = encode(
                        extensions.digest(
                            convert_to(b.source_record_key, 'UTF8'),
                            'sha256'
                        ),
                        'hex'
                    )
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
             and not ('license_date_match' = any(
                coalesce(s.new_company_reason_codes, '{}')
             ))
            then array_append(
                coalesce(s.new_company_reason_codes, '{}'),
                'license_date_match'
            )
            else coalesce(s.new_company_reason_codes, '{}')
        end,
        coalesce(s.compact_license_date, s.estimated_opening_date),
        extract(year from coalesce(
            s.compact_license_date,
            s.estimated_opening_date
        ))::integer,
        case
            when s.compact_license_date is not null then 'day'
            else s.estimated_opening_precision
        end,
        case
            when s.compact_license_date is not null then 'license_date'
            else s.estimated_opening_source
        end,
        coalesce(s.matched_license_date, s.compact_license_date)
    from scored s;
$$;

revoke all on function public.oasis_search_recent_openings_v4(
    text, text, text, integer, integer, text[], text[], integer,
    boolean, integer, text
) from public, anon;
grant execute on function public.oasis_search_recent_openings_v4(
    text, text, text, integer, integer, text[], text[], integer,
    boolean, integer, text
) to authenticated, service_role;

comment on column public.oasis_recent_license_signals.contact_ref_key is
    'SHA-256 of an internal contact source key; raw business/contact identifiers are not retained.';
comment on column public.oasis_recent_license_signals.contact_match_method is
    'High-confidence staged match: exact address, address core, or unique name within the same district.';
