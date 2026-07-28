-- OASIS CRM v10.1.2
-- 신규개업 추정 기업 발굴
-- 원천은 국민연금 월별 사업장 자료와 근로복지공단 연간 자료만 사용한다.

alter table public.oasis_prospect_search_history
    add column if not exists discovery_type text not null default 'growth',
    add column if not exists recent_months integer not null default 6,
    add column if not exists include_comwel_annual boolean not null default true;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'oasis_prospect_search_history_discovery_type_check'
          and conrelid = 'public.oasis_prospect_search_history'::regclass
    ) then
        alter table public.oasis_prospect_search_history
        add constraint oasis_prospect_search_history_discovery_type_check
        check (discovery_type in ('growth', 'recent_opening'));
    end if;
end $$;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'oasis_prospect_search_history_recent_months_check'
          and conrelid = 'public.oasis_prospect_search_history'::regclass
    ) then
        alter table public.oasis_prospect_search_history
        add constraint oasis_prospect_search_history_recent_months_check
        check (recent_months in (3, 6, 12));
    end if;
end $$;

create index if not exists idx_oasis_nps_recent_opening
    on public.oasis_nps_employee_snapshots (
        province_code,
        applied_on desc,
        data_created_ym desc,
        employee_count desc
    )
    where applied_on is not null
      and business_no ~ '^[0-9]{10}$';

create index if not exists idx_oasis_comwel_recent_opening
    on public.oasis_comwel_annual_growth (
        province,
        district,
        workers_2025 desc,
        business_no
    )
    where is_new_2025;

create index if not exists idx_oasis_employment_contacts_business_no
    on public.oasis_employment_contacts (
        business_no,
        checked_at desc,
        contact_key
    )
    where business_no ~ '^[0-9]{10}$';

create or replace function public.oasis_enqueue_nps_recent_opening_contact()
returns trigger
language plpgsql
set search_path = public
as $$
declare
    district_value text;
