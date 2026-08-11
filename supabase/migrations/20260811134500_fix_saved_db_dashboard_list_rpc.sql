begin;

create or replace function public.oasis_list_user_db_assignments(
    p_current_user_id text,
    p_filter text default 'all',
    p_limit integer default 100,
    p_offset integer default 0
)
returns table (
    assignment_id uuid,
    company_id uuid,
    company_uid text,
    source text,
    source_key text,
    business_no text,
    company_name text,
    address text,
    region text,
    industry_code text,
    industry_name text,
    employee_count integer,
    new_employee_count integer,
    lost_employee_count integer,
    monthly_notice_amount bigint,
    data_created_ym text,
    priority_score integer,
    priority_reasons jsonb,
    source_data jsonb,
    status text,
    assigned_at timestamptz,
    assignment_expires_at timestamptz,
    first_contacted_at timestamptz,
    last_contacted_at timestamptz,
    next_contact_at timestamptz,
    contact_count integer,
    own_memo text,
    legacy_hold boolean,
    updated_at timestamptz,
    total_count bigint
)
language plpgsql
stable
set search_path = public, pg_temp
as $$
declare
    v_user_id text := lower(btrim(coalesce(p_current_user_id, '')));
    v_filter text := lower(btrim(coalesce(p_filter, 'all')));
begin
    if not public.oasis_sales_actor_is_active(v_user_id) then
        raise exception using
            errcode = '42501',
            message = 'OASIS_SALES_USER_NOT_AUTHORIZED';
    end if;
    if v_filter not in (
        'all', 'landline', 'mobile', 'new', 'in_progress', 'completed'
    ) then
        raise exception using
            errcode = '22023',
            message = 'OASIS_INVALID_DB_FILTER';
    end if;

    return query
    with owned as (
        select
            a.id as assignment_id,
            company.id as company_id,
            a.company_uid,
            company.source,
            company.source_key,
            company.business_no,
            company.company_name,
            company.address,
            company.region,
            company.industry_code,
            company.industry_name,
            company.employee_count,
            company.new_employee_count,
            company.lost_employee_count,
            company.monthly_notice_amount,
            company.data_created_ym,
            company.priority_score,
            company.priority_reasons,
            company.source_data,
            a.status,
            a.assigned_at,
            a.assignment_expires_at,
            a.first_contacted_at,
            a.last_contacted_at,
            a.next_contact_at,
            a.contact_count,
            coalesce(note.memo, '') as own_memo,
            a.legacy_hold,
            a.updated_at,
            a.current_assignment_contact_count,
            latest.contact_result as latest_contact_result,
            latest.next_contact_at as latest_next_contact_at,
            coalesce(phones.has_landline, false) as has_landline,
            coalesce(phones.has_mobile, false) as has_mobile
        from public.oasis_company_sales_assignments a
        left join lateral (
            select p.*
            from public.oasis_prospect_companies p
            where p.company_uid = a.company_uid
            order by
                (p.id = a.company_id) desc,
                p.updated_at desc nulls last,
                p.id
            limit 1
        ) company on true
        left join public.oasis_user_prospect_notes note
          on note.company_uid = a.company_uid
         and note.user_id = v_user_id
        left join lateral (
            select
                bool_or(
                    normalized.phone_digits ~ '^010[0-9]{8}$'
                ) as has_mobile,
                bool_or(
                    normalized.phone_digits ~ '^0[0-9]{8,10}$'
                    and normalized.phone_digits !~ '^010[0-9]{8}$'
                ) as has_landline
            from (
                select case
                    when digits.phone_digits like '0082%'
                        then '0' || substr(digits.phone_digits, 5)
                    when digits.phone_digits like '82%'
                        then '0' || substr(digits.phone_digits, 3)
                    else digits.phone_digits
                end as phone_digits
                from (
                    select regexp_replace(
                        coalesce(c.contact_value, ''),
                        '[^0-9]',
                        '',
                        'g'
                    ) as phone_digits
                    from public.oasis_prospect_contacts c
                    where c.prospect_id = company.id
                      and c.contact_type = 'phone'
                      and lower(coalesce(c.verification_status, '')) <> 'rejected'
                      and coalesce(c.do_not_contact, false) is false
                      and c.opt_out_at is null
                ) digits
            ) normalized
        ) phones on true
        left join lateral (
            select
                l.contact_result,
                l.next_contact_at
            from public.oasis_company_sales_contact_logs l
            where l.assignment_id = a.id
            order by
                l.contacted_at desc,
                l.created_at desc,
                l.id desc
            limit 1
        ) latest on true
        where a.assigned_user_id = v_user_id
          and a.released_at is null
          and coalesce(a.permanently_excluded, false) is false
          and coalesce(a.status, '') not in (
              'unassigned', 'long_hold', 'permanently_excluded'
          )
          and (
              a.assignment_expires_at is null
              or a.assignment_expires_at > now()
          )
    ), classified as (
        select
            owned.*,
            case
                when owned.latest_contact_result in (
                    'existing_customer',
                    'contracted',
                    'not_interested',
                    'bad_number',
                    'unreachable'
                ) then 'completed'
                when owned.latest_contact_result = 'connected'
                     and coalesce(
                         owned.latest_next_contact_at,
                         owned.next_contact_at
                     ) is null
                    then 'completed'
                when owned.latest_contact_result is null
                     and owned.status in (
                         'contacted',
                         'rejected',
                         'contracted',
                         'unreachable',
                         'wrong_number',
                         'closed'
                     )
                    then 'completed'
                when coalesce(
                         owned.current_assignment_contact_count,
                         0
                     ) = 0
                     and owned.latest_contact_result is null
                    then 'new'
                else 'in_progress'
            end as db_stage
        from owned
    ), filtered as (
        select classified.*
        from classified
        where v_filter = 'all'
           or (v_filter = 'landline' and has_landline)
           or (v_filter = 'mobile' and has_mobile)
           or (v_filter = 'new' and db_stage = 'new')
           or (v_filter = 'in_progress' and db_stage = 'in_progress')
           or (v_filter = 'completed' and db_stage = 'completed')
    )
    select
        filtered.assignment_id,
        filtered.company_id,
        filtered.company_uid,
        filtered.source,
        filtered.source_key,
        filtered.business_no,
        filtered.company_name,
        filtered.address,
        filtered.region,
        filtered.industry_code,
        filtered.industry_name,
        filtered.employee_count,
        filtered.new_employee_count,
        filtered.lost_employee_count,
        filtered.monthly_notice_amount,
        filtered.data_created_ym,
        filtered.priority_score,
        filtered.priority_reasons,
        filtered.source_data,
        filtered.status,
        filtered.assigned_at,
        filtered.assignment_expires_at,
        filtered.first_contacted_at,
        filtered.last_contacted_at,
        filtered.next_contact_at,
        filtered.contact_count,
        filtered.own_memo,
        filtered.legacy_hold,
        filtered.updated_at,
        count(*) over ()::bigint
    from filtered
    order by
        coalesce(filtered.next_contact_at, filtered.updated_at) desc nulls last,
        filtered.assignment_id
    limit greatest(1, least(coalesce(p_limit, 100), 1000))
    offset greatest(0, coalesce(p_offset, 0));
end;
$$;

revoke execute on function public.oasis_list_user_db_assignments(
    text, text, integer, integer
) from public, anon, authenticated;
grant execute on function public.oasis_list_user_db_assignments(
    text, text, integer, integer
) to service_role;

commit;
