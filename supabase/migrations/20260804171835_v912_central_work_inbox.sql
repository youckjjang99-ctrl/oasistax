-- OASIS CRM v9.12.0 - central work inbox
--
-- This migration makes the existing PII-free oasis_work_tasks projection
-- operable from one assignee-scoped inbox. It does not recreate or mutate any
-- source outbox, collection queue, provider lease, customer identifier, or
-- company_uid. Existing guidance and claim task identities remain unchanged.

begin;

select pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('oasis-v912-central-work-inbox', 0)
);

-- Fail closed before adding the inbox contract when the deployed v9.10 task
-- master or the custom OASIS account authorization helper is not compatible.
do $v912_prerequisite_guard$
declare
    v_column text;
    v_type regtype;
begin
    if pg_catalog.to_regclass('public.oasis_work_tasks') is null then
        raise exception using
            errcode = '42P01',
            message = 'OASIS_V912_REQUIRES_PUBLIC_OASIS_WORK_TASKS';
    end if;

    if pg_catalog.to_regprocedure(
        'public.oasis_sales_actor_is_active(text)'
    ) is null then
        raise exception using
            errcode = '42883',
            message = 'OASIS_V912_REQUIRES_ACTIVE_USER_GUARD';
    end if;

    for v_column, v_type in
        select required.column_name, required.column_type
        from (values
            ('id', 'uuid'::regtype),
            ('assigned_user_id', 'text'::regtype),
            ('task_type', 'text'::regtype),
            ('title', 'text'::regtype),
            ('priority', 'text'::regtype),
            ('status', 'text'::regtype),
            ('due_at', 'timestamp with time zone'::regtype),
            ('completed_at', 'timestamp with time zone'::regtype),
            ('source_type', 'text'::regtype),
            ('source_id', 'uuid'::regtype),
            ('idempotency_key', 'text'::regtype),
            ('updated_at', 'timestamp with time zone'::regtype)
        ) as required(column_name, column_type)
    loop
        if not exists (
            select 1
            from pg_catalog.pg_attribute a
            where a.attrelid = 'public.oasis_work_tasks'::regclass
              and a.attname = v_column
              and a.atttypid = v_type
              and a.attnum > 0
              and not a.attisdropped
        ) then
            raise exception using
                errcode = '42804',
                message = 'OASIS_V912_WORK_TASK_SCHEMA_INCOMPATIBLE';
        end if;
    end loop;

    if exists (
        select 1
        from public.oasis_work_tasks t
        where t.task_type not in ('guidance_followup', 'claim_tax_review')
           or t.status not in (
                'scheduled', 'pending', 'in_progress',
                'completed', 'cancelled'
           )
           or t.priority not in ('normal', 'high')
    ) then
        raise exception using
            errcode = '23514',
            message = 'OASIS_V912_WORK_TASK_VALUES_INCOMPATIBLE';
    end if;
end;
$v912_prerequisite_guard$;

do $v912_sales_prerequisite_guard$
declare
    v_table text;
    v_column text;
    v_type regtype;
begin
    if pg_catalog.to_regclass(
        'public.oasis_company_sales_assignments'
    ) is null or pg_catalog.to_regclass(
        'public.oasis_prospect_companies'
    ) is null then
        raise exception using
            errcode = '42P01',
            message = 'OASIS_V912_REQUIRES_SALES_ASSIGNMENT_TABLES';
    end if;

    for v_table, v_column, v_type in
        select required.table_name, required.column_name, required.column_type
        from (values
            ('public.oasis_company_sales_assignments', 'id', 'uuid'::regtype),
            ('public.oasis_company_sales_assignments', 'company_id', 'uuid'::regtype),
            ('public.oasis_company_sales_assignments', 'company_uid', 'text'::regtype),
            ('public.oasis_company_sales_assignments', 'assigned_user_id', 'text'::regtype),
            ('public.oasis_company_sales_assignments', 'status', 'text'::regtype),
            ('public.oasis_company_sales_assignments', 'next_contact_at', 'timestamp with time zone'::regtype),
            ('public.oasis_prospect_companies', 'id', 'uuid'::regtype),
            ('public.oasis_prospect_companies', 'company_uid', 'text'::regtype),
            ('public.oasis_prospect_companies', 'company_name', 'text'::regtype),
            ('public.oasis_prospect_companies', 'updated_at', 'timestamp with time zone'::regtype)
        ) as required(table_name, column_name, column_type)
    loop
        if not exists (
            select 1
            from pg_catalog.pg_attribute a
            where a.attrelid = pg_catalog.to_regclass(v_table)
              and a.attname = v_column
              and a.atttypid = v_type
              and a.attnum > 0
              and not a.attisdropped
        ) then
            raise exception using
                errcode = '42804',
                message = 'OASIS_V912_SALES_ASSIGNMENT_SCHEMA_INCOMPATIBLE';
        end if;
    end loop;
