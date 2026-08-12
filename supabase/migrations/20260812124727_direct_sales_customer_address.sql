-- Add canonical customer address support to employee-owned direct sales DB.
-- The address remains in oasis_customers so every customer-facing feature can
-- reuse the same value without creating a second source of truth.

create or replace function public.oasis_register_direct_sales_customer(
    p_current_user_id text,
    p_company_name text,
    p_business_no text,
    p_business_type text,
    p_representative_name text default '',
    p_landline_phone text default '',
    p_mobile_phone text default '',
    p_mobile_phone_hash text default '',
    p_industry_name text default '',
    p_address text default '',
    p_employee_count integer default 0,
    p_acquisition_source text default '',
    p_registration_memo text default '',
    p_marketing_consent_confirmed boolean default false,
    p_marketing_consent_method text default '',
    p_manager_name text default ''
)
returns table (
    success boolean,
    code text,
    message text,
    direct_customer_id uuid,
    customer_id uuid
)
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
    v_actor text := lower(btrim(coalesce(p_current_user_id, '')));
    v_address text := left(btrim(coalesce(p_address, '')), 300);
    v_result record;
begin
    if not public.oasis_sales_actor_is_active(v_actor) then
        raise exception using errcode = '42501', message = 'PERMISSION_DENIED';
    end if;

    select * into v_result
    from public.oasis_register_direct_sales_customer(
        v_actor,
        p_company_name,
        p_business_no,
        p_business_type,
        p_representative_name,
        p_landline_phone,
        p_mobile_phone,
        p_mobile_phone_hash,
        p_industry_name,
        p_employee_count,
        p_acquisition_source,
        p_registration_memo,
        p_marketing_consent_confirmed,
        p_marketing_consent_method,
        p_manager_name
    );

    if coalesce(v_result.success, false)
       and v_result.customer_id is not null
       and v_address <> '' then
        update public.oasis_customers c
        set
            address = v_address,
            customer_data = public.oasis_v911_lossless_jsonb_merge(
                c.customer_data,
                pg_catalog.jsonb_build_object('주소', v_address)
            ),
            updated_at = now()
        where c.id = v_result.customer_id
          and c.owner_user_id = v_actor;
    end if;

    return query select
        coalesce(v_result.success, false),
        v_result.code::text,
        v_result.message::text,
        v_result.direct_customer_id::uuid,
        v_result.customer_id::uuid;
end;
$$;


create or replace function public.oasis_list_direct_sales_customers_v2(
    p_current_user_id text,
    p_filter text default 'all',
    p_direct_customer_id uuid default null,
    p_limit integer default 500,
    p_offset integer default 0
)
returns table (
    direct_customer_id uuid,
    customer_id uuid,
    company_uid text,
    company_name text,
    business_no text,
    representative_name text,
    business_type text,
    discovery_type text,
    landline_phone text,
    mobile_phone text,
    industry_name text,
    address text,
    employee_count integer,
    acquisition_source text,
    registration_memo text,
    marketing_consent_confirmed boolean,
    marketing_consent_at timestamptz,
    marketing_consent_method text,
    crm_status text,
    sales_category text,
    created_at timestamptz,
    updated_at timestamptz,
    total_count bigint
)
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    v_actor text := lower(btrim(coalesce(p_current_user_id, '')));
    v_filter text := lower(btrim(coalesce(p_filter, 'all')));
begin
    if not public.oasis_sales_actor_is_active(v_actor) then
        raise exception using errcode = '42501', message = 'PERMISSION_DENIED';
    end if;
    if v_filter not in ('all', 'registered', 'contracted') then
        v_filter := 'all';
    end if;

    return query
    select
        d.id,
        d.customer_id,
        coalesce(link.company_uid, 'customer:' || d.customer_id::text),
        d.company_name,
        d.business_no,
        d.representative_name,
        d.business_type,
        d.discovery_type,
        d.landline_phone,
        d.mobile_phone,
        d.industry_name,
        coalesce(customer.address, ''),
        d.employee_count,
        d.acquisition_source,
        d.registration_memo,
        d.marketing_consent_confirmed,
        d.marketing_consent_at,
        d.marketing_consent_method,
        coalesce(crm.crm_data ->> 'status', '신규'),
        case when coalesce(crm.crm_data ->> 'status', '') = '계약완료'
            then 'contracted' else 'registered' end,
        d.created_at,
        d.updated_at,
        count(*) over()
    from public.oasis_direct_sales_customers d
    join public.oasis_customers customer
      on customer.id = d.customer_id
     and customer.owner_user_id = d.owner_user_id
    left join public.oasis_customer_company_links link
      on link.owner_user_id = d.owner_user_id
     and link.customer_id = d.customer_id
    left join public.oasis_crm crm
      on crm.owner_user_id = d.owner_user_id
     and crm.business_no = d.business_no
    where d.owner_user_id = v_actor
      and d.is_active
      and (p_direct_customer_id is null or d.id = p_direct_customer_id)
      and (
          v_filter = 'all'
          or (
              v_filter = 'contracted'
              and coalesce(crm.crm_data ->> 'status', '') = '계약완료'
          )
          or (
              v_filter = 'registered'
              and coalesce(crm.crm_data ->> 'status', '') <> '계약완료'
          )
      )
    order by d.updated_at desc, d.id
    limit greatest(1, least(coalesce(p_limit, 500), 5000))
    offset greatest(0, coalesce(p_offset, 0));
end;
$$;


revoke all on function public.oasis_register_direct_sales_customer(
    text, text, text, text, text, text, text, text,
    text, text, integer, text, text, boolean, text, text
) from PUBLIC, anon, authenticated, service_role;
grant execute on function public.oasis_register_direct_sales_customer(
    text, text, text, text, text, text, text, text,
    text, text, integer, text, text, boolean, text, text
) to service_role;

revoke all on function public.oasis_list_direct_sales_customers_v2(
    text, text, uuid, integer, integer
) from PUBLIC, anon, authenticated, service_role;
grant execute on function public.oasis_list_direct_sales_customers_v2(
    text, text, uuid, integer, integer
) to service_role;

comment on function public.oasis_list_direct_sales_customers_v2(
    text, text, uuid, integer, integer
) is 'Owner-scoped direct sales DB list with canonical customer address.';
