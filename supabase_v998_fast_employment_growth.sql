-- 국민연금은 10명 이상 월별 증감, 근로복지공단은 전 업체 연간 증감으로 분리한다.
delete from public.oasis_nps_growth_leads
where current_employee_count < 10;

alter table public.oasis_nps_growth_leads
    drop constraint if exists oasis_nps_growth_leads_minimum_10_check;

alter table public.oasis_nps_growth_leads
    add constraint oasis_nps_growth_leads_minimum_10_check
    check (current_employee_count >= 10);

create or replace function public.oasis_refresh_nps_growth_leads(
    p_current_ym text default null,
    p_previous_ym text default null
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_current_ym text;
    v_previous_ym text;
    v_inserted bigint;
begin
    v_current_ym := coalesce(
        nullif(p_current_ym, ''),
        (
            select max(data_created_ym)
            from public.oasis_nps_employee_snapshots
        )
    );
    v_previous_ym := coalesce(
        nullif(p_previous_ym, ''),
        (
            select max(data_created_ym)
            from public.oasis_nps_employee_snapshots
            where data_created_ym < v_current_ym
        )
    );

    if v_current_ym is null or v_previous_ym is null then
        raise exception '국민연금 비교 기준월 2개가 필요합니다.';
    end if;

    delete from public.oasis_nps_growth_leads
    where current_ym = v_current_ym;

    insert into public.oasis_nps_growth_leads (
        snapshot_identity,
        current_ym,
        previous_ym,
        business_no,
        company_name,
        address,
        industry_code,
        industry_name,
        province_code,
        district_code,
        join_status_code,
        workplace_type_code,
        current_employee_count,
        previous_employee_count,
        employee_growth,
        employee_growth_rate,
        new_employee_count,
        lost_employee_count,
        source_snapshot_updated_at,
        computed_at
    )
    select
        current.snapshot_identity,
        current.data_created_ym,
        previous.data_created_ym,
        current.business_no,
        current.company_name,
        current.address,
        current.industry_code,
        current.industry_name,
        current.province_code,
        current.district_code,
        current.join_status_code,
        current.workplace_type_code,
        current.employee_count,
        previous.employee_count,
        current.employee_count - previous.employee_count,
        case
            when previous.employee_count > 0 then round(
                (
                    (current.employee_count - previous.employee_count)::numeric
                    / previous.employee_count::numeric
                ) * 100,
                2
            )
            else null
        end,
        current.new_employee_count,
        current.lost_employee_count,
        current.updated_at,
        now()
    from public.oasis_nps_employee_snapshots current
    join public.oasis_nps_employee_snapshots previous
      on previous.snapshot_identity = current.snapshot_identity
     and previous.data_created_ym = v_previous_ym
    where current.data_created_ym = v_current_ym
      and current.employee_count >= 10
      and current.employee_count > previous.employee_count
    on conflict (current_ym, snapshot_identity) do update set
        previous_ym = excluded.previous_ym,
        business_no = excluded.business_no,
        company_name = excluded.company_name,
        address = excluded.address,
        industry_code = excluded.industry_code,
        industry_name = excluded.industry_name,
        province_code = excluded.province_code,
        district_code = excluded.district_code,
        join_status_code = excluded.join_status_code,
        workplace_type_code = excluded.workplace_type_code,
        current_employee_count = excluded.current_employee_count,
        previous_employee_count = excluded.previous_employee_count,
        employee_growth = excluded.employee_growth,
        employee_growth_rate = excluded.employee_growth_rate,
        new_employee_count = excluded.new_employee_count,
        lost_employee_count = excluded.lost_employee_count,
        source_snapshot_updated_at = excluded.source_snapshot_updated_at,
        computed_at = excluded.computed_at;

    get diagnostics v_inserted = row_count;
    return jsonb_build_object(
        'current_ym', v_current_ym,
        'previous_ym', v_previous_ym,
        'rows', v_inserted
    );
end;
$$;

revoke all on function public.oasis_refresh_nps_growth_leads(text, text)
from public, anon, authenticated;
grant execute on function public.oasis_refresh_nps_growth_leads(text, text)
to service_role;

drop view if exists public.oasis_growth_crm_leads;

create view public.oasis_growth_crm_leads
with (security_invoker = true)
as
select
    snapshot.company_id,
    coalesce(master.company_name, growth.company_name) as company_name,
    coalesce(master.address, growth.address) as address,
    coalesce(master.province, '') as province,
    coalesce(master.district, '') as district,
    coalesce(master.is_active, true) as is_active,
    growth.current_ym as data_created_ym,
    growth.current_employee_count as employee_count,
    growth.previous_employee_count,
    growth.new_employee_count,
    growth.lost_employee_count,
    growth.employee_growth,
    growth.employee_growth_rate,
    'monthly_snapshot'::text as growth_basis,
    snapshot.match_status,
    snapshot.match_method,
    snapshot.match_confidence,
    contact.contact_value as phone,
    contact.source as phone_source,
    contact.source_url as phone_source_url,
    contact.confidence as phone_confidence,
    case
        when growth.industry_name <> '' then array[growth.industry_name]
        else null::text[]
    end as industry_names,
    null::text[] as category_names,
    0::bigint as license_count,
    null::text as latest_license_date
from public.oasis_nps_growth_leads growth
left join public.oasis_nps_employee_snapshots snapshot
  on snapshot.snapshot_identity = growth.snapshot_identity
 and snapshot.data_created_ym = growth.current_ym
left join public.oasis_company_master master
  on master.id = snapshot.company_id
left join lateral (
    select
        company_contact.contact_value,
        company_contact.source,
        company_contact.source_url,
        company_contact.confidence
    from public.oasis_company_contacts company_contact
    where company_contact.company_id = snapshot.company_id
      and company_contact.contact_type = 'phone'
    order by
        company_contact.is_primary desc,
        company_contact.confidence desc,
        company_contact.updated_at desc
    limit 1
) contact on true;

create or replace view public.oasis_fast_employment_growth_leads
with (security_invoker = true)
as
select
    'nps_monthly'::text as source_type,
    growth.business_no,
    growth.company_name,
    growth.address,
    growth.province_code as province,
    growth.district_code as district,
    growth.industry_code,
    growth.industry_name,
    growth.current_employee_count as current_employee_count,
    growth.previous_employee_count,
    growth.employee_growth,
    growth.previous_ym as previous_period,
    growth.current_ym as current_period,
    'monthly'::text as growth_frequency,
    false as is_new_company
from public.oasis_nps_growth_leads growth
where growth.current_employee_count >= 10
union all
select
    'comwel_annual'::text,
    annual.business_no,
    annual.company_name,
    annual.address,
    annual.province,
    annual.district,
    annual.industry_code,
    annual.industry_name,
    annual.workers_2025,
    annual.workers_2024,
    annual.growth_2024_2025,
    '2024',
    '2025',
    'annual',
    annual.is_new_2025
from public.oasis_comwel_annual_growth annual
where annual.workers_2025 between 1 and 9
  and annual.growth_2024_2025 > 0;

comment on view public.oasis_fast_employment_growth_leads is
    '빠른 DB발굴용: 국민연금 10명 이상 월별 증가 + 근로복지공단 1~9명 연간 증가';
