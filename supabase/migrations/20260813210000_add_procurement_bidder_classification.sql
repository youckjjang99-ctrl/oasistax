-- Keep only compact, irreversible bidder signals and matched phone-contact rows.
-- Raw G2B payloads, company names, phone numbers, and plain business numbers
-- are intentionally not retained in the bidder signal table.

begin;

create table if not exists public.oasis_procurement_bidder_signals (
    business_no_hash text primary key,
    first_bid_date date,
    last_bid_date date,
    has_won boolean not null default false,
    last_business_category text not null default '',
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint oasis_procurement_bidder_signals_hash_check
        check (business_no_hash ~ '^[0-9a-f]{64}$'),
    constraint oasis_procurement_bidder_signals_category_length_check
        check (length(last_business_category) <= 30)
);

create index if not exists oasis_procurement_bidder_signals_last_bid_idx
    on public.oasis_procurement_bidder_signals (last_bid_date desc)
    where last_bid_date is not null;

create table if not exists public.oasis_procurement_contact_activity (
    contact_key text primary key,
    business_no_hash text not null,
    activity_status text not null default 'bidder',
    first_bid_date date,
    last_bid_date date,
    last_business_category text not null default '',
    classified_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint oasis_procurement_contact_activity_hash_check
        check (business_no_hash ~ '^[0-9a-f]{64}$'),
    constraint oasis_procurement_contact_activity_status_check
        check (activity_status in ('bidder', 'winner')),
    constraint oasis_procurement_contact_activity_category_length_check
        check (length(last_business_category) <= 30)
);

create index if not exists oasis_procurement_contact_activity_status_idx
    on public.oasis_procurement_contact_activity (
        activity_status,
        last_bid_date desc,
        contact_key
    );

create index if not exists oasis_procurement_contact_activity_hash_idx
    on public.oasis_procurement_contact_activity (business_no_hash);

create table if not exists public.oasis_procurement_sync_runs (
    sync_key text primary key,
    target_date date,
    status text not null default 'pending',
    api_call_count integer not null default 0,
    source_item_count integer not null default 0,
    bidder_signal_count integer not null default 0,
    matched_contact_count integer not null default 0,
    message text not null default '',
    started_at timestamptz,
    completed_at timestamptz,
    updated_at timestamptz not null default now(),
    constraint oasis_procurement_sync_runs_key_length_check
        check (length(sync_key) between 1 and 100),
    constraint oasis_procurement_sync_runs_status_check
        check (status in ('pending', 'running', 'completed', 'partial', 'failed')),
    constraint oasis_procurement_sync_runs_nonnegative_check
        check (
            api_call_count >= 0
            and source_item_count >= 0
            and bidder_signal_count >= 0
            and matched_contact_count >= 0
        ),
    constraint oasis_procurement_sync_runs_message_length_check
        check (length(message) <= 1000)
);

-- A BRIN index stays small while supporting the once-daily KST date scan.
create index if not exists oasis_employment_contacts_phone_checked_brin_idx
    on public.oasis_employment_contacts
    using brin (phone_checked_at)
    where (has_mobile_phone or has_landline_phone)
      and business_no ~ '^[0-9]{10}$';

alter table public.oasis_procurement_bidder_signals enable row level security;
alter table public.oasis_procurement_contact_activity enable row level security;
alter table public.oasis_procurement_sync_runs enable row level security;

revoke all on table public.oasis_procurement_bidder_signals
    from public, anon, authenticated;
revoke all on table public.oasis_procurement_contact_activity
    from public, anon, authenticated;
revoke all on table public.oasis_procurement_sync_runs
    from public, anon, authenticated;

grant select, insert, update, delete
    on table public.oasis_procurement_bidder_signals to service_role;
grant select, insert, update, delete
    on table public.oasis_procurement_contact_activity to service_role;
grant select, insert, update, delete
    on table public.oasis_procurement_sync_runs to service_role;

create or replace function public.oasis_upsert_procurement_bidder_signals(
    p_rows jsonb
)
returns table (
    signal_count integer,
    matched_contact_count integer
)
language plpgsql
volatile
security invoker
set search_path = public, extensions, pg_temp
as $$
declare
    v_signal_count integer := 0;
    v_contact_count integer := 0;
