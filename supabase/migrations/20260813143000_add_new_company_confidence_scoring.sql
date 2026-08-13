-- OASIS CRM: classify recent-opening candidates with free public signals.
-- Existing is_new_company/opening_signal_* values are preserved.  The v3 RPC
-- adds a confidence score and an estimated date without rewriting source data.

begin;

create index if not exists idx_oasis_nps_recent_opening_business_no
    on public.oasis_nps_employee_snapshots (
        business_no,
        applied_on desc,
        data_created_ym desc
    )
    where business_no ~ '^[0-9]{10}$'
      and applied_on is not null;

create index if not exists idx_oasis_licensed_businesses_opening_match
    on public.oasis_licensed_businesses (
        public.oasis_normalize_company_name(company_name),
        province,
        district,
        public.oasis_normalize_sales_address(address)
    )
    where is_active
      and trim(coalesce(license_date, '')) <> '';

create or replace function public.oasis_search_recent_openings_v3(
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
set search_path = public, pg_temp
as $$
    with base as (
        select *
        from public.oasis_search_recent_openings_v2(
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
    signals as (
        select
            b.*,
            nps.applied_on as verified_nps_date,
            (
                comwel.business_no is not null
            ) as has_comwel_new_signal,
            (
                nps.applied_on is not null
                and not public.oasis_has_earlier_employment_history(
                    b.business_no,
                    nps.snapshot_identity,
                    nps.applied_on
                )
            ) as has_nps_new_signal,
            license_match.license_dt as verified_license_date
        from base b
        left join public.oasis_comwel_annual_growth comwel
          on b.business_no ~ '^[0-9]{10}$'
         and comwel.business_no = b.business_no
         and comwel.is_new_2025
         and coalesce(comwel.workers_2023, 0) = 0
         and coalesce(comwel.workers_2024, 0) = 0
        left join lateral (
            select
                s.applied_on,
                s.snapshot_identity
            from public.oasis_nps_employee_snapshots s
            where s.applied_on is not null
              and s.applied_on >= current_date - make_interval(
                  months => case
                      when p_recent_months in (3, 6, 12)
                          then p_recent_months
                      else 6
                  end
              )
              and (
                  (
                      b.business_no ~ '^[0-9]{10}$'
                      and s.business_no = b.business_no
                  )
                  or s.snapshot_identity = b.source_record_key
              )
            order by s.applied_on desc, s.data_created_ym desc
            limit 1
        ) nps on true
        left join lateral (
            select parsed.license_dt
            from (
                select case
                    when replace(
                        replace(trim(l.license_date), '-', ''),
                        '.',
                        ''
                    ) ~ '^[0-9]{8}$'
                    then to_date(
                        replace(
                            replace(trim(l.license_date), '-', ''),
                            '.',
                            ''
                        ),
                        'YYYYMMDD'
                    )
                    else null
                end as license_dt
                from public.oasis_licensed_businesses l
                where l.is_active
                  and public.oasis_normalize_company_name(l.company_name)
                      = public.oasis_normalize_company_name(b.company_name)
                  and trim(coalesce(l.province, ''))
                      = trim(coalesce(b.province_name, ''))
                  and trim(coalesce(l.district, ''))
                      = trim(coalesce(b.district_name, ''))
                  and public.oasis_normalize_sales_address(l.address)
                      = public.oasis_normalize_sales_address(b.address)
            ) parsed
            where parsed.license_dt >= current_date - make_interval(
                months => case
                    when p_recent_months in (3, 6, 12)
                        then p_recent_months
                    else 6
                end
            )
            order by parsed.license_dt desc
            limit 1
        ) license_match on true
    ),
    scored as (
        select
            s.*,
            least(
                100,
                (case when s.has_nps_new_signal then 40 else 0 end)
                + (case when s.has_comwel_new_signal then 40 else 0 end)
                + (
                    case
                        when s.has_nps_new_signal
                          or s.has_comwel_new_signal
                            then 20
                        else 0
                    end
                )
                + (case when s.verified_license_date is not null then 20 else 0 end)
            )::integer as confidence_score
        from signals s
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
        s.confidence_score,
        case
            when s.confidence_score >= 80 then 'high'
            when s.confidence_score >= 50 then 'medium'
            when s.confidence_score >= 30 then 'low'
            else 'pending'
        end,
        array_remove(
            array[
                case when s.has_nps_new_signal
                    then 'nps_recent_applied' end,
                case when s.has_nps_new_signal or s.has_comwel_new_signal
                    then 'no_earlier_employment_history' end,
                case when s.has_comwel_new_signal
                    then 'comwel_first_seen' end,
                case when s.verified_license_date is not null
                    then 'license_date_match' end
            ]::text[],
            null
        ),
        coalesce(s.verified_license_date, s.verified_nps_date),
        coalesce(
            extract(year from coalesce(
                s.verified_license_date,
                s.verified_nps_date
            ))::integer,
            s.opening_signal_year
        ),
        case
            when s.verified_license_date is not null
              or s.verified_nps_date is not null
                then 'day'
            when s.opening_signal_year is not null then 'year'
            else ''
        end,
        case
            when s.verified_license_date is not null then 'license_date'
            when s.verified_nps_date is not null then 'nps_applied_on'
            when s.has_comwel_new_signal then 'comwel_first_seen'
            else ''
        end,
        s.verified_license_date
    from scored s;
$$;

revoke all on function public.oasis_search_recent_openings_v3(
    text,
    text,
    text,
    integer,
    integer,
    text[],
    text[],
    integer,
    boolean,
    integer,
    text
) from public, anon, authenticated;

grant execute on function public.oasis_search_recent_openings_v3(
    text,
    text,
    text,
    integer,
    integer,
    text[],
    text[],
    integer,
    boolean,
    integer,
    text
) to service_role;

comment on function public.oasis_search_recent_openings_v3(
    text,
    text,
    text,
    integer,
    integer,
    text[],
    text[],
    integer,
    boolean,
    integer,
    text
) is 'Recent-opening search enriched with free NPS, COMWEL, and strict LOCALDATA license-date confidence signals.';

commit;
