begin;

alter table public.oasis_sales_assignment_settings
    add column if not exists max_landline_db integer not null default 30,
    add column if not exists max_mobile_db integer not null default 30;

alter table public.oasis_sales_assignment_settings
    drop constraint if exists oasis_sales_assignment_settings_landline_limit_check,
    add constraint oasis_sales_assignment_settings_landline_limit_check
        check (max_landline_db between 1 and 1000) not valid,
    drop constraint if exists oasis_sales_assignment_settings_mobile_limit_check,
    add constraint oasis_sales_assignment_settings_mobile_limit_check
        check (max_mobile_db between 1 and 1000) not valid;

alter table public.oasis_sales_assignment_settings
    validate constraint oasis_sales_assignment_settings_landline_limit_check;
alter table public.oasis_sales_assignment_settings
    validate constraint oasis_sales_assignment_settings_mobile_limit_check;

create or replace function public.oasis_get_sales_user_limits(
    p_current_user_id text,
    p_target_user_id text
)
returns table (
    max_uncontacted integer,
    max_landline_db integer,
    max_mobile_db integer
)
language plpgsql
stable
set search_path = public, pg_temp
as $$
begin
    if not public.oasis_sales_actor_is_admin(p_current_user_id) then
        raise exception using
            errcode = '42501',
            message = 'OASIS_SALES_ADMIN_REQUIRED';
    end if;
    if not public.oasis_sales_actor_is_active(p_target_user_id) then
        raise exception using
            errcode = '22023',
            message = 'OASIS_SALES_TARGET_USER_INVALID';
    end if;

    return query
    select
        coalesce(
            (select s.max_uncontacted
             from public.oasis_sales_assignment_settings s
             where lower(s.user_id) = lower(btrim(p_target_user_id))),
            (select s.max_uncontacted
             from public.oasis_sales_assignment_settings s
             where s.user_id = '__default__'),
            60
        ),
        coalesce(
            (select s.max_landline_db
             from public.oasis_sales_assignment_settings s
             where lower(s.user_id) = lower(btrim(p_target_user_id))),
            (select s.max_landline_db
             from public.oasis_sales_assignment_settings s
             where s.user_id = '__default__'),
            30
        ),
        coalesce(
            (select s.max_mobile_db
             from public.oasis_sales_assignment_settings s
             where lower(s.user_id) = lower(btrim(p_target_user_id))),
            (select s.max_mobile_db
             from public.oasis_sales_assignment_settings s
             where s.user_id = '__default__'),
            30
        );
end;
$$;

drop function if exists public.oasis_admin_set_sales_user_limit(
    text, text, integer, text, text
);

create function public.oasis_admin_set_sales_user_limit(
    p_admin_user_id text,
    p_target_user_id text,
    p_max_uncontacted integer,
    p_max_landline_db integer,
    p_max_mobile_db integer,
    p_reason text,
    p_session_id text default null
)
returns boolean
language plpgsql
volatile
set search_path = public, pg_temp
as $$
declare
    v_previous_uncontacted integer;
    v_previous_landline integer;
    v_previous_mobile integer;