end;
$v912_sales_prerequisite_guard$;

alter table public.oasis_work_tasks
    add column if not exists task_version bigint not null default 1;

do $v912_task_version_guard$
begin
    if not exists (
        select 1
        from pg_catalog.pg_attribute a
        where a.attrelid = 'public.oasis_work_tasks'::regclass
          and a.attname = 'task_version'
          and a.atttypid = 'bigint'::regtype
          and a.attnotnull
          and a.atthasdef
          and a.attnum > 0
          and not a.attisdropped
          and exists (
              select 1
              from pg_catalog.pg_attrdef d
              where d.adrelid = a.attrelid
                and d.adnum = a.attnum
                and pg_catalog.regexp_replace(
                    pg_catalog.lower(
                        pg_catalog.pg_get_expr(d.adbin, d.adrelid)
                    ),
                    '[[:space:]()]',
                    '',
                    'g'
                ) = '1'
          )
    ) then
        raise exception using
            errcode = '42804',
            message = 'OASIS_V912_TASK_VERSION_SCHEMA_INCOMPATIBLE';
    end if;

    if not exists (
        select 1
        from pg_catalog.pg_constraint c
        where c.conrelid = 'public.oasis_work_tasks'::regclass
          and c.conname = 'oasis_work_tasks_version_check'
    ) then
        alter table public.oasis_work_tasks
            add constraint oasis_work_tasks_version_check
            check (task_version > 0);
    end if;

    if not exists (
        select 1
        from pg_catalog.pg_constraint c
        where c.conrelid = 'public.oasis_work_tasks'::regclass
          and c.conname = 'oasis_work_tasks_version_check'
          and c.contype = 'c'
          and c.convalidated
          and pg_catalog.regexp_replace(
              pg_catalog.lower(
                  pg_catalog.pg_get_expr(c.conbin, c.conrelid)
              ),
              '[[:space:]()]',
              '',
              'g'
          ) = 'task_version>0'
    ) then
        raise exception using
            errcode = '42804',
            message = 'OASIS_V912_TASK_VERSION_CHECK_INCOMPATIBLE';
    end if;
end;
$v912_task_version_guard$;

create table if not exists public.oasis_work_task_events (
    id uuid primary key default extensions.gen_random_uuid(),
    task_id uuid not null,
    actor_user_id text not null,
    event_type text not null,
    from_status text not null,
    to_status text not null,
    from_due_at timestamptz not null,
    to_due_at timestamptz not null,
    task_version bigint not null,
    created_at timestamptz not null default now(),
    constraint oasis_work_task_events_task_fkey
        foreign key (task_id)
        references public.oasis_work_tasks(id)
        on delete restrict,
    constraint oasis_work_task_events_actor_check
        check (
            actor_user_id = pg_catalog.lower(pg_catalog.btrim(actor_user_id))
            and pg_catalog.length(actor_user_id) between 1 and 200
        ),
    constraint oasis_work_task_events_type_check
        check (event_type in ('started', 'completed', 'deferred')),
    constraint oasis_work_task_events_from_status_check
        check (from_status in (
            'scheduled', 'pending', 'in_progress', 'completed', 'cancelled'
        )),
    constraint oasis_work_task_events_to_status_check
        check (to_status in (
            'scheduled', 'pending', 'in_progress', 'completed', 'cancelled'
        )),
    constraint oasis_work_task_events_version_check
        check (task_version > 0)
);

