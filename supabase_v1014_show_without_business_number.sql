-- OASIS CRM v10.1.4
-- 사업자등록번호가 없는 국민연금 사업장도 공단 사업장 식별키로
-- 신규개업 조회와 영업후보 저장에 사용할 수 있게 한다.

create or replace function public.oasis_enqueue_nps_recent_opening_contact()
returns trigger
language plpgsql
set search_path = public
as $$
declare
    district_value text;
    business_no_value text;
    normalized_name text;
    normalized_address text;
    target_contact_key text;
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

    normalized_name := regexp_replace(
        lower(coalesce(new.company_name, '')),
        '[^0-9a-z가-힣]',
        '',
        'g'
    );
    normalized_address := regexp_replace(
        lower(coalesce(new.address, '')),
        '[^0-9a-z가-힣]',
        '',
        'g'
    );

    if coalesce(business_no_value, '') ~ '^[0-9]{10}$' then
        target_contact_key := 'business:' || business_no_value;
    elsif normalized_name <> '' and normalized_address <> '' then
        business_no_value := '';
        target_contact_key := 'place:' || md5(
            normalized_name || '|' || normalized_address
        );
    else
        return new;
    end if;

    if new.applied_on is not null
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
            current_employee_count,
            current_period,
            is_new_company,
            opening_signal_date,
            opening_signal_year,
            opening_signal_basis,
            opening_signal_precision,
            opening_employee_count
        )
        values (
            target_contact_key,
            'nps_monthly',
            new.snapshot_identity,
            coalesce(business_no_value, ''),
            new.company_name,
            new.address,
            new.province_code,
            district_value,
            new.industry_name,
            new.province_code,
            new.district_code,
            new.industry_code,
            public.oasis_growth_industry_category(new.industry_name),
            new.employee_count,
            new.data_created_ym,
            true,
            new.applied_on,
            extract(year from new.applied_on)::integer,
            'nps_applied_on',
            'day',
            new.employee_count
        )
        on conflict (contact_key) do update
        set
            source_type = excluded.source_type,
            source_record_key = excluded.source_record_key,
            business_no = case
                when excluded.business_no ~ '^[0-9]{10}$'
                    then excluded.business_no
                else public.oasis_employment_contacts.business_no
            end,
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

-- 기존 12개월 스냅샷을 사업자번호가 있으면 사업자 키로, 없으면
-- 기존 연락처 수집 파이프라인과 같은 상호·주소 키로 연락처 캐시에 넣는다.
create temporary table oasis_recent_nps_name_map_work_1014
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
    on oasis_recent_nps_name_map_work_1014 (normalized_company_name);
analyze oasis_recent_nps_name_map_work_1014;

with recent_nps as materialized (
    select
        s.snapshot_identity,
        s.business_no,
        s.company_name,
        s.address,
        s.province_code,
        s.district_code,
        s.industry_code,
        s.industry_name,
        s.employee_count,
        s.data_created_ym,
        s.applied_on,
        s.updated_at,
        public.oasis_normalize_company_name(s.company_name)
            as normalized_company_name,
        regexp_replace(
            lower(coalesce(s.company_name, '')),
            '[^0-9a-z가-힣]',
            '',
            'g'
        ) as place_name,
        regexp_replace(
            lower(coalesce(s.address, '')),
            '[^0-9a-z가-힣]',
            '',
            'g'
        ) as place_address
    from public.oasis_nps_employee_snapshots s
    where s.applied_on >= current_date - interval '12 months'
      and (s.withdrawn_on is null or s.withdrawn_on > current_date)
),
resolved_nps as (
    select
        snapshot.*,
        case
            when regexp_replace(
                coalesce(snapshot.business_no, ''),
                '[^0-9]',
                '',
                'g'
            ) ~ '^[0-9]{10}$'
                then regexp_replace(
                    snapshot.business_no,
                    '[^0-9]',
                    '',
                    'g'
                )
            else coalesce(matched.business_no, '')
        end as resolved_business_no
    from recent_nps snapshot
    left join oasis_recent_nps_name_map_work_1014 matched
      on matched.normalized_company_name
         = snapshot.normalized_company_name
    where snapshot.place_name <> ''
      and snapshot.place_address <> ''
),
keyed_nps as (
    select
        resolved.*,
        case
            when resolved.resolved_business_no ~ '^[0-9]{10}$'
                then 'business:' || resolved.resolved_business_no
            else 'place:' || md5(
                resolved.place_name || '|' || resolved.place_address
            )
        end as target_contact_key
    from resolved_nps resolved
),
latest_nps as (
    select distinct on (target_contact_key)
        *
    from keyed_nps
    order by
        target_contact_key,
        data_created_ym desc,
        updated_at desc
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
    current_employee_count,
    current_period,
    is_new_company,
    opening_signal_date,
    opening_signal_year,
    opening_signal_basis,
    opening_signal_precision,
    opening_employee_count
)
select
    target_contact_key,
    'nps_monthly',
    snapshot_identity,
    resolved_business_no,
    company_name,
    address,
    province_code,
    coalesce((regexp_split_to_array(trim(address), '\s+'))[2], ''),
    industry_name,
    province_code,
    district_code,
    industry_code,
    public.oasis_growth_industry_category(industry_name),
    employee_count,
    data_created_ym,
    true,
    applied_on,
    extract(year from applied_on)::integer,
    'nps_applied_on',
    'day',
    employee_count
from latest_nps
on conflict (contact_key) do update
set
    source_type = excluded.source_type,
    source_record_key = excluded.source_record_key,
    business_no = case
        when excluded.business_no ~ '^[0-9]{10}$'
            then excluded.business_no
        else public.oasis_employment_contacts.business_no
    end,
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

create index if not exists idx_oasis_contacts_nps_opening_search_all
    on public.oasis_employment_contacts (
        province_code,
        opening_signal_date desc,
        opening_employee_count desc,
        contact_key
    )
    where opening_signal_basis = 'nps_applied_on';

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
            when 'nps_applied_on'
                then to_char(c.opening_signal_date, 'YYYYMM')
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
    where trim(coalesce(c.source_record_key, '')) <> ''
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
        c.opening_employee_count desc,
        c.contact_key
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

comment on function public.oasis_search_recent_openings(
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
) is
    '사업자번호 유무와 관계없이 국민연금·근로복지공단 신규개업 추정 후보를 조회';

analyze public.oasis_employment_contacts;