begin
    if not public.oasis_sales_actor_is_admin(p_admin_user_id) then
        raise exception using
            errcode = '42501',
            message = 'OASIS_SALES_ADMIN_REQUIRED';
    end if;
    if not public.oasis_sales_actor_is_active(p_target_user_id) then
        raise exception using
            errcode = '22023',
            message = 'OASIS_SALES_TARGET_USER_INVALID';
    end if;
    if p_max_uncontacted not between 1 and 1000
       or p_max_landline_db not between 1 and 1000
       or p_max_mobile_db not between 1 and 1000 then
        raise exception using
            errcode = '22023',
            message = 'OASIS_SALES_LIMIT_OUT_OF_RANGE';
    end if;
    if nullif(btrim(p_reason), '') is null then
        raise exception using
            errcode = '22023',
            message = 'OASIS_SALES_REASON_REQUIRED';
    end if;

    select limits.max_uncontacted, limits.max_landline_db, limits.max_mobile_db
    into v_previous_uncontacted, v_previous_landline, v_previous_mobile
    from public.oasis_get_sales_user_limits(
        p_admin_user_id,
        p_target_user_id
    ) limits;

    insert into public.oasis_sales_assignment_settings (
        user_id,
        max_uncontacted,
        max_landline_db,
        max_mobile_db
    ) values (
        lower(btrim(p_target_user_id)),
        p_max_uncontacted,
        p_max_landline_db,
        p_max_mobile_db
    )
    on conflict (user_id) do update
    set
        max_uncontacted = excluded.max_uncontacted,
        max_landline_db = excluded.max_landline_db,
        max_mobile_db = excluded.max_mobile_db,
        updated_at = now();

    perform public.oasis_write_company_assignment_audit(
        p_admin_user_id,
        null,
        null,
        'user_assignment_limit_changed',
        jsonb_build_object(
            'target_user_id', lower(btrim(p_target_user_id)),
            'max_uncontacted', v_previous_uncontacted,
            'max_landline_db', v_previous_landline,
            'max_mobile_db', v_previous_mobile
        ),
        jsonb_build_object(
            'target_user_id', lower(btrim(p_target_user_id)),
            'max_uncontacted', p_max_uncontacted,
            'max_landline_db', p_max_landline_db,
            'max_mobile_db', p_max_mobile_db,
            'reason', left(btrim(p_reason), 500)
        ),
        p_session_id
    );
    return true;
end;
$$;

create or replace function public.oasis_claim_company_sales_assignment(
    p_current_user_id text,
    p_company_id uuid,
    p_company_uid text,
    p_session_id text default null
)
returns table (
    success boolean,
    code text,
    message text,
    assignment_id uuid,
    company_uid text,
    status text,
    assigned_at timestamptz,
    assignment_expires_at timestamptz
)
language plpgsql
volatile
set search_path = public, pg_temp
as $$
declare
    v_user_id text := lower(btrim(coalesce(p_current_user_id, '')));
    v_uid text;
    v_channel text := lower(btrim(coalesce(
        current_setting('oasis.allocation_channel', true),
        ''
    )));
    v_active_total integer := 0;
    v_active_channel integer := 0;
    v_landline_limit integer := 30;
    v_mobile_limit integer := 30;
    v_total_limit integer := 60;
    v_channel_limit integer := 0;
    v_already_owned boolean := false;