do $v912_event_table_guard$
declare
    v_column text;
    v_type regtype;
    v_constraint text;
    v_definition text;
begin
    for v_column, v_type in
        select required.column_name, required.column_type
        from (values
            ('id', 'uuid'::regtype),
            ('task_id', 'uuid'::regtype),
            ('actor_user_id', 'text'::regtype),
            ('event_type', 'text'::regtype),
            ('from_status', 'text'::regtype),
            ('to_status', 'text'::regtype),
            ('from_due_at', 'timestamp with time zone'::regtype),
            ('to_due_at', 'timestamp with time zone'::regtype),
            ('task_version', 'bigint'::regtype),
            ('created_at', 'timestamp with time zone'::regtype)
        ) as required(column_name, column_type)
    loop
        if not exists (
            select 1
            from pg_catalog.pg_attribute a
            where a.attrelid = 'public.oasis_work_task_events'::regclass
              and a.attname = v_column
              and a.atttypid = v_type
              and a.attnotnull
              and a.attnum > 0
              and not a.attisdropped
        ) then
            raise exception using
                errcode = '42804',
                message = 'OASIS_V912_WORK_TASK_EVENT_SCHEMA_INCOMPATIBLE';
        end if;
    end loop;

    if exists (
        select 1
        from pg_catalog.pg_attribute a
        where a.attrelid = 'public.oasis_work_task_events'::regclass
          and a.attnum > 0
          and not a.attisdropped
          and a.attname not in (
              'id', 'task_id', 'actor_user_id', 'event_type',
              'from_status', 'to_status', 'from_due_at', 'to_due_at',
              'task_version', 'created_at'
          )
          and a.attnotnull
          and not a.atthasdef
    ) then
        raise exception using
            errcode = '42804',
            message = 'OASIS_V912_WORK_TASK_EVENT_EXTRA_COLUMN_INCOMPATIBLE';
    end if;

    if not exists (
        select 1
        from pg_catalog.pg_constraint c
        join pg_catalog.pg_attribute a
          on a.attrelid = c.conrelid
         and a.attname = 'id'
         and a.attnum > 0
         and not a.attisdropped
        where c.conrelid = 'public.oasis_work_task_events'::regclass
          and c.contype = 'p'
          and c.convalidated
          and c.conkey = array[a.attnum]
    ) then
        raise exception using
            errcode = '42830',
            message = 'OASIS_V912_WORK_TASK_EVENT_PK_INCOMPATIBLE';
    end if;

    if not exists (
        select 1
        from pg_catalog.pg_constraint c
        join pg_catalog.pg_attribute source_column
          on source_column.attrelid = c.conrelid
         and source_column.attname = 'task_id'
         and source_column.attnum > 0
         and not source_column.attisdropped
        join pg_catalog.pg_attribute target_column
          on target_column.attrelid = c.confrelid
         and target_column.attname = 'id'
         and target_column.attnum > 0
         and not target_column.attisdropped
        where c.conrelid = 'public.oasis_work_task_events'::regclass
          and c.conname = 'oasis_work_task_events_task_fkey'
          and c.contype = 'f'
          and c.convalidated
          and c.confrelid = 'public.oasis_work_tasks'::regclass
          and c.conkey = array[source_column.attnum]
          and c.confkey = array[target_column.attnum]
          and c.confdeltype = 'r'
    ) then
        raise exception using
            errcode = '42830',
            message = 'OASIS_V912_WORK_TASK_EVENT_FK_INCOMPATIBLE';
    end if;

    if not exists (
        select 1
        from pg_catalog.pg_attribute a
        join pg_catalog.pg_attrdef d
          on d.adrelid = a.attrelid
         and d.adnum = a.attnum
        where a.attrelid = 'public.oasis_work_task_events'::regclass
          and a.attname = 'id'
          and pg_catalog.regexp_replace(
              pg_catalog.lower(
                  pg_catalog.pg_get_expr(d.adbin, d.adrelid)
              ),
              '[[:space:]]',
              '',
              'g'
          ) in ('extensions.gen_random_uuid()', 'gen_random_uuid()')
    ) or not exists (
        select 1
        from pg_catalog.pg_attribute a
        join pg_catalog.pg_attrdef d
          on d.adrelid = a.attrelid
         and d.adnum = a.attnum
        where a.attrelid = 'public.oasis_work_task_events'::regclass
          and a.attname = 'created_at'
          and pg_catalog.regexp_replace(
              pg_catalog.lower(
                  pg_catalog.pg_get_expr(d.adbin, d.adrelid)
              ),
              '[[:space:]]',
              '',
              'g'
          ) in ('now()', 'current_timestamp')
    ) then
        raise exception using
            errcode = '42804',
            message = 'OASIS_V912_WORK_TASK_EVENT_DEFAULT_INCOMPATIBLE';
    end if;

    for v_constraint in
        select pg_catalog.unnest(array[
            'oasis_work_task_events_actor_check',
            'oasis_work_task_events_type_check',
            'oasis_work_task_events_from_status_check',
            'oasis_work_task_events_to_status_check',
            'oasis_work_task_events_version_check'
        ]::text[])
    loop
        if not exists (
            select 1
            from pg_catalog.pg_constraint c
            where c.conrelid = 'public.oasis_work_task_events'::regclass
              and c.conname = v_constraint
              and c.contype = 'c'
              and c.convalidated
        ) then
            raise exception using
                errcode = '42804',
                message = 'OASIS_V912_WORK_TASK_EVENT_CHECK_INCOMPATIBLE';
        end if;

        select pg_catalog.regexp_replace(
            pg_catalog.lower(
                pg_catalog.pg_get_expr(c.conbin, c.conrelid)
            ),
            '[[:space:]()]',
            '',
            'g'
        )
        into v_definition
        from pg_catalog.pg_constraint c
        where c.conrelid = 'public.oasis_work_task_events'::regclass
          and c.conname = v_constraint;

        if (
            v_constraint = 'oasis_work_task_events_actor_check'
            and not (
                pg_catalog.strpos(v_definition, 'actor_user_id') > 0
                and pg_catalog.strpos(v_definition, 'lower') > 0
                and pg_catalog.strpos(v_definition, 'btrim') > 0
                and pg_catalog.strpos(v_definition, 'length') > 0
                and pg_catalog.strpos(v_definition, '1') > 0
                and pg_catalog.strpos(v_definition, '200') > 0
            )
        ) or (
            v_constraint = 'oasis_work_task_events_type_check'
            and not (
                pg_catalog.strpos(v_definition, 'event_type') > 0
                and pg_catalog.strpos(v_definition, 'started') > 0
                and pg_catalog.strpos(v_definition, 'completed') > 0
                and pg_catalog.strpos(v_definition, 'deferred') > 0
            )
        ) or (
            v_constraint = 'oasis_work_task_events_from_status_check'
            and not (
                pg_catalog.strpos(v_definition, 'from_status') > 0
                and pg_catalog.strpos(v_definition, 'scheduled') > 0
                and pg_catalog.strpos(v_definition, 'pending') > 0
                and pg_catalog.strpos(v_definition, 'in_progress') > 0
                and pg_catalog.strpos(v_definition, 'completed') > 0
                and pg_catalog.strpos(v_definition, 'cancelled') > 0
            )
        ) or (
            v_constraint = 'oasis_work_task_events_to_status_check'
            and not (
                pg_catalog.strpos(v_definition, 'to_status') > 0
                and pg_catalog.strpos(v_definition, 'scheduled') > 0
                and pg_catalog.strpos(v_definition, 'pending') > 0
                and pg_catalog.strpos(v_definition, 'in_progress') > 0
                and pg_catalog.strpos(v_definition, 'completed') > 0
                and pg_catalog.strpos(v_definition, 'cancelled') > 0
            )
        ) or (
            v_constraint = 'oasis_work_task_events_version_check'
            and v_definition <> 'task_version>0'
        ) then
            raise exception using
                errcode = '42804',
                message = 'OASIS_V912_WORK_TASK_EVENT_CHECK_INCOMPATIBLE';
        end if;
    end loop;