begin
    if jsonb_typeof(p_rows) is distinct from 'array' then
        raise exception 'p_rows must be a JSON array';
    end if;

    create temporary table if not exists pg_temp.oasis_procurement_input (
        business_no text primary key,
        business_no_hash text not null,
        first_bid_date date,
        last_bid_date date,
        has_won boolean not null,
        last_business_category text not null
    ) on commit drop;
    truncate table pg_temp.oasis_procurement_input;

    insert into pg_temp.oasis_procurement_input (
        business_no,
        business_no_hash,
        first_bid_date,
        last_bid_date,
        has_won,
        last_business_category
    )
    with input_rows as (
        select
            regexp_replace(coalesce(r.business_no, ''), '[^0-9]', '', 'g')
                as business_no,
            case
                when replace(coalesce(r.bid_date, ''), '-', '') ~ '^[0-9]{8}$'
                then to_date(replace(r.bid_date, '-', ''), 'YYYYMMDD')
                else null
            end as bid_date,
            coalesce(r.has_won, false) as has_won,
            left(btrim(coalesce(r.business_category, '')), 30)
                as business_category
        from jsonb_to_recordset(p_rows) as r(
            business_no text,
            bid_date text,
            has_won boolean,
            business_category text
        )
    ),
    valid_rows as (
        select *
        from input_rows
        where business_no ~ '^[0-9]{10}$'
          and (bid_date is null or bid_date <= current_date)
    )
    select
        business_no,
        encode(
            extensions.digest(convert_to(business_no, 'UTF8'), 'sha256'),
            'hex'
        ),
        min(bid_date),
        max(bid_date),
        bool_or(has_won),
        coalesce(
            (
                array_agg(business_category order by bid_date desc nulls last)
                    filter (where business_category <> '')
            )[1],
            ''
        )
    from valid_rows
    group by business_no;

    with upserted as (
        insert into public.oasis_procurement_bidder_signals (
            business_no_hash,
            first_bid_date,
            last_bid_date,
            has_won,
            last_business_category,
            last_seen_at,
            updated_at
        )
        select
            business_no_hash,
            first_bid_date,
            last_bid_date,
            has_won,
            last_business_category,
            now(),
            now()
        from pg_temp.oasis_procurement_input
        on conflict (business_no_hash) do update
        set first_bid_date = case
                when public.oasis_procurement_bidder_signals.first_bid_date is null
                    then excluded.first_bid_date
                when excluded.first_bid_date is null
                    then public.oasis_procurement_bidder_signals.first_bid_date
                else least(
                    public.oasis_procurement_bidder_signals.first_bid_date,
                    excluded.first_bid_date
                )
            end,
            last_bid_date = case
                when public.oasis_procurement_bidder_signals.last_bid_date is null
                    then excluded.last_bid_date
                when excluded.last_bid_date is null
                    then public.oasis_procurement_bidder_signals.last_bid_date
                else greatest(
                    public.oasis_procurement_bidder_signals.last_bid_date,
                    excluded.last_bid_date
                )
            end,
            has_won = public.oasis_procurement_bidder_signals.has_won
                or excluded.has_won,
            last_business_category = case
                when excluded.last_bid_date >= coalesce(
                    public.oasis_procurement_bidder_signals.last_bid_date,
                    '-infinity'::date
                ) and excluded.last_business_category <> ''
                then excluded.last_business_category
                else public.oasis_procurement_bidder_signals.last_business_category
            end,
            last_seen_at = now(),
            updated_at = now()
        returning 1
    )
    select count(*)::integer into v_signal_count from upserted;

    with matched as (
        select
            c.contact_key,
            i.business_no_hash,
            case when s.has_won then 'winner' else 'bidder' end as activity_status,
            s.first_bid_date,
            s.last_bid_date,
            s.last_business_category
        from pg_temp.oasis_procurement_input i
        join public.oasis_employment_contacts c
          on c.business_no = i.business_no
         and (c.has_mobile_phone or c.has_landline_phone)
        join public.oasis_procurement_bidder_signals s
          on s.business_no_hash = i.business_no_hash
    ),
    upserted as (
        insert into public.oasis_procurement_contact_activity (
            contact_key,
            business_no_hash,
            activity_status,
            first_bid_date,
            last_bid_date,
            last_business_category,
            classified_at,
            updated_at
        )
        select
            contact_key,
            business_no_hash,
            activity_status,
            first_bid_date,
            last_bid_date,
            last_business_category,
            now(),
            now()
        from matched
        on conflict (contact_key) do update
        set business_no_hash = excluded.business_no_hash,
            activity_status = excluded.activity_status,
            first_bid_date = excluded.first_bid_date,
            last_bid_date = excluded.last_bid_date,
            last_business_category = excluded.last_business_category,
            classified_at = now(),
            updated_at = now()
        returning 1
    )
    select count(*)::integer into v_contact_count from upserted;

    return query select v_signal_count, v_contact_count;
end;
$$;

create or replace function public.oasis_refresh_procurement_contact_activity(
    p_since timestamptz default null
)
returns integer
language plpgsql
volatile
security invoker
set search_path = public, extensions, pg_temp
as $$
declare
    v_affected integer := 0;
