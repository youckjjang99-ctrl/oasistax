-- 국민연금 월별·근로복지공단 연간 성장기업이 함께 사용하는 연락처 캐시
create table if not exists public.oasis_employment_contacts (
    contact_key text primary key,
    source_type text not null default '',
    source_record_key text not null default '',
    business_no text not null default '',
    company_name text not null default '',
    address text not null default '',
    province text not null default '',
    district text not null default '',
    industry_name text not null default '',
    mobile_phone text not null default '',
    landline_phone text not null default '',
    email text not null default '',
    instagram_id text not null default '',
    instagram_url text not null default '',
    has_mobile_phone boolean not null default false,
    has_landline_phone boolean not null default false,
    has_email boolean not null default false,
    has_instagram boolean not null default false,
    contact_sources jsonb not null default '{}'::jsonb,
    status text not null default 'pending'
        check (
            status in (
                'pending',
                'processing',
                'matched',
                'no_match',
                'error'
            )
        ),
    checked_at timestamptz,
    next_check_at timestamptz,
    attempt_count integer not null default 0
        check (attempt_count >= 0),
    last_error text not null default '',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

alter table public.oasis_employment_contacts enable row level security;
revoke all on table public.oasis_employment_contacts from anon, authenticated;

create or replace function public.oasis_sync_employment_contact_flags()
returns trigger
language plpgsql
set search_path = public
as $$
begin
    new.mobile_phone := trim(coalesce(new.mobile_phone, ''));
    new.landline_phone := trim(coalesce(new.landline_phone, ''));
    new.email := lower(trim(coalesce(new.email, '')));
    new.instagram_id := trim(coalesce(new.instagram_id, ''));
    new.instagram_url := trim(coalesce(new.instagram_url, ''));
    new.has_mobile_phone := new.mobile_phone <> '';
    new.has_landline_phone := new.landline_phone <> '';
    new.has_email := new.email <> '';
    new.has_instagram := new.instagram_id <> '' or new.instagram_url <> '';
    new.updated_at := now();
    return new;
end;
$$;

revoke execute on function public.oasis_sync_employment_contact_flags()
    from public, anon, authenticated;

drop trigger if exists oasis_sync_employment_contact_flags_trigger
    on public.oasis_employment_contacts;
create trigger oasis_sync_employment_contact_flags_trigger
before insert or update on public.oasis_employment_contacts
for each row execute function public.oasis_sync_employment_contact_flags();

create index if not exists idx_oasis_employment_contacts_pending
    on public.oasis_employment_contacts (created_at, contact_key)
    where status = 'pending';

create index if not exists idx_oasis_employment_contacts_due
    on public.oasis_employment_contacts (
        next_check_at,
        status,
        contact_key
    )
    where status in ('matched', 'no_match', 'error', 'processing');

alter table public.oasis_nps_growth_leads
    add column if not exists contact_key text not null default '';

create or replace function public.oasis_set_nps_contact_key()
returns trigger
language plpgsql
set search_path = public
as $$
declare
    normalized_name text;
    normalized_address text;
begin
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
    if normalized_name <> '' and normalized_address <> '' then
        new.contact_key := 'place:' || md5(
            normalized_name || '|' || normalized_address
        );
    else
        new.contact_key := '';
    end if;
    return new;
end;
$$;

revoke execute on function public.oasis_set_nps_contact_key()
    from public, anon, authenticated;

drop trigger if exists oasis_set_nps_contact_key_trigger
    on public.oasis_nps_growth_leads;
create trigger oasis_set_nps_contact_key_trigger
before insert or update of company_name, address
on public.oasis_nps_growth_leads
for each row execute function public.oasis_set_nps_contact_key();

update public.oasis_nps_growth_leads
set contact_key = 'place:' || md5(
    regexp_replace(
        lower(coalesce(company_name, '')),
        '[^0-9a-z가-힣]',
        '',
        'g'
    )
    || '|'
    || regexp_replace(
        lower(coalesce(address, '')),
        '[^0-9a-z가-힣]',
        '',
        'g'
    )
)
where contact_key = ''
  and regexp_replace(
      lower(coalesce(company_name, '')),
      '[^0-9a-z가-힣]',
      '',
      'g'
  ) <> ''
  and regexp_replace(
      lower(coalesce(address, '')),
      '[^0-9a-z가-힣]',
      '',
      'g'
  ) <> '';

create index if not exists idx_oasis_nps_growth_contact_key
    on public.oasis_nps_growth_leads (contact_key)
    where contact_key <> '';

insert into public.oasis_employment_contacts (
    contact_key,
    source_type,
    source_record_key,
    business_no,
    company_name,
    address,
    province,
    district,
    industry_name
)
select distinct on (contact_key)
    contact_key,
    'nps_monthly',
    snapshot_identity,
    business_no,
    company_name,
    address,
    province_code,
    district_name,
    industry_name
from public.oasis_nps_growth_leads
where contact_key <> ''
order by contact_key, current_ym desc, employee_growth desc
on conflict (contact_key) do update
set
    source_type = excluded.source_type,
    source_record_key = excluded.source_record_key,
    company_name = excluded.company_name,
    address = excluded.address,
    province = excluded.province,
    district = excluded.district,
    industry_name = excluded.industry_name,
    updated_at = now();

create or replace function public.oasis_enqueue_nps_employment_contact()
returns trigger
language plpgsql
set search_path = public
as $$
begin
    if new.employee_growth > 0 and new.contact_key <> '' then
        insert into public.oasis_employment_contacts (
            contact_key,
            source_type,
            source_record_key,
            business_no,
            company_name,
            address,
            province,
            district,
            industry_name
        )
        values (
            new.contact_key,
            'nps_monthly',
            new.snapshot_identity,
            new.business_no,
            new.company_name,
            new.address,
            new.province_code,
            new.district_name,
            new.industry_name
        )
        on conflict (contact_key) do update
        set
            source_type = excluded.source_type,
            source_record_key = excluded.source_record_key,
            company_name = excluded.company_name,
            address = excluded.address,
            province = excluded.province,
            district = excluded.district,
            industry_name = excluded.industry_name,
            updated_at = now();
    end if;
    return new;
end;
$$;

revoke execute on function public.oasis_enqueue_nps_employment_contact()
    from public, anon, authenticated;

drop trigger if exists oasis_enqueue_nps_employment_contact_trigger
    on public.oasis_nps_growth_leads;
create trigger oasis_enqueue_nps_employment_contact_trigger
after insert or update on public.oasis_nps_growth_leads
for each row execute function public.oasis_enqueue_nps_employment_contact();

create or replace function public.oasis_enqueue_comwel_employment_contact()
returns trigger
language plpgsql
set search_path = public
as $$
begin
    if new.growth_2024_2025 > 0
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
            industry_name
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
            new.industry_name
        )
        on conflict (contact_key) do update
        set
            source_type = excluded.source_type,
            source_record_key = excluded.source_record_key,
            business_no = excluded.business_no,
            company_name = excluded.company_name,
            address = excluded.address,
            province = excluded.province,
            district = excluded.district,
            industry_name = excluded.industry_name,
            updated_at = now();
    end if;
    return new;