end;
$v912_event_table_guard$;

create index if not exists oasis_work_task_events_task_created_idx
    on public.oasis_work_task_events (task_id, created_at desc);
create index if not exists oasis_work_task_events_actor_created_idx
    on public.oasis_work_task_events (actor_user_id, created_at desc);
create index if not exists oasis_work_tasks_assignee_completed_idx
    on public.oasis_work_tasks (
        assigned_user_id, completed_at desc, updated_at desc
    )
    where status = 'completed';
create index if not exists oasis_sales_followup_inbox_idx
    on public.oasis_company_sales_assignments (
        assigned_user_id, next_contact_at, id
    )
    where status = 'follow_up' and next_contact_at is not null;

-- Every task update receives a monotonic optimistic-concurrency version. The
-- existing trigger already points at this function, so replacing the body is
-- additive and keeps guidance/claim cancellation behavior intact.
create or replace function public.oasis_work_task_touch_updated_at()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
    new.updated_at := pg_catalog.clock_timestamp();
    new.task_version := old.task_version + 1;
    return new;
end;
$$;

drop trigger if exists oasis_work_tasks_updated_at
    on public.oasis_work_tasks;
create trigger oasis_work_tasks_updated_at
before update on public.oasis_work_tasks
for each row execute function public.oasis_work_task_touch_updated_at();

