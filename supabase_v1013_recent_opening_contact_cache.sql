-- OASIS CRM v10.1.3
-- 신규개업 조회를 원천 스냅샷 전체가 아닌 공용 연락처 캐시에서 수행한다.

alter table public.oasis_employment_contacts
    add column if not exists opening_signal_date date,
    add column if not exists opening_signal_year integer,
    add column if not exists opening_signal_basis text not null default '',
    add column if not exists opening_signal_precision text not null default '',
    add column if not exists opening_employee_count integer not null default 0;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'oasis_employment_contacts_opening_year_check'
          and conrelid = 'public.oasis_employment_contacts'::regclass
    ) then
        alter table public.oasis_employment_contacts
        add constraint oasis_employment_contacts_opening_year_check
        check (
            opening_signal_year is null
            or opening_signal_year between 1900 and 2200
        );
    end if;
end $$;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'oasis_employment_contacts_opening_precision_check'
          and conrelid = 'public.oasis_employment_contacts'::regclass
    ) then
        alter table public.oasis_employment_contacts
        add constraint oasis_employment_contacts_opening_precision_check
        check (opening_signal_precision in ('', 'day', 'year'));
    end if;
end $$;

create or replace function public.oasis_normalize_company_name(
    p_company_name text
)
returns text
language sql
immutable
parallel safe
set search_path = public, pg_temp
as $$
    select regexp_replace(
        lower(
            regexp_replace(
                coalesce(p_company_name, ''),
                '([/-]\s*\(?(일용|상용)\)?).*$', '', 'i'
            )
        ),
        '(주식회사|유한회사|합자회사|합명회사|\(주\)|㈜|\(유\)|[^가-힣a-z0-9])',
        '',
        'g'
    );
$$;

revoke execute on function public.oasis_normalize_company_name(text)
    from public, anon, authenticated;
grant execute on function public.oasis_normalize_company_name(text)
    to service_role;

create or replace function public.oasis_enqueue_nps_recent_opening_contact()
returns trigger
language plpgsql
set search_path = public
as $$
declare
    district_value text;
    business_no_value text;
begin
    business_no_value := regexp_replace(
        coalesce(new.business_no, ''),
        '[^0-9]',
        '',
        'g'
    );
    if business_no_value !~ '^[0-9]{10}$' then
        select min(c.business_no)
          into business_no_value
          from public.oasis_employment_contacts c
         where c.contact_key like 'business:%'
           and c.business_no ~ '^[0-9]{10}$'
           and public.oasis_normalize_company_name(c.company_name)
               = public.oasis_normalize_company_name(new.company_name)
        having count(distinct c.business_no) = 1;
    end if;

    if business_no_value ~ '^[0-9]{10}$'
       and new.applied_on is not null
       and new.applied_on >= current_date - interval '12 months'
       and (new.withdrawn_on is null or new.withdrawn_on > current_date) then
        district_value := coalesce(
            (regexp_split_to_array(trim(new.address), '\s+'))[2],
            ''
        );
        insert into public.oasis_employment_contacts (
            contact_key,
            source_type,
            source_record_key,
            business_no,
            company_name,
            address,
            province,
            district,
            industry_name,
            province_code,
            district_code,
            industry_code,
            industry_category,
            is_new_company,
            opening_signal_date,
            opening_signal_year,
            opening_signal_basis,
            opening_signal_precision,
            opening_employee_count
        )
        values (
            'business:' || business_no_value,
            'nps_monthly',
            new.snapshot_identity,
            business_no_value,
            new.company_name,
            new.address,
            new.province_code,
            district_value,
            new.industry_name,
            new.province_code,
            new.district_code,
            new.industry_code,
            public.oasis_growth_industry_category(new.industry_name),
            true,
            new.applied_on,
            extract(year from new.applied_on)::integer,
            'nps_applied_on',
            'day',
            new.employee_count
        )
        on conflict (contact_key) do update
        set
            business_no = excluded.business_no,
            company_name = excluded.company_name,
            address = excluded.address,
            province = excluded.province,
            district = excluded.district,
            industry_name = excluded.industry_name,
            province_code = excluded.province_code,
            district_code = excluded.district_code,
            industry_code = excluded.industry_code,
            industry_category = excluded.industry_category,
            is_new_company = true,
            opening_signal_date = excluded.opening_signal_date,
            opening_signal_year = excluded.opening_signal_year,
            opening_signal_basis = excluded.opening_signal_basis,
            opening_signal_precision = excluded.opening_signal_precision,
            opening_employee_count = excluded.opening_employee_count,
            updated_at = now();
    end if;
    return new;
