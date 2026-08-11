begin;

create schema if not exists oasis_private;
revoke all on schema oasis_private from public, anon, authenticated;
grant usage on schema oasis_private to service_role;

create materialized view oasis_private.oasis_assignable_db_inventory as
with source_inventory as (
    select
        case
            when c.contact_key ~ '^business:[0-9]{10}$'
                then c.contact_key
            else public.oasis_make_company_uid(
                c.business_no,
                '',
                '',
                c.company_name,
                c.address,
                coalesce(
                    nullif(btrim(c.mobile_phone), ''),
                    nullif(btrim(c.landline_phone), ''),
                    ''
                ),
                c.source_type,
                concat_ws(
                    ':',
                    nullif(btrim(c.source_type), ''),
                    nullif(btrim(c.source_record_key), '')
                )
            )
        end as company_uid,
        coalesce(c.has_landline_phone, false) as has_landline,
        coalesce(c.has_mobile_phone, false) as has_mobile,
        coalesce(
            public.oasis_is_stock_company(c.company_name),
            false
        ) as is_corporate
    from public.oasis_employment_contacts c
    where coalesce(c.has_landline_phone, false)
       or coalesce(c.has_mobile_phone, false)
)
select
    company_uid,
    bool_or(has_landline) as has_landline,
    bool_or(has_mobile) as has_mobile,
    bool_or(is_corporate) as is_corporate
from source_inventory
where nullif(btrim(company_uid), '') is not null
group by company_uid;

create unique index oasis_assignable_db_inventory_company_uid_uidx
    on oasis_private.oasis_assignable_db_inventory (company_uid);

revoke all on oasis_private.oasis_assignable_db_inventory
from public, anon, authenticated;
grant select on oasis_private.oasis_assignable_db_inventory
to service_role;

comment on materialized view oasis_private.oasis_assignable_db_inventory is
    '전 조직 공통 DB 현황용 전화번호 보유 업체 캐시. 원문 개인정보는 공개하지 않는다.';

create function public.oasis_get_assignable_db_inventory_dashboard(
    p_current_user_id text
)
returns table (
    total_db_count bigint,
    landline_db_count bigint,
    mobile_db_count bigint,
    total_individual_count bigint,
    total_corporate_count bigint,
    landline_individual_count bigint,
    landline_corporate_count bigint,
    mobile_individual_count bigint,
    mobile_corporate_count bigint
)
language plpgsql
stable
security invoker
set search_path = public, oasis_private, pg_temp
as $$
declare
    v_user_id text := lower(btrim(coalesce(p_current_user_id, '')));
begin
    if not public.oasis_sales_actor_is_active(v_user_id) then
        raise exception using
            errcode = '42501',
            message = 'OASIS_SALES_USER_NOT_AUTHORIZED';
    end if;

    return query
    with available as (
        select
            inventory.has_landline,
            inventory.has_mobile,
            inventory.is_corporate
        from oasis_private.oasis_assignable_db_inventory inventory
        left join public.oasis_company_sales_assignments assignment
          on assignment.company_uid = inventory.company_uid
        where assignment.company_uid is null
           or (
                coalesce(assignment.status, '') = 'unassigned'
                and assignment.assigned_user_id is null
                and coalesce(assignment.permanently_excluded, false) is false
                and coalesce(assignment.migration_conflict, false) is false
           )
    )
    select
        count(*)::bigint,
        count(*) filter (where has_landline)::bigint,
        count(*) filter (where has_mobile)::bigint,
        count(*) filter (where not is_corporate)::bigint,
        count(*) filter (where is_corporate)::bigint,
        count(*) filter (
            where has_landline and not is_corporate
        )::bigint,
        count(*) filter (
            where has_landline and is_corporate
        )::bigint,
        count(*) filter (
            where has_mobile and not is_corporate
        )::bigint,
        count(*) filter (
            where has_mobile and is_corporate
        )::bigint
    from available;
end;
$$;

revoke all on function public.oasis_get_assignable_db_inventory_dashboard(text)
from public, anon, authenticated;
grant execute on function public.oasis_get_assignable_db_inventory_dashboard(text)
to service_role;

comment on function public.oasis_get_assignable_db_inventory_dashboard(text) is
    '활성 사용자에게 전 조직 공통 배정 가능 DB 재고의 집계 숫자만 반환한다.';

select cron.schedule(
    'oasis-assignable-db-inventory-refresh-v1194',
    '17 * * * *',
    'refresh materialized view concurrently oasis_private.oasis_assignable_db_inventory;'
);

commit;