create or replace function public.oasis_work_task_event_is_immutable()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
    raise exception using
        errcode = '55000',
        message = 'OASIS_WORK_TASK_EVENTS_ARE_APPEND_ONLY';
end;
$$;

drop trigger if exists oasis_work_task_events_immutable
    on public.oasis_work_task_events;
create trigger oasis_work_task_events_immutable
before update or delete on public.oasis_work_task_events
for each row execute function public.oasis_work_task_event_is_immutable();

-- Read-only sales follow-ups for the central inbox. The existing sales list
-- RPC performs assignment-expiry writes, so it must not be used by this view.
create or replace function public.oasis_list_my_sales_followups(
    p_current_user_id text,
    p_limit integer default 1000,
    p_after_next_contact_at timestamptz default null,
    p_after_assignment_id uuid default null
)
returns table (
    assignment_id uuid,
    company_name text,
    next_contact_at timestamptz
)
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    v_actor text := pg_catalog.lower(
        pg_catalog.btrim(coalesce(p_current_user_id, ''))
    );
    v_limit integer := greatest(
        1,
        least(coalesce(p_limit, 1000), 1000)
    );
begin
    if not public.oasis_sales_actor_is_active(v_actor) then
        raise exception using
            errcode = '42501',
            message = 'PERMISSION_DENIED';
    end if;

    if (p_after_next_contact_at is null)
       <> (p_after_assignment_id is null) then
        raise exception using
            errcode = '22023',
            message = 'INVALID_SALES_FOLLOWUP_CURSOR';
    end if;

    return query
    select
        a.id,
        pg_catalog.left(coalesce(prospect.company_name, ''), 500),
        a.next_contact_at
    from public.oasis_company_sales_assignments a
    left join lateral (
        select candidate.company_name
        from public.oasis_prospect_companies candidate
        where candidate.company_uid = a.company_uid
        order by
            (candidate.id = a.company_id) desc,
            candidate.updated_at desc nulls last,
            candidate.id
        limit 1
    ) prospect on true
    where a.assigned_user_id = v_actor
      and a.status = 'follow_up'
      and a.next_contact_at is not null
      and (
          p_after_next_contact_at is null
          or (a.next_contact_at, a.id) > (
              p_after_next_contact_at,
              p_after_assignment_id
          )
      )
    order by a.next_contact_at, a.id
    limit v_limit;