end;
$$;

revoke execute on function public.oasis_enqueue_nps_recent_opening_contact()
    from public, anon, authenticated;

create or replace function public.oasis_enqueue_comwel_recent_opening_contact()
returns trigger
language plpgsql
set search_path = public
as $$
begin
    if new.is_new_2025
       and new.business_no ~ '^[0-9]{10}$' then
        insert into public.oasis_employment_contacts (
            contact_key,
            source_type,
            source_record_key,
            business_no,
            company_name,
            address,
            province,
            district,
            industry_name,
            province_code,
            industry_code,
            industry_category,
            is_new_company,
            opening_signal_year,
            opening_signal_basis,
            opening_signal_precision,
            opening_employee_count
        )
        values (
            'business:' || new.business_no,
            'comwel_annual',
            new.business_no,
            new.business_no,
            new.company_name,
            new.address,
            new.province,
            new.district,
            new.industry_name,
            case new.province
                when '서울특별시' then '11'
                when '부산광역시' then '26'
                when '대구광역시' then '27'
                when '인천광역시' then '28'
                when '광주광역시' then '29'
                when '대전광역시' then '30'
                when '울산광역시' then '31'
                when '세종특별자치시' then '36'
                when '경기도' then '41'
                when '강원특별자치도' then '51'
                when '충청북도' then '43'
                when '충청남도' then '44'
                when '전북특별자치도' then '52'
                when '전라남도' then '46'
                when '경상북도' then '47'
                when '경상남도' then '48'
                when '제주특별자치도' then '50'
                else ''
            end,
            new.industry_code,
            new.industry_category,
            true,
            2025,
            'comwel_first_seen_2025',
            'year',
            new.workers_2025
        )
        on conflict (contact_key) do update
        set
            business_no = excluded.business_no,
            company_name = excluded.company_name,
            address = excluded.address,
            province = excluded.province,
            district = excluded.district,
            industry_name = excluded.industry_name,
            province_code = excluded.province_code,
            industry_code = excluded.industry_code,
            industry_category = excluded.industry_category,
            is_new_company = true,
            opening_signal_year = case
                when public.oasis_employment_contacts.opening_signal_basis
                    = 'nps_applied_on'
                    then public.oasis_employment_contacts.opening_signal_year
                else excluded.opening_signal_year
            end,
            opening_signal_basis = case
                when public.oasis_employment_contacts.opening_signal_basis
                    = 'nps_applied_on'
                    then public.oasis_employment_contacts.opening_signal_basis
                else excluded.opening_signal_basis
            end,
            opening_signal_precision = case
                when public.oasis_employment_contacts.opening_signal_basis
                    = 'nps_applied_on'
                    then public.oasis_employment_contacts.opening_signal_precision
                else excluded.opening_signal_precision
            end,
            opening_employee_count = case
                when public.oasis_employment_contacts.opening_signal_basis
                    = 'nps_applied_on'
                    then public.oasis_employment_contacts.opening_employee_count
                else excluded.opening_employee_count
            end,
            updated_at = now();
    end if;
    return new;
end;
$$;

revoke execute on function public.oasis_enqueue_comwel_recent_opening_contact()
    from public, anon, authenticated;

create index if not exists idx_oasis_contacts_normalized_company_business
    on public.oasis_employment_contacts (
        public.oasis_normalize_company_name(company_name),
        business_no
    )
    where contact_key like 'business:%'
      and business_no ~ '^[0-9]{10}$';

-- 연간 신규 신호를 기존 사업자번호 연락처 행에 연결한다.
update public.oasis_employment_contacts c
set
    is_new_company = true,
    opening_signal_year = 2025,
    opening_signal_basis = 'comwel_first_seen_2025',
    opening_signal_precision = 'year',
    opening_employee_count = w.workers_2025,
    updated_at = now()
from public.oasis_comwel_annual_growth w
where w.is_new_2025
  and w.business_no ~ '^[0-9]{10}$'
  and c.contact_key = 'business:' || w.business_no
  and c.opening_signal_basis = '';

-- 국민연금 적용일은 일 단위이므로 같은 업체의 연간 신호보다 우선한다.
create temporary table oasis_recent_nps_name_map_work_1013
on commit drop
as
with recent_names as materialized (
    select distinct
        public.oasis_normalize_company_name(company_name)
            as normalized_company_name
    from public.oasis_nps_employee_snapshots
    where applied_on >= current_date - interval '12 months'
      and (withdrawn_on is null or withdrawn_on > current_date)
      and public.oasis_normalize_company_name(company_name) <> ''
)
select
    recent.normalized_company_name,
    min(c.business_no) as business_no