end;
$$;

revoke execute on function public.oasis_enqueue_comwel_employment_contact()
    from public, anon, authenticated;

drop trigger if exists oasis_enqueue_comwel_employment_contact_trigger
    on public.oasis_comwel_annual_growth;
create trigger oasis_enqueue_comwel_employment_contact_trigger
after insert or update on public.oasis_comwel_annual_growth
for each row execute function public.oasis_enqueue_comwel_employment_contact();

alter table public.oasis_prospect_search_history
    add column if not exists contact_channels text[] not null default '{}';

create or replace function public.oasis_search_employment_growth(
    p_province_code text default '',
    p_province_name text default '',
    p_district text default '',
    p_min_employees integer default 1,
    p_max_employees integer default 300,
    p_industries text[] default '{}',
    p_contact_channels text[] default '{}',
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
    previous_employee_count integer,
    employee_growth integer,
    previous_period text,
    current_period text,
    growth_frequency text,
    is_new_company boolean,
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
    with combined as (
        select
            'nps_monthly'::text as source_type,
            n.snapshot_identity as source_record_key,
            n.business_no,
            n.company_name,
            n.address,
            case n.province_code
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
            n.district_name,
            n.province_code,
            n.district_code,
            n.industry_code,
            n.industry_name,
            n.industry_category,
            n.current_employee_count,
            n.previous_employee_count,
            n.employee_growth,
            n.previous_ym as previous_period,
            n.current_ym as current_period,
            'monthly'::text as growth_frequency,
            false as is_new_company,
            c.mobile_phone,
            c.landline_phone,
            c.email,
            c.instagram_id as instagram,
            c.instagram_url,
            c.status as contact_status,
            c.checked_at as contact_checked_at
        from public.oasis_nps_growth_leads n
        left join public.oasis_employment_contacts c
          on c.contact_key = n.contact_key
        where n.current_employee_count >= greatest(10, p_min_employees)
          and n.current_employee_count <= greatest(
              greatest(10, p_min_employees),
              p_max_employees
          )
          and n.employee_growth > 0
          and (p_province_code = '' or n.province_code = p_province_code)
          and (p_district = '' or n.district_name = p_district)
          and (
              coalesce(cardinality(p_industries), 0) = 0
              or n.industry_category = any(p_industries)
          )
          and (
              coalesce(cardinality(p_contact_channels), 0) = 0
              or (
                  'mobile_phone' = any(p_contact_channels)
                  and coalesce(c.has_mobile_phone, false)
              )
              or (
                  'landline_phone' = any(p_contact_channels)
                  and coalesce(c.has_landline_phone, false)
              )
              or (
                  'email' = any(p_contact_channels)
                  and coalesce(c.has_email, false)
              )
              or (
                  'instagram' = any(p_contact_channels)
                  and coalesce(c.has_instagram, false)
              )
          )

        union all

        select
            'comwel_annual'::text,
            w.business_no,
            w.business_no,
            w.company_name,
            w.address,
            w.province,
            w.district,
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
            end,
            ''::text,
            w.industry_code,
            w.industry_name,
            w.industry_category,
            w.workers_2025,
            w.workers_2024,
            w.growth_2024_2025,
            '2024'::text,
            '2025'::text,
            'annual'::text,
            w.is_new_2025,
            c.mobile_phone,
            c.landline_phone,
            c.email,
            c.instagram_id,
            c.instagram_url,
            c.status,
            c.checked_at
        from public.oasis_comwel_annual_growth w
        left join public.oasis_employment_contacts c
          on c.contact_key = 'business:' || w.business_no
        where w.workers_2025 >= greatest(1, p_min_employees)
          and w.workers_2025 <= least(
              9,
              greatest(greatest(1, p_min_employees), p_max_employees)
          )
          and p_min_employees <= 9
          and w.growth_2024_2025 > 0
          and (p_province_name = '' or w.province = p_province_name)
          and (p_district = '' or w.district = p_district)
          and (
              coalesce(cardinality(p_industries), 0) = 0
              or w.industry_category = any(p_industries)
          )
          and (
              coalesce(cardinality(p_contact_channels), 0) = 0
              or (
                  'mobile_phone' = any(p_contact_channels)
                  and coalesce(c.has_mobile_phone, false)
              )
              or (
                  'landline_phone' = any(p_contact_channels)
                  and coalesce(c.has_landline_phone, false)
              )
              or (
                  'email' = any(p_contact_channels)
                  and coalesce(c.has_email, false)
              )
              or (
                  'instagram' = any(p_contact_channels)
                  and coalesce(c.has_instagram, false)
              )
          )
    )
    select *
    from combined
    order by employee_growth desc, current_employee_count desc
    limit least(500, greatest(1, p_limit));
$$;

revoke execute on function public.oasis_search_employment_growth(
    text,
    text,
    text,
    integer,
    integer,
    text[],
    text[],
    integer
) from public, anon, authenticated;

grant execute on function public.oasis_search_employment_growth(
    text,
    text,
    text,
    integer,
    integer,
    text[],
    text[],
    integer
) to service_role;

comment on table public.oasis_employment_contacts is
    '국민연금 월별·근로복지공단 연간 성장기업의 공개 업무용 연락처 월간 갱신 캐시';