end;
$$;

create or replace function public.oasis_work_inbox_feature_ready()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select
        pg_catalog.to_regclass('public.oasis_work_tasks') is not null
        and pg_catalog.to_regclass('public.oasis_work_task_events') is not null
        and pg_catalog.to_regprocedure(
            'public.oasis_list_my_sales_followups(text,integer,timestamp with time zone,uuid)'
        ) is not null
        and exists (
            select 1
            from pg_catalog.pg_attribute a
            where a.attrelid = 'public.oasis_work_tasks'::regclass
              and a.attname = 'task_version'
              and a.atttypid = 'bigint'::regtype
              and a.attnotnull
              and a.attnum > 0
              and not a.attisdropped
        );
$$;

create or replace function public.oasis_list_my_work_tasks(
    p_current_user_id text,
    p_statuses text[] default array[
        'scheduled', 'pending', 'in_progress'
    ]::text[],
    p_limit integer default 100,
    p_offset integer default 0
)
returns table (
    task_id uuid,
    task_type text,
    title text,
    priority text,
    status text,
    due_at timestamptz,
    completed_at timestamptz,
    task_version bigint,
    updated_at timestamptz,
    total_count bigint
)
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    v_actor text := pg_catalog.lower(
        pg_catalog.btrim(coalesce(p_current_user_id, ''))
    );
    v_statuses text[] := coalesce(
        p_statuses,
        array['scheduled', 'pending', 'in_progress']::text[]
    );
    v_limit integer := greatest(
        1,
        least(coalesce(p_limit, 100), 500)
    );
    v_offset integer := greatest(
        0,
        coalesce(p_offset, 0)
    );
begin
    if not public.oasis_sales_actor_is_active(v_actor) then
        raise exception using
            errcode = '42501',
            message = 'PERMISSION_DENIED';
    end if;

    if exists (
        select 1
        from pg_catalog.unnest(v_statuses) selected(status_value)
        where selected.status_value not in (
            'scheduled', 'pending', 'in_progress', 'completed', 'cancelled'
        )
    ) then
        raise exception using
            errcode = '22023',
            message = 'INVALID_TASK_STATUS_FILTER';
    end if;

    return query
    select
        t.id,
        t.task_type,
        t.title,
        t.priority,
        t.status,
        t.due_at,
        t.completed_at,
        t.task_version,
        t.updated_at,
        pg_catalog.count(*) over ()
    from public.oasis_work_tasks t
    where t.assigned_user_id = v_actor
      and (
          pg_catalog.cardinality(v_statuses) = 0
          or t.status = any(v_statuses)
      )
    order by
        case t.priority when 'high' then 0 else 1 end,
        t.due_at asc,
        t.id asc
    limit v_limit
    offset v_offset;
end;
$$;

create or replace function public.oasis_get_my_work_task_summary(
    p_current_user_id text
)
returns table (
    open_count bigint,
    overdue_count bigint,
    today_count bigint,
    week_count bigint,
    in_progress_count bigint,
    completed_today_count bigint
)
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    v_actor text := pg_catalog.lower(
        pg_catalog.btrim(coalesce(p_current_user_id, ''))
    );
    v_today date := (
        pg_catalog.now() at time zone 'Asia/Seoul'
    )::date;