from recent_names recent
join public.oasis_employment_contacts c
  on public.oasis_normalize_company_name(c.company_name)
     = recent.normalized_company_name
where c.contact_key like 'business:%'
  and c.business_no ~ '^[0-9]{10}$'
group by recent.normalized_company_name
having count(distinct c.business_no) = 1;

create unique index
    on oasis_recent_nps_name_map_work_1013 (normalized_company_name);
analyze oasis_recent_nps_name_map_work_1013;

with recent_nps as materialized (
    select
        snapshot_identity,
        company_name,
        address,
        province_code,
        district_code,
        industry_code,
        industry_name,
        employee_count,
        data_created_ym,
        applied_on,
        updated_at,
        public.oasis_normalize_company_name(company_name)
            as normalized_company_name
    from public.oasis_nps_employee_snapshots
    where applied_on >= current_date - interval '12 months'
      and (withdrawn_on is null or withdrawn_on > current_date)
),
latest_nps as (
    select distinct on (matched.business_no)
        matched.business_no,
        snapshot.snapshot_identity,
        snapshot.company_name,
        snapshot.address,
        snapshot.province_code,
        snapshot.district_code,
        snapshot.industry_code,
        snapshot.industry_name,
        snapshot.employee_count,
        snapshot.data_created_ym,
        snapshot.applied_on
    from recent_nps snapshot
    join oasis_recent_nps_name_map_work_1013 matched
      on matched.normalized_company_name
         = snapshot.normalized_company_name
    order by
        matched.business_no,
        snapshot.data_created_ym desc,
        snapshot.updated_at desc
)
insert into public.oasis_employment_contacts (
    contact_key,
    source_type,
    source_record_key,
    business_no,
    company_name,
    address,
    province,
    district,
    industry_name,
    province_code,
    district_code,
    industry_code,
    industry_category,
    is_new_company,
    opening_signal_date,
    opening_signal_year,
    opening_signal_basis,
    opening_signal_precision,
    opening_employee_count
)
select
    'business:' || business_no,
    'nps_monthly',
    snapshot_identity,
    business_no,
    company_name,
    address,
    province_code,
    coalesce((regexp_split_to_array(trim(address), '\s+'))[2], ''),
    industry_name,
    province_code,
    district_code,
    industry_code,
    public.oasis_growth_industry_category(industry_name),
    true,
    applied_on,
    extract(year from applied_on)::integer,
    'nps_applied_on',
    'day',
    employee_count
from latest_nps
on conflict (contact_key) do update
set
    business_no = excluded.business_no,
    company_name = excluded.company_name,
    address = excluded.address,
    province = excluded.province,
    district = excluded.district,
    industry_name = excluded.industry_name,
    province_code = excluded.province_code,
    district_code = excluded.district_code,
    industry_code = excluded.industry_code,
    industry_category = excluded.industry_category,
    is_new_company = true,
    opening_signal_date = excluded.opening_signal_date,
    opening_signal_year = excluded.opening_signal_year,
    opening_signal_basis = excluded.opening_signal_basis,
    opening_signal_precision = excluded.opening_signal_precision,
    opening_employee_count = excluded.opening_employee_count,
    updated_at = now();

create index if not exists idx_oasis_contacts_nps_opening_search
    on public.oasis_employment_contacts (
        province_code,
        opening_signal_date desc,
        opening_employee_count desc,
        contact_key
    )
    where opening_signal_basis = 'nps_applied_on'
      and business_no ~ '^[0-9]{10}$';

create index if not exists idx_oasis_contacts_comwel_opening_search
    on public.oasis_employment_contacts (
        province_code,
        district,
        opening_employee_count desc,
        contact_key
    )
    where opening_signal_basis = 'comwel_first_seen_2025'
      and business_no ~ '^[0-9]{10}$';