begin
    create temporary table if not exists pg_temp.oasis_procurement_candidates (
        contact_key text primary key,
        business_no_hash text not null
    ) on commit drop;
    truncate table pg_temp.oasis_procurement_candidates;

    insert into pg_temp.oasis_procurement_candidates (
        contact_key,
        business_no_hash
    )
    select
        c.contact_key,
        encode(
            extensions.digest(convert_to(c.business_no, 'UTF8'), 'sha256'),
            'hex'
        )
    from public.oasis_employment_contacts c
    where (c.has_mobile_phone or c.has_landline_phone)
      and c.business_no ~ '^[0-9]{10}$'
      and (
          p_since is null
          or c.phone_checked_at >= p_since
      );

    delete from public.oasis_procurement_contact_activity a
    using pg_temp.oasis_procurement_candidates c
    where a.contact_key = c.contact_key
      and not exists (
          select 1
          from public.oasis_procurement_bidder_signals s
          where s.business_no_hash = c.business_no_hash
      );

    with matched as (
        select
            c.contact_key,
            c.business_no_hash,
            case when s.has_won then 'winner' else 'bidder' end as activity_status,
            s.first_bid_date,
            s.last_bid_date,
            s.last_business_category
        from pg_temp.oasis_procurement_candidates c
        join public.oasis_procurement_bidder_signals s
          on s.business_no_hash = c.business_no_hash
    ),
    upserted as (
        insert into public.oasis_procurement_contact_activity (
            contact_key,
            business_no_hash,
            activity_status,
            first_bid_date,
            last_bid_date,
            last_business_category,
            classified_at,
            updated_at
        )
        select
            contact_key,
            business_no_hash,
            activity_status,
            first_bid_date,
            last_bid_date,
            last_business_category,
            now(),
            now()
        from matched
        on conflict (contact_key) do update
        set business_no_hash = excluded.business_no_hash,
            activity_status = excluded.activity_status,
            first_bid_date = excluded.first_bid_date,
            last_bid_date = excluded.last_bid_date,
            last_business_category = excluded.last_business_category,
            classified_at = now(),
            updated_at = now()
        returning 1
    )
    select count(*)::integer into v_affected from upserted;

    return v_affected;
end;
$$;

create or replace function public.oasis_refresh_today_procurement_contacts()
returns integer
language sql
volatile
security invoker
set search_path = public, pg_temp
as $$
    select public.oasis_refresh_procurement_contact_activity(
        (
            timezone('Asia/Seoul', now())::date::timestamp
            at time zone 'Asia/Seoul'
        )
    );
$$;

create or replace function public.oasis_lookup_procurement_activity(
    p_business_nos jsonb
)
returns table (
    business_no text,
    activity_status text,
    first_bid_date date,
    last_bid_date date,
    last_business_category text
)
language sql
stable
security invoker
set search_path = public, extensions, pg_temp
as $$
    with requested as (
        select distinct
            regexp_replace(value, '[^0-9]', '', 'g') as business_no
        from jsonb_array_elements_text(
            case
                when jsonb_typeof(p_business_nos) = 'array'
                    then p_business_nos
                else '[]'::jsonb
            end
        )
    )
    select
        r.business_no,
        case when s.has_won then 'winner' else 'bidder' end,
        s.first_bid_date,
        s.last_bid_date,
        s.last_business_category
    from requested r
    join public.oasis_procurement_bidder_signals s
      on s.business_no_hash = encode(
          extensions.digest(convert_to(r.business_no, 'UTF8'), 'sha256'),
          'hex'
      )
    where r.business_no ~ '^[0-9]{10}$';
$$;

revoke all on function public.oasis_upsert_procurement_bidder_signals(jsonb)
    from public, anon, authenticated;
revoke all on function public.oasis_refresh_procurement_contact_activity(timestamptz)
    from public, anon, authenticated;
revoke all on function public.oasis_refresh_today_procurement_contacts()
    from public, anon, authenticated;
revoke all on function public.oasis_lookup_procurement_activity(jsonb)
    from public, anon, authenticated;

grant execute on function public.oasis_upsert_procurement_bidder_signals(jsonb)
    to service_role;
grant execute on function public.oasis_refresh_procurement_contact_activity(timestamptz)
    to service_role;
grant execute on function public.oasis_refresh_today_procurement_contacts()
    to service_role;
grant execute on function public.oasis_lookup_procurement_activity(jsonb)
    to service_role;

comment on table public.oasis_procurement_bidder_signals is
    'Irreversible SHA-256 bidder fingerprints with compact activity dates only; no raw G2B payload or plain business number.';
comment on table public.oasis_procurement_contact_activity is
    'Procurement classifications only for DB-discovery companies with a collected mobile or landline phone.';

create extension if not exists pg_cron with schema pg_catalog;

do $oasis_procurement_cron$
declare
    v_job_id bigint;
begin
    for v_job_id in
        select jobid
        from cron.job
        where jobname = 'oasis-procurement-contact-refresh-10am-kst'
          and database = current_database()
    loop
        perform cron.unschedule(v_job_id);
    end loop;

    perform cron.schedule(
        'oasis-procurement-contact-refresh-10am-kst',
        '0 1 * * *',
        'select public.oasis_refresh_today_procurement_contacts();'
    );
end
$oasis_procurement_cron$;

notify pgrst, 'reload schema';

commit;