begin
    if not public.oasis_sales_actor_is_active(v_actor) then
        raise exception using
            errcode = '42501',
            message = 'PERMISSION_DENIED';
    end if;

    return query
    select
        pg_catalog.count(*) filter (
            where t.status in ('scheduled', 'pending', 'in_progress')
        ),
        pg_catalog.count(*) filter (
            where t.status in ('scheduled', 'pending', 'in_progress')
              and (t.due_at at time zone 'Asia/Seoul')::date < v_today
        ),
        pg_catalog.count(*) filter (
            where t.status in ('scheduled', 'pending', 'in_progress')
              and (t.due_at at time zone 'Asia/Seoul')::date = v_today
        ),
        pg_catalog.count(*) filter (
            where t.status in ('scheduled', 'pending', 'in_progress')
              and (t.due_at at time zone 'Asia/Seoul')::date > v_today
              and (t.due_at at time zone 'Asia/Seoul')::date
                    <= v_today + 7
        ),
        pg_catalog.count(*) filter (
            where t.status = 'in_progress'
        ),
        pg_catalog.count(*) filter (
            where t.status = 'completed'
              and (t.completed_at at time zone 'Asia/Seoul')::date = v_today
        )
    from public.oasis_work_tasks t
    where t.assigned_user_id = v_actor;
end;
$$;

create or replace function public.oasis_transition_my_work_task(
    p_current_user_id text,
    p_task_id uuid,
    p_action text,
    p_expected_version bigint,
    p_defer_until timestamptz default null
)
returns table (
    success boolean,
    code text,
    task_id uuid,
    status text,
    due_at timestamptz,
    task_version bigint,
    updated_at timestamptz
)
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
    v_actor text := pg_catalog.lower(
        pg_catalog.btrim(coalesce(p_current_user_id, ''))
    );
    v_action text := pg_catalog.lower(
        pg_catalog.btrim(coalesce(p_action, ''))
    );
    v_task public.oasis_work_tasks%rowtype;
    v_saved public.oasis_work_tasks%rowtype;
    v_event_type text;
begin
    if not public.oasis_sales_actor_is_active(v_actor) then
        raise exception using
            errcode = '42501',
            message = 'PERMISSION_DENIED';
    end if;

    if p_task_id is null
       or p_expected_version is null
       or p_expected_version < 1
       or v_action not in ('start', 'complete', 'defer')
       or (v_action <> 'defer' and p_defer_until is not null)
       or (v_action = 'defer' and p_defer_until is null) then
        return query select
            false, 'INVALID_REQUEST', p_task_id,
            null::text, null::timestamptz, null::bigint, null::timestamptz;
        return;
    end if;

    select t.*
    into v_task
    from public.oasis_work_tasks t
    where t.id = p_task_id
      and t.assigned_user_id = v_actor
    for update;

    if not found then
        return query select
            false, 'NOT_FOUND', p_task_id,
            null::text, null::timestamptz, null::bigint, null::timestamptz;
        return;
    end if;

    -- Lost-response retries are idempotent even though the first successful
    -- request already advanced task_version. Other stale writes still fail.
    if v_action = 'start' then
        if v_task.status = 'in_progress' then
            return query select
                true, 'ALREADY_IN_PROGRESS', v_task.id, v_task.status,
                v_task.due_at, v_task.task_version, v_task.updated_at;
            return;
        end if;
        if v_task.status in ('completed', 'cancelled') then
            return query select
                false, 'TASK_TERMINAL', v_task.id, v_task.status,
                v_task.due_at, v_task.task_version, v_task.updated_at;
            return;
        end if;
    elsif v_action = 'complete' then
        if v_task.status = 'completed' then
            return query select
                true, 'ALREADY_COMPLETED', v_task.id, v_task.status,
                v_task.due_at, v_task.task_version, v_task.updated_at;
            return;
        end if;
        if v_task.status = 'cancelled' then
            return query select
                false, 'TASK_TERMINAL', v_task.id, v_task.status,
                v_task.due_at, v_task.task_version, v_task.updated_at;
            return;
        end if;
    else
        if v_task.status in ('completed', 'cancelled') then
            return query select
                false, 'TASK_TERMINAL', v_task.id, v_task.status,
                v_task.due_at, v_task.task_version, v_task.updated_at;
            return;
        end if;
        if v_task.status = 'scheduled'
           and v_task.due_at = p_defer_until then
            return query select
                true, 'ALREADY_DEFERRED', v_task.id, v_task.status,
                v_task.due_at, v_task.task_version, v_task.updated_at;
            return;
        end if;
    end if;

    if v_action = 'defer'
       and (
            p_defer_until < pg_catalog.clock_timestamp() + interval '1 minute'
            or p_defer_until > pg_catalog.clock_timestamp() + interval '365 days'
       ) then
        return query select
            false, 'INVALID_REQUEST', p_task_id,
            null::text, null::timestamptz, null::bigint, null::timestamptz;
        return;
    end if;

    if v_task.task_version <> p_expected_version then
        return query select
            false, 'STALE_TASK', v_task.id, v_task.status,
            v_task.due_at, v_task.task_version, v_task.updated_at;
        return;
    end if;

    if v_action = 'start' then
        update public.oasis_work_tasks t
        set status = 'in_progress'
        where t.id = v_task.id
        returning t.* into v_saved;
        v_event_type := 'started';
    elsif v_action = 'complete' then
        update public.oasis_work_tasks t
        set
            status = 'completed',
            completed_at = coalesce(t.completed_at, pg_catalog.now())
        where t.id = v_task.id
        returning t.* into v_saved;
        v_event_type := 'completed';
    else

        update public.oasis_work_tasks t
        set
            status = 'scheduled',
            due_at = p_defer_until
        where t.id = v_task.id
        returning t.* into v_saved;
        v_event_type := 'deferred';
    end if;

    insert into public.oasis_work_task_events (
        task_id,
        actor_user_id,
        event_type,
        from_status,
        to_status,
        from_due_at,
        to_due_at,
        task_version
    ) values (
        v_saved.id,
        v_actor,
        v_event_type,
        v_task.status,
        v_saved.status,
        v_task.due_at,
        v_saved.due_at,
        v_saved.task_version
    );

    return query select
        true,
        case v_action
            when 'start' then 'STARTED'
            when 'complete' then 'COMPLETED'
            else 'DEFERRED'
        end,
        v_saved.id,
        v_saved.status,
        v_saved.due_at,
        v_saved.task_version,
        v_saved.updated_at;