create or replace function public.oasis_search_recent_openings(
    p_province_code text default '',
    p_province_name text default '',
    p_district text default '',
    p_min_employees integer default 1,
    p_max_employees integer default 300,
    p_industries text[] default '{}',
    p_contact_channels text[] default '{}',
    p_recent_months integer default 6,
    p_include_comwel_annual boolean default true,
    p_limit integer default 100
)
returns table (
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
    contact_checked_at timestamptz
)
language sql
stable
security invoker
set search_path = public
as $$
    select
        case c.opening_signal_basis
            when 'nps_applied_on' then 'nps_monthly'
            else 'comwel_annual'
        end as source_type,
        c.source_record_key,
        c.business_no,
        c.company_name,
        c.address,
        case c.province_code
            when '11' then '서울특별시'
            when '26' then '부산광역시'
            when '27' then '대구광역시'
            when '28' then '인천광역시'
            when '29' then '광주광역시'
            when '30' then '대전광역시'
            when '31' then '울산광역시'
            when '36' then '세종특별자치시'
            when '41' then '경기도'
            when '51' then '강원특별자치도'
            when '43' then '충청북도'
            when '44' then '충청남도'
            when '52' then '전북특별자치도'
            when '46' then '전라남도'
            when '47' then '경상북도'
            when '48' then '경상남도'
            when '50' then '제주특별자치도'
            else c.province
        end as province_name,
        c.district as district_name,
        c.province_code,
        c.district_code,
        c.industry_code,
        c.industry_name,
        c.industry_category,
        c.opening_employee_count as current_employee_count,
        c.opening_signal_date,
        c.opening_signal_year,
        c.opening_signal_basis,
        c.opening_signal_precision,
        case c.opening_signal_basis
            when 'nps_applied_on' then to_char(c.opening_signal_date, 'YYYYMM')
            else coalesce(c.opening_signal_year::text, '2025')
        end as source_period,
        c.mobile_phone,
        c.landline_phone,
        c.email,
        c.instagram_id as instagram,
        c.instagram_url,
        c.status as contact_status,
        c.checked_at as contact_checked_at
    from public.oasis_employment_contacts c
    where c.contact_key = 'business:' || c.business_no
      and c.business_no ~ '^[0-9]{10}$'
      and c.opening_employee_count between
          greatest(1, p_min_employees)
          and greatest(greatest(1, p_min_employees), p_max_employees)
      and (
          (
              c.opening_signal_basis = 'nps_applied_on'
              and c.opening_signal_date >= current_date - make_interval(
                  months => case
                      when p_recent_months in (3, 6, 12)
                          then p_recent_months
                      else 6
                  end
              )
          )
          or (
              p_include_comwel_annual
              and c.opening_signal_basis = 'comwel_first_seen_2025'
          )
      )
      and (
          trim(coalesce(p_province_code, '')) = ''
          or c.province_code = trim(p_province_code)
      )
      and (
          trim(coalesce(p_province_name, '')) = ''
          or case c.province_code
              when '11' then '서울특별시'
              when '26' then '부산광역시'
              when '27' then '대구광역시'
              when '28' then '인천광역시'
              when '29' then '광주광역시'
              when '30' then '대전광역시'
              when '31' then '울산광역시'
              when '36' then '세종특별자치시'
              when '41' then '경기도'
              when '51' then '강원특별자치도'
              when '43' then '충청북도'
              when '44' then '충청남도'
              when '52' then '전북특별자치도'
              when '46' then '전라남도'
              when '47' then '경상북도'
              when '48' then '경상남도'
              when '50' then '제주특별자치도'
              else c.province
          end = trim(p_province_name)
      )
      and (
          trim(coalesce(p_district, '')) = ''
          or c.district = trim(p_district)
      )
      and (
          coalesce(cardinality(p_industries), 0) = 0
          or c.industry_category = any(p_industries)
      )
      and (
          coalesce(cardinality(p_contact_channels), 0) = 0
          or (
              'mobile_phone' = any(p_contact_channels)
              and c.has_mobile_phone
          )
          or (
              'landline_phone' = any(p_contact_channels)
              and c.has_landline_phone
          )
          or (
              'email' = any(p_contact_channels)
              and c.has_email
          )
          or (
              'instagram' = any(p_contact_channels)
              and c.has_instagram
          )
      )
    order by
        c.opening_signal_date desc nulls last,
        c.opening_signal_year desc,
        c.opening_employee_count desc
    limit least(500, greatest(1, p_limit));
$$;

revoke execute on function public.oasis_search_recent_openings(
    text,
    text,
    text,
    integer,
    integer,
    text[],
    text[],
    integer,
    boolean,
    integer
) from public, anon, authenticated;

grant execute on function public.oasis_search_recent_openings(
    text,
    text,
    text,
    integer,
    integer,
    text[],
    text[],
    integer,
    boolean,
    integer
) to service_role;

analyze public.oasis_employment_contacts;