begin
    if not public.oasis_sales_actor_is_active(v_user_id) then
        raise exception using
            errcode = '42501',
            message = 'OASIS_SALES_USER_NOT_AUTHORIZED';
    end if;

    perform public.oasis_release_expired_company_assignments(
        v_user_id,
        p_session_id
    );

    v_uid := public.oasis_resolve_company_sales_uid(
        p_company_id,
        p_company_uid
    );
    if v_uid is null then
        return query select
            false, 'invalid_company_uid',
            '업체 공통 식별키를 생성할 수 없습니다.',
            null::uuid, null::text, null::text,
            null::timestamptz, null::timestamptz;
        return;
    end if;

    perform pg_advisory_xact_lock(
        hashtextextended('oasis-company:' || v_uid, 0)
    );
    perform pg_advisory_xact_lock(
        hashtextextended('oasis-user:' || v_user_id, 0)
    );

    select exists (
        select 1
        from public.oasis_company_sales_assignments a
        where a.company_uid = v_uid
          and a.assigned_user_id = v_user_id
          and a.released_at is null
          and coalesce(a.permanently_excluded, false) is false
          and coalesce(a.status, '') not in (
              'unassigned', 'long_hold', 'permanently_excluded'
          )
          and (
              a.assignment_expires_at is null
              or a.assignment_expires_at > now()
          )
    ) into v_already_owned;

    if not v_already_owned then
        select
            coalesce(
                (select s.max_landline_db
                 from public.oasis_sales_assignment_settings s
                 where lower(s.user_id) = v_user_id),
                (select s.max_landline_db
                 from public.oasis_sales_assignment_settings s
                 where s.user_id = '__default__'),
                30
            ),
            coalesce(
                (select s.max_mobile_db
                 from public.oasis_sales_assignment_settings s
                 where lower(s.user_id) = v_user_id),
                (select s.max_mobile_db
                 from public.oasis_sales_assignment_settings s
                 where s.user_id = '__default__'),
                30
            )
        into v_landline_limit, v_mobile_limit;
        v_total_limit := v_landline_limit + v_mobile_limit;

        select count(*)::integer
        into v_active_total
        from public.oasis_company_sales_assignments a
        where a.assigned_user_id = v_user_id
          and a.released_at is null
          and coalesce(a.permanently_excluded, false) is false
          and coalesce(a.status, '') not in (
              'unassigned', 'long_hold', 'permanently_excluded'
          )
          and (
              a.assignment_expires_at is null
              or a.assignment_expires_at > now()
          );

        if v_active_total >= v_total_limit then
            return query select
                false, 'total_db_limit_reached',
                '일반전화 ' || v_landline_limit::text ||
                '개와 핸드폰번호 DB ' || v_mobile_limit::text ||
                '개를 합쳐 최대 ' || v_total_limit::text ||
                '개까지 보유할 수 있습니다.',
                null::uuid, v_uid, null::text,
                null::timestamptz, null::timestamptz;
            return;
        end if;

        if v_channel in ('landline', 'mobile') then
            v_channel_limit := case
                when v_channel = 'mobile' then v_mobile_limit
                else v_landline_limit
            end;

            select count(*)::integer
            into v_active_channel
            from public.oasis_company_sales_assignments a
            left join lateral (
                select p.source_data
                from public.oasis_prospect_companies p
                where p.company_uid = a.company_uid
                order by
                    (p.id = a.company_id) desc,
                    p.updated_at desc nulls last,
                    p.id
                limit 1
            ) company on true
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
              and lower(btrim(coalesce(
                  company.source_data ->> 'allocation_channel',
                  ''
              ))) = v_channel;

            if v_active_channel >= v_channel_limit then
                return query select
                    false,
                    case
                        when v_channel = 'mobile'
                            then 'mobile_limit_reached'
                        else 'landline_limit_reached'
                    end,
                    case
                        when v_channel = 'mobile'
                            then '핸드폰번호 DB는 최대 ' ||
                                v_mobile_limit::text || '개까지 보유할 수 있습니다.'
                        else '일반전화 DB는 최대 ' ||
                            v_landline_limit::text || '개까지 보유할 수 있습니다.'
                    end,
                    null::uuid, v_uid, null::text,
                    null::timestamptz, null::timestamptz;
                return;
            end if;
        end if;
    end if;

    return query
    select *
    from public.oasis_claim_company_sales_assignment_base_v1130(
        v_user_id,
        p_company_id,
        v_uid,
        p_session_id
    );
end;
$$;

revoke all on function public.oasis_get_sales_user_limits(
    text, text
) from public, anon, authenticated;
revoke all on function public.oasis_admin_set_sales_user_limit(
    text, text, integer, integer, integer, text, text
) from public, anon, authenticated;
revoke all on function public.oasis_claim_company_sales_assignment(
    text, uuid, text, text
) from public, anon, authenticated;

grant execute on function public.oasis_get_sales_user_limits(
    text, text
) to service_role;
grant execute on function public.oasis_admin_set_sales_user_limit(
    text, text, integer, integer, integer, text, text
) to service_role;
grant execute on function public.oasis_claim_company_sales_assignment(
    text, uuid, text, text
) to service_role;

commit;
