-- OASIS CRM v10.0.1
-- 사전계산 성장기업의 시군구·업종·고용인원 필터와 공개 인스타그램 저장

alter table public.oasis_nps_growth_leads
    add column if not exists district_name text not null default '',
    add column if not exists industry_category text not null default '기타';

alter table public.oasis_comwel_annual_growth
    add column if not exists industry_category text not null default '기타';

alter table public.oasis_prospect_search_history
    add column if not exists district text not null default '',
    add column if not exists data_source text not null default 'combined',
    add column if not exists maximum_employees integer not null default 300,
    add column if not exists minimum_growth integer not null default 1;

alter table public.oasis_prospect_search_history
    drop constraint if exists oasis_prospect_search_history_data_source_check,
    drop constraint if exists oasis_prospect_search_history_employee_range_check,
    drop constraint if exists oasis_prospect_search_history_minimum_growth_check;

alter table public.oasis_prospect_search_history
    add constraint oasis_prospect_search_history_data_source_check
        check (data_source in ('combined', 'nps_monthly', 'comwel_annual')),
    add constraint oasis_prospect_search_history_employee_range_check
        check (
            maximum_employees >= minimum_employees
            and maximum_employees >= 1
        ),
    add constraint oasis_prospect_search_history_minimum_growth_check
        check (minimum_growth >= 1);

create or replace function public.oasis_growth_industry_category(
    industry_name_value text
)
returns text
language sql
immutable
set search_path = ''
as $$
    select case
        when coalesce(industry_name_value, '') ~
            '(병원|의원|치과|한의원|의료|요양)'
            then '병원·의원'
        when coalesce(industry_name_value, '') ~
            '(음식|한식|중식|일식|양식|분식|카페|커피|제과|주점)'
            then '음식점'
        when coalesce(industry_name_value, '') ~
            '(서비스|미용|세탁|수리|교육|학원|스포츠|여행|광고|컨설팅|임대)'
            then '서비스업'
        when coalesce(industry_name_value, '') ~
            '(도매|소매|판매|유통|전자상거래)'
            then '도소매업'
        when coalesce(industry_name_value, '') ~
            '(제조|가공|생산)'
            then '제조업'
        when coalesce(industry_name_value, '') ~
            '(건설|공사|설비|토목|인테리어)'
            then '건설업'
        else '기타'
    end
$$;

create or replace function public.oasis_set_nps_growth_filters()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    new.district_name := coalesce(
        (regexp_split_to_array(trim(new.address), '\s+'))[2],
        ''
    );
    new.industry_category :=
        public.oasis_growth_industry_category(new.industry_name);
    return new;
end
$$;

create or replace function public.oasis_set_comwel_growth_filters()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    new.industry_category :=
        public.oasis_growth_industry_category(new.industry_name);
    return new;
end
$$;

drop trigger if exists oasis_nps_growth_filter_columns
    on public.oasis_nps_growth_leads;
create trigger oasis_nps_growth_filter_columns
before insert or update of address, industry_name
on public.oasis_nps_growth_leads
for each row execute function public.oasis_set_nps_growth_filters();

drop trigger if exists oasis_comwel_growth_filter_columns
    on public.oasis_comwel_annual_growth;
create trigger oasis_comwel_growth_filter_columns
before insert or update of industry_name
on public.oasis_comwel_annual_growth
for each row execute function public.oasis_set_comwel_growth_filters();

update public.oasis_nps_growth_leads
set
    district_name = coalesce(
        (regexp_split_to_array(trim(address), '\s+'))[2],
        ''
    ),
    industry_category =
        public.oasis_growth_industry_category(industry_name)
where
    district_name = ''
    or (
        industry_category = '기타'
        and industry_name ~
            '(병원|의원|치과|한의원|의료|요양|음식|한식|중식|일식|양식|분식|카페|커피|제과|주점|서비스|미용|세탁|수리|교육|학원|스포츠|여행|광고|컨설팅|임대|도매|소매|판매|유통|전자상거래|제조|가공|생산|건설|공사|설비|토목|인테리어)'
    );

update public.oasis_comwel_annual_growth
set industry_category =
    public.oasis_growth_industry_category(industry_name)
where industry_category = '기타'
  and industry_name ~
      '(병원|의원|치과|한의원|의료|요양|음식|한식|중식|일식|양식|분식|카페|커피|제과|주점|서비스|미용|세탁|수리|교육|학원|스포츠|여행|광고|컨설팅|임대|도매|소매|판매|유통|전자상거래|제조|가공|생산|건설|공사|설비|토목|인테리어)';

create index if not exists idx_oasis_nps_fast_growth_district
on public.oasis_nps_growth_leads (
    province_code,
    district_name,
    employee_growth desc,
    current_employee_count desc
)
where current_employee_count >= 10
  and employee_growth > 0;

create index if not exists idx_oasis_nps_fast_growth_industry_category
on public.oasis_nps_growth_leads (
    province_code,
    industry_category,
    employee_growth desc,
    current_employee_count desc
)
where current_employee_count >= 10
  and employee_growth > 0;

create index if not exists idx_oasis_nps_fast_growth_district_industry
on public.oasis_nps_growth_leads (
    province_code,
    district_name,
    industry_category,
    employee_growth desc,
    current_employee_count desc
)
where current_employee_count >= 10
  and employee_growth > 0;

create index if not exists idx_oasis_comwel_fast_growth_industry_category
on public.oasis_comwel_annual_growth (
    province,
    industry_category,
    growth_2024_2025 desc,
    workers_2025 desc
)
where growth_2024_2025 > 0;

create index if not exists idx_oasis_comwel_fast_growth_district_industry
on public.oasis_comwel_annual_growth (
    province,
    district,
    industry_category,
    growth_2024_2025 desc,
    workers_2025 desc
)
where growth_2024_2025 > 0;

alter table public.oasis_prospect_contacts
    drop constraint if exists oasis_prospect_contacts_contact_type_check;

alter table public.oasis_prospect_contacts
    add constraint oasis_prospect_contacts_contact_type_check
        check (contact_type in ('phone', 'email', 'instagram', 'website'));

comment on column public.oasis_nps_growth_leads.district_name is
    '주소에서 정규화한 시군구명(DB발굴 지역 필터용)';
comment on column public.oasis_nps_growth_leads.industry_category is
    'CRM 공통 업종 대분류(DB발굴 업종 필터용)';
comment on column public.oasis_comwel_annual_growth.industry_category is
    'CRM 공통 업종 대분류(DB발굴 업종 필터용)';
comment on table public.oasis_prospect_search_history is
    'DB발굴 사용자별 지역·업종·고용인원 검색 이력';

revoke all on function public.oasis_growth_industry_category(text)
    from public, anon, authenticated;
revoke all on function public.oasis_set_nps_growth_filters()
    from public, anon, authenticated;
revoke all on function public.oasis_set_comwel_growth_filters()
    from public, anon, authenticated;
grant execute on function public.oasis_growth_industry_category(text)
    to service_role;
grant execute on function public.oasis_set_nps_growth_filters()
    to service_role;
grant execute on function public.oasis_set_comwel_growth_filters()
    to service_role;

analyze public.oasis_nps_growth_leads;
analyze public.oasis_comwel_annual_growth;