end;
$$;

alter table public.oasis_work_tasks enable row level security;
alter table public.oasis_work_tasks force row level security;
alter table public.oasis_work_task_events enable row level security;
alter table public.oasis_work_task_events force row level security;

revoke all on table public.oasis_work_task_events
    from PUBLIC, anon, authenticated, service_role;
grant select on table public.oasis_work_task_events to service_role;

do $v912_function_acl$
declare
    fn record;
begin
    for fn in
        select
            p.proname,
            pg_catalog.pg_get_function_identity_arguments(p.oid)
                as identity_arguments
        from pg_catalog.pg_proc p
        join pg_catalog.pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'public'
          and p.proname in (
              'oasis_work_task_touch_updated_at',
              'oasis_work_task_event_is_immutable',
              'oasis_list_my_sales_followups',
              'oasis_work_inbox_feature_ready',
              'oasis_list_my_work_tasks',
              'oasis_get_my_work_task_summary',
              'oasis_transition_my_work_task'
          )
    loop
        execute pg_catalog.format(
            'revoke all on function public.%I(%s) from PUBLIC, anon, authenticated, service_role',
            fn.proname,
            fn.identity_arguments
        );
        execute pg_catalog.format(
            'grant execute on function public.%I(%s) to service_role',
            fn.proname,
            fn.identity_arguments
        );
    end loop;
end;
$v912_function_acl$;

comment on column public.oasis_work_tasks.task_version is
    'Monotonic optimistic-concurrency version; source task identities never change.';
comment on table public.oasis_work_task_events is
    'Append-only PII-free history of assignee-initiated work task transitions.';
comment on column public.oasis_work_task_events.event_type is
    'Fixed safe action code only; customer names, phone numbers, business numbers, and authentication data are prohibited.';

notify pgrst, 'reload schema';

commit;