begin
    if new.business_no ~ '^[0-9]{10}$'
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
            current_employee_count,
            current_period,
            growth_frequency,
            is_new_company
        )
        values (
            'business:' || new.business_no,
            'nps_monthly',
            new.snapshot_identity,
            new.business_no,
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
            'monthly',
            true
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
            updated_at = now();
    end if;
    return new;
end;
$$;

revoke execute on function public.oasis_enqueue_nps_recent_opening_contact()
    from public, anon, authenticated;

drop trigger if exists oasis_enqueue_nps_recent_opening_contact_trigger
    on public.oasis_nps_employee_snapshots;
create trigger oasis_enqueue_nps_recent_opening_contact_trigger
after insert or update of
    business_no,
    company_name,
    address,
    province_code,
    district_code,
    industry_code,
    industry_name,
    employee_count,
    data_created_ym,
    applied_on,
    withdrawn_on
on public.oasis_nps_employee_snapshots
for each row execute function public.oasis_enqueue_nps_recent_opening_contact();

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
            current_employee_count,
            current_period,
            growth_frequency,
            is_new_company
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
            new.workers_2025,
            '2025',
            'annual',
            true
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
            updated_at = now();
    end if;
    return new;
end;
$$;

revoke execute on function public.oasis_enqueue_comwel_recent_opening_contact()
    from public, anon, authenticated;

drop trigger if exists oasis_enqueue_comwel_recent_opening_contact_trigger
    on public.oasis_comwel_annual_growth;
create trigger oasis_enqueue_comwel_recent_opening_contact_trigger
after insert or update of
    business_no,
    company_name,
    address,
    province,
    district,
    industry_code,
    industry_name,
    workers_2025,
    is_new_2025
on public.oasis_comwel_annual_growth
for each row execute function public.oasis_enqueue_comwel_recent_opening_contact();

-- 이미 적재된 최근 국민연금 사업장도 연락처 보강 대기열에 한 번만 넣는다.
with latest_nps as (
    select distinct on (business_no)
        business_no,
        snapshot_identity,
        company_name,
        address,
        province_code,
        district_code,
        industry_code,
        industry_name,
        employee_count,
        data_created_ym
    from public.oasis_nps_employee_snapshots
    where business_no ~ '^[0-9]{10}$'
      and applied_on >= current_date - interval '12 months'
      and (withdrawn_on is null or withdrawn_on > current_date)
    order by business_no, data_created_ym desc, updated_at desc
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
    growth_frequency,
    is_new_company
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
    employee_count,
    data_created_ym,
    'monthly',
    true
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
    updated_at = now();

-- 근로복지공단은 정확한 개업일이 아니라 2025년 자료에 처음 등장한 신호다.
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
    current_employee_count,
    current_period,
    growth_frequency,
    is_new_company
)
select
    'business:' || business_no,
    'comwel_annual',
    business_no,
    business_no,
    company_name,
    address,
    province,
    district,
    industry_name,
    case province
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
    industry_code,
    industry_category,
    workers_2025,
    '2025',
    'annual',
    true
from public.oasis_comwel_annual_growth
where is_new_2025
  and business_no ~ '^[0-9]{10}$'
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
    updated_at = now();

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
    with nps_ranked as (
        select
            'nps_monthly'::text as source_type,
            s.snapshot_identity as source_record_key,
            s.business_no,
            s.company_name,
            s.address,
            case s.province_code
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
                else ''
            end as province_name,
            coalesce(
                (regexp_split_to_array(trim(s.address), '\s+'))[2],
                ''
            ) as district_name,
            s.province_code,
            s.district_code,
            s.industry_code,
            s.industry_name,
            public.oasis_growth_industry_category(
                s.industry_name
            ) as industry_category,
            s.employee_count as current_employee_count,
            s.applied_on as opening_signal_date,
            extract(year from s.applied_on)::integer as opening_signal_year,
            'nps_applied_on'::text as opening_signal_basis,
            'day'::text as opening_signal_precision,
            s.data_created_ym as source_period,
            row_number() over (
                partition by s.business_no
                order by s.data_created_ym desc, s.updated_at desc
            ) as source_rank
        from public.oasis_nps_employee_snapshots s
        where s.business_no ~ '^[0-9]{10}$'
          and s.applied_on >= current_date - make_interval(
              months => case
                  when p_recent_months in (3, 6, 12)
                      then p_recent_months
                  else 6
              end
          )
          and (s.withdrawn_on is null or s.withdrawn_on > current_date)
          and s.employee_count between
              greatest(1, p_min_employees)
              and greatest(
                  greatest(1, p_min_employees),
                  p_max_employees
              )
          and (
              trim(coalesce(p_province_code, '')) = ''
              or s.province_code = trim(p_province_code)
          )
          and (
              trim(coalesce(p_district, '')) = ''
              or coalesce(
                  (regexp_split_to_array(trim(s.address), '\s+'))[2],
                  ''
              ) = trim(p_district)
          )
          and (
              coalesce(cardinality(p_industries), 0) = 0
              or public.oasis_growth_industry_category(
                  s.industry_name
              ) = any(p_industries)
          )
    ),
    nps_recent as (
        select *
        from nps_ranked
        where source_rank = 1
        order by
            opening_signal_date desc,
            current_employee_count desc
        limit 5000
    ),
    comwel_recent as (
        select
            'comwel_annual'::text as source_type,
            w.business_no as source_record_key,
            w.business_no,
            w.company_name,
            w.address,
            w.province as province_name,
            w.district as district_name,
            case w.province
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
            end as province_code,
            ''::text as district_code,
            w.industry_code,
            w.industry_name,
            w.industry_category,
            w.workers_2025 as current_employee_count,
            null::date as opening_signal_date,
            2025 as opening_signal_year,
            'comwel_first_seen_2025'::text as opening_signal_basis,
            'year'::text as opening_signal_precision,
            '2025'::text as source_period,
            1::bigint as source_rank
        from public.oasis_comwel_annual_growth w
        where p_include_comwel_annual
          and w.is_new_2025
          and w.business_no ~ '^[0-9]{10}$'
          and w.workers_2025 between
              greatest(1, p_min_employees)
              and greatest(
                  greatest(1, p_min_employees),
                  p_max_employees
              )
          and (
              trim(coalesce(p_province_name, '')) = ''
              or w.province = trim(p_province_name)
          )
          and (
              trim(coalesce(p_district, '')) = ''
              or w.district = trim(p_district)
          )
          and (
              coalesce(cardinality(p_industries), 0) = 0
              or w.industry_category = any(p_industries)
          )
        order by w.workers_2025 desc, w.business_no
        limit 5000
    ),
    combined as (
        select
            source_type,
            source_record_key,
            business_no,
            company_name,
            address,
            province_name,
            district_name,
            province_code,
            district_code,
            industry_code,
            industry_name,
            industry_category,
            current_employee_count,
            opening_signal_date,
            opening_signal_year,
            opening_signal_basis,
            opening_signal_precision,
            source_period
        from nps_recent
        union all
        select
            source_type,
            source_record_key,
            business_no,
            company_name,
            address,
            province_name,
            district_name,
            province_code,
            district_code,
            industry_code,
            industry_name,
            industry_category,
            current_employee_count,
            opening_signal_date,
            opening_signal_year,
            opening_signal_basis,
            opening_signal_precision,
            source_period
        from comwel_recent
    ),
    deduplicated as (
        select
            combined.*,
            row_number() over (
                partition by combined.business_no
                order by
                    case combined.source_type
                        when 'nps_monthly' then 0
                        else 1
                    end,
                    combined.opening_signal_date desc nulls last,
                    combined.current_employee_count desc
            ) as business_rank
        from combined
    ),
    shortlisted as (
        select *
        from deduplicated
        where business_rank = 1
        order by
            opening_signal_date desc nulls last,
            opening_signal_year desc,
            current_employee_count desc
        limit least(
            5000,
            case
                when coalesce(cardinality(p_contact_channels), 0) = 0
                    then least(500, greatest(1, p_limit))
                else greatest(
                    1000,
                    least(500, greatest(1, p_limit)) * 20
                )
            end
        )
    ),
    with_contact as (
        select
            d.*,
            c.mobile_phone,
            c.landline_phone,
            c.email,
            c.instagram_id,
            c.instagram_url,
            c.status,
            c.checked_at,
            c.has_mobile_phone,
            c.has_landline_phone,
            c.has_email,
            c.has_instagram
        from shortlisted d
        left join lateral (
            select candidates.*
            from (
                select c1.*, 0 as key_rank
                from public.oasis_employment_contacts c1
                where c1.contact_key = 'business:' || d.business_no
                union all
                select c2.*, 1 as key_rank
                from public.oasis_employment_contacts c2
                where c2.business_no = d.business_no
                  and c2.contact_key <> 'business:' || d.business_no
            ) candidates
            order by
                (
                    candidates.has_mobile_phone
                    or candidates.has_landline_phone
                    or candidates.has_email
                    or candidates.has_instagram
                ) desc,
                candidates.key_rank,
                candidates.checked_at desc nulls last
            limit 1
        ) c on true
    )
    select
        source_type,
        source_record_key,
        business_no,
        company_name,
        address,
        province_name,
        district_name,
        province_code,
        district_code,
        industry_code,
        industry_name,
        industry_category,
        current_employee_count,
        opening_signal_date,
        opening_signal_year,
        opening_signal_basis,
        opening_signal_precision,
        source_period,
        coalesce(mobile_phone, ''),
        coalesce(landline_phone, ''),
        coalesce(email, ''),
        coalesce(instagram_id, ''),
        coalesce(instagram_url, ''),
        coalesce(status, 'pending'),
        checked_at
    from with_contact
    where
        coalesce(cardinality(p_contact_channels), 0) = 0
        or (
            'mobile_phone' = any(p_contact_channels)
            and coalesce(has_mobile_phone, false)
        )
        or (
            'landline_phone' = any(p_contact_channels)
            and coalesce(has_landline_phone, false)
        )
        or (
            'email' = any(p_contact_channels)
            and coalesce(has_email, false)
        )
        or (
            'instagram' = any(p_contact_channels)
            and coalesce(has_instagram, false)
        )
    order by
        opening_signal_date desc nulls last,
        opening_signal_year desc,
        current_employee_count desc
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
    '행안부 자료를 제외하고 국민연금 적용일과 근로복지공단 2025년 최초 등장으로 신규개업 추정 기업을 조회';

analyze public.oasis_nps_employee_snapshots;
analyze public.oasis_comwel_annual_growth;
analyze public.oasis_employment_contacts;
