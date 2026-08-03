-- OASIS CRM v9.11.0 - customer information integration
--
-- Additive identity crosswalks and lossless customer-profile RPCs. Existing
-- customer UUIDs and existing sales/prospect company_uid values are never
-- rewritten. Automatic links require one owner-scoped customer and one
-- canonical company_uid with the same normalized 10-digit business number.

begin;

-- ---------------------------------------------------------------------------
-- 1. Prerequisite and compatibility guards
-- ---------------------------------------------------------------------------

do $v911_guard$
declare
    v_column text;
    v_type regtype;
    v_table text;
    v_relation regclass;
begin
    if to_regclass('public.oasis_customers') is null then
        raise exception using
            errcode = '42P01',
            message = 'OASIS_V911_REQUIRES_PUBLIC_OASIS_CUSTOMERS';
    end if;

    for v_column, v_type in
        select required.column_name, required.column_type
        from (values
            ('id', 'uuid'::regtype),
            ('owner_user_id', 'text'::regtype),
            ('business_no', 'text'::regtype),
            ('company_name', 'text'::regtype),
            ('representative_name', 'text'::regtype),
            ('industry_name', 'text'::regtype),
            ('address', 'text'::regtype),
            ('manager_name', 'text'::regtype),
            ('source', 'text'::regtype),
            ('customer_data', 'jsonb'::regtype),
            ('lifecycle_status', 'text'::regtype),
            ('created_at', 'timestamp with time zone'::regtype),
            ('updated_at', 'timestamp with time zone'::regtype)
        ) as required(column_name, column_type)
    loop
        if not exists (
            select 1
            from pg_catalog.pg_attribute a
            where a.attrelid = 'public.oasis_customers'::regclass
              and a.attname = v_column
              and a.atttypid = v_type
              and a.attnum > 0
              and not a.attisdropped
        ) then
            raise exception using
                errcode = '42804',
                message = 'OASIS_V911_CUSTOMER_PROFILE_SCHEMA_INCOMPATIBLE';
        end if;
    end loop;

    if not exists (
        select 1
        from pg_catalog.pg_constraint c
        where c.conrelid = 'public.oasis_customers'::regclass
          and c.conname = 'oasis_customers_id_owner_user_id_unique'
          and c.contype = 'u'
    ) then
        raise exception using
            errcode = '55000',
            message = 'OASIS_V911_REQUIRES_V990_CUSTOMER_IDENTITY_GUARD';
    end if;

    if to_regprocedure('public.oasis_is_valid_company_uid(text)') is null then
        raise exception using
            errcode = '42883',
            message = 'OASIS_V911_REQUIRES_COMPANY_UID_VALIDATOR';
    end if;

    if to_regclass('public.oasis_prospect_companies') is not null then
        for v_column, v_type in
            select required.column_name, required.column_type
            from (values
                ('owner_user_id', 'text'::regtype),
                ('business_no', 'text'::regtype),
                ('company_uid', 'text'::regtype)
            ) as required(column_name, column_type)
        loop
            if not exists (
                select 1
                from pg_catalog.pg_attribute a
                where a.attrelid = 'public.oasis_prospect_companies'::regclass
                  and a.attname = v_column
                  and a.atttypid = v_type
                  and a.attnum > 0
                  and not a.attisdropped
            ) then
                raise exception using
                    errcode = '42804',
                    message = 'OASIS_V911_PROSPECT_IDENTITY_SCHEMA_INCOMPATIBLE';
            end if;
        end loop;
    end if;

    if to_regclass('public.oasis_company_sales_assignments') is not null then
        for v_column, v_type in
            select required.column_name, required.column_type
            from (values
                ('assigned_user_id', 'text'::regtype),
                ('company_uid', 'text'::regtype)
            ) as required(column_name, column_type)
        loop
            if not exists (
                select 1
                from pg_catalog.pg_attribute a
                where a.attrelid = 'public.oasis_company_sales_assignments'::regclass
                  and a.attname = v_column
                  and a.atttypid = v_type
                  and a.attnum > 0
                  and not a.attisdropped
            ) then
                raise exception using
                    errcode = '42804',
                    message = 'OASIS_V911_SALES_IDENTITY_SCHEMA_INCOMPATIBLE';
            end if;
        end loop;
    end if;

    foreach v_table in array array[
        'oasis_crm',
        'oasis_financials',
        'oasis_registry',
        'oasis_matching_preferences',
        'oasis_customer_history',
        'oasis_consultation_journals',
        'oasis_customer_trash',
        'oasis_stock_valuations'
    ]
    loop
        v_relation := to_regclass('public.' || v_table);
        if v_relation is not null then
            if not exists (
                select 1
                from pg_catalog.pg_attribute a
                where a.attrelid = v_relation
                  and a.attname = 'owner_user_id'
                  and a.atttypid = 'text'::regtype
                  and a.attnum > 0
                  and not a.attisdropped
            ) or not exists (
                select 1
                from pg_catalog.pg_attribute a
                where a.attrelid = v_relation
                  and a.attname = 'business_no'
                  and a.atttypid = 'text'::regtype
                  and a.attnum > 0
                  and not a.attisdropped
            ) then
                raise exception using
                    errcode = '42804',
                    message = 'OASIS_V911_DEPENDENT_IDENTITY_SCHEMA_INCOMPATIBLE';
            end if;

            if exists (
                select 1
                from pg_catalog.pg_attribute a
                where a.attrelid = v_relation
                  and a.attname = 'customer_id'
                  and a.attnum > 0
                  and not a.attisdropped
                  and (a.atttypid <> 'uuid'::regtype or a.attnotnull)
            ) then
                raise exception using
                    errcode = '42804',
                    message = 'OASIS_V911_EXISTING_CUSTOMER_LINK_SCHEMA_INCOMPATIBLE';
            end if;
        end if;
    end loop;
end;
$v911_guard$;

-- ---------------------------------------------------------------------------
-- 2. Exact identity and lossless JSON helpers
-- ---------------------------------------------------------------------------

create or replace function public.oasis_v911_normalize_business_no(
    p_value text
)
returns text
language plpgsql
immutable
security invoker
set search_path = public, pg_temp
as $$
declare
    v_digits text := pg_catalog.regexp_replace(
        coalesce(p_value, ''),
        '[^0-9]',
        '',
        'g'
    );
begin
    if v_digits ~ '^[0-9]{10}$' then
        return v_digits;
    end if;
    return null;
end;
$$;

create or replace function public.oasis_v911_lossless_jsonb_merge(
    p_existing jsonb,
    p_incoming jsonb
)
returns jsonb
language plpgsql
immutable
security invoker
set search_path = public, pg_temp
as $$
declare
    v_result jsonb;
    v_key text;
    v_value jsonb;
    v_existing_value jsonb;
    v_kind text;
begin
    -- Legacy non-object payloads are retained verbatim. Converting an array or
    -- scalar to an object here would be a lossy rewrite.
    if p_existing is not null
       and p_existing <> 'null'::jsonb
       and pg_catalog.jsonb_typeof(p_existing) <> 'object' then
        return p_existing;
    end if;

    v_result := coalesce(p_existing, '{}'::jsonb);

    if p_incoming is null or p_incoming = 'null'::jsonb then
        return v_result;
    end if;

    if pg_catalog.jsonb_typeof(p_incoming) <> 'object' then
        return v_result;
    end if;

    for v_key, v_value in
        select item.key, item.value
        from pg_catalog.jsonb_each(p_incoming) as item(key, value)
    loop
        v_kind := pg_catalog.jsonb_typeof(v_value);

        -- Null, blank strings, empty objects, and empty arrays cannot erase
        -- a previously stored value.
        if v_value = 'null'::jsonb
           or (v_kind = 'string' and nullif(pg_catalog.btrim(v_value #>> '{}'), '') is null)
           or (v_kind = 'object' and v_value = '{}'::jsonb)
           or (v_kind = 'array' and v_value = '[]'::jsonb) then
            continue;
        end if;

        v_existing_value := v_result -> v_key;
        if v_kind = 'object'
           and pg_catalog.jsonb_typeof(v_existing_value) = 'object' then
            v_result := pg_catalog.jsonb_set(
                v_result,
                array[v_key],
                public.oasis_v911_lossless_jsonb_merge(
                    v_existing_value,
                    v_value
                ),
                true
            );
        else
            v_result := pg_catalog.jsonb_set(
                v_result,
                array[v_key],
                v_value,
                true
            );
        end if;
    end loop;

    return v_result;
end;
$$;

create index if not exists idx_oasis_customers_owner_normalized_business_no
    on public.oasis_customers (
        owner_user_id,
        public.oasis_v911_normalize_business_no(business_no)
    )
    where public.oasis_v911_normalize_business_no(business_no) is not null;

do $v911_prospect_index$
begin
    if to_regclass('public.oasis_prospect_companies') is not null then
        execute $index$
            create index if not exists idx_oasis_prospects_owner_normalized_business_no
            on public.oasis_prospect_companies (
                owner_user_id,
                public.oasis_v911_normalize_business_no(business_no)
            )
            where public.oasis_v911_normalize_business_no(business_no) is not null
        $index$;
    end if;
end;
$v911_prospect_index$;

-- ---------------------------------------------------------------------------
-- 3. Owner-scoped immutable crosswalk and PII-free review queue
-- ---------------------------------------------------------------------------

create table if not exists public.oasis_customer_company_links (
    id uuid primary key default gen_random_uuid(),
    owner_user_id text not null,
    customer_id uuid not null,
    company_uid text not null,
    match_method text not null default 'exact_normalized_business_no',
    created_at timestamptz not null default now(),
    constraint oasis_customer_company_links_owner_customer_unique
        unique (owner_user_id, customer_id),
    constraint oasis_customer_company_links_customer_owner_fkey
        foreign key (customer_id, owner_user_id)
        references public.oasis_customers(id, owner_user_id),
    constraint oasis_customer_company_links_company_uid_nonblank_check
        check (nullif(btrim(company_uid), '') is not null),
    constraint oasis_customer_company_links_match_method_check
        check (match_method = 'exact_normalized_business_no')
);

create table if not exists public.oasis_customer_identity_reviews (
    id uuid primary key default gen_random_uuid(),
    owner_user_id text not null,
    customer_id uuid not null,
    reason_code text not null,
    source_relation text not null default 'oasis_customers',
    candidate_count integer not null default 0,
    review_status text not null default 'pending',
    first_seen_at timestamptz not null default now(),
    constraint oasis_customer_identity_reviews_dedup_unique
        unique (owner_user_id, customer_id, reason_code, source_relation),
    constraint oasis_customer_identity_reviews_customer_owner_fkey
        foreign key (customer_id, owner_user_id)
        references public.oasis_customers(id, owner_user_id),
    constraint oasis_customer_identity_reviews_reason_check
        check (reason_code in (
            'duplicate_customer_business_number',
            'multiple_company_uid_candidates',
            'dependent_record_ambiguous',
            'business_number_changed_link_unchanged'
        )),
    constraint oasis_customer_identity_reviews_status_check
        check (review_status in ('pending', 'acknowledged', 'resolved')),
    constraint oasis_customer_identity_reviews_candidate_count_check
        check (candidate_count >= 0)
);

create index if not exists idx_oasis_customer_company_links_company_uid
    on public.oasis_customer_company_links (company_uid);

create index if not exists idx_oasis_customer_company_links_customer_owner
    on public.oasis_customer_company_links (customer_id, owner_user_id);

create index if not exists idx_oasis_customer_identity_reviews_pending
    on public.oasis_customer_identity_reviews (
        owner_user_id,
        review_status,
        first_seen_at,
        customer_id
    );

create index if not exists idx_oasis_customer_identity_reviews_customer_owner
    on public.oasis_customer_identity_reviews (customer_id, owner_user_id);

alter table public.oasis_customer_company_links enable row level security;
alter table public.oasis_customer_company_links force row level security;
alter table public.oasis_customer_identity_reviews enable row level security;
alter table public.oasis_customer_identity_reviews force row level security;

revoke all on table public.oasis_customer_company_links
    from PUBLIC, anon, authenticated;
revoke all on table public.oasis_customer_company_links from service_role;
grant select on table public.oasis_customer_company_links to service_role;

revoke all on table public.oasis_customer_identity_reviews
    from PUBLIC, anon, authenticated;
revoke all on table public.oasis_customer_identity_reviews from service_role;
grant select on table public.oasis_customer_identity_reviews to service_role;

-- ---------------------------------------------------------------------------
-- 4. Candidate resolution and link creation (never changes an existing link)
-- ---------------------------------------------------------------------------

create or replace function public.oasis_v911_company_uid_candidates(
    p_owner_user_id text,
    p_business_no text
)
returns table (company_uid text)
language plpgsql
stable
security definer
set search_path = public, pg_temp
as $$
declare
    v_owner_user_id text := nullif(pg_catalog.btrim(p_owner_user_id), '');
    v_business_no text := public.oasis_v911_normalize_business_no(p_business_no);
begin
    if v_owner_user_id is null or v_business_no is null then
        return;
    end if;

    if to_regclass('public.oasis_prospect_companies') is null then
        return;
    end if;

    return query execute $candidate$
        select distinct pg_catalog.btrim(p.company_uid)::text
        from public.oasis_prospect_companies p
        where p.owner_user_id = $1
          and public.oasis_v911_normalize_business_no(p.business_no) = $2
          and public.oasis_is_valid_company_uid(p.company_uid)
    $candidate$
    using v_owner_user_id, v_business_no;

    if to_regclass('public.oasis_company_sales_assignments') is not null then
        return query execute $assigned_candidate$
            select distinct pg_catalog.btrim(a.company_uid)::text
            from public.oasis_company_sales_assignments a
            join public.oasis_prospect_companies p
              on p.company_uid = a.company_uid
            where a.assigned_user_id = $1
              and p.owner_user_id = $1
              and public.oasis_v911_normalize_business_no(p.business_no) = $2
              and public.oasis_is_valid_company_uid(a.company_uid)
        $assigned_candidate$
        using v_owner_user_id, v_business_no;
    end if;
end;
$$;

create or replace function public.oasis_v911_ensure_customer_company_link(
    p_owner_user_id text,
    p_customer_id uuid,
    p_business_no text
)
returns table (company_uid text, link_status text)
language plpgsql
volatile
security definer
set search_path = public, pg_temp
as $$
declare
    v_owner_user_id text := nullif(pg_catalog.btrim(p_owner_user_id), '');
    v_business_no text := public.oasis_v911_normalize_business_no(p_business_no);
    v_company_uids text[] := '{}'::text[];
    v_customer_count integer := 0;
    v_company_uid text;
    v_has_pending_review boolean := false;
begin
    if v_owner_user_id is null or p_customer_id is null or v_business_no is null then
        return query select null::text, 'invalid_request'::text;
        return;
    end if;

    select l.company_uid
    into v_company_uid
    from public.oasis_customer_company_links l
    where l.owner_user_id = v_owner_user_id
      and l.customer_id = p_customer_id;

    if v_company_uid is not null then
        select exists (
            select 1
            from public.oasis_customer_identity_reviews r
            where r.owner_user_id = v_owner_user_id
              and r.customer_id = p_customer_id
              and r.review_status = 'pending'
        ) into v_has_pending_review;

        return query select
            v_company_uid,
            case when v_has_pending_review
                then 'linked_review_required'
                else 'linked'
            end;
        return;
    end if;

    select pg_catalog.count(*)::integer
    into v_customer_count
    from public.oasis_customers c
    where c.owner_user_id = v_owner_user_id
      and public.oasis_v911_normalize_business_no(c.business_no) = v_business_no;

    if v_customer_count <> 1 then
        insert into public.oasis_customer_identity_reviews (
            owner_user_id,
            customer_id,
            reason_code,
            source_relation,
            candidate_count
        ) values (
            v_owner_user_id,
            p_customer_id,
            'duplicate_customer_business_number',
            'oasis_customers',
            v_customer_count
        ) on conflict (
            owner_user_id,
            customer_id,
            reason_code,
            source_relation
        ) do nothing;

        return query select null::text, 'ambiguous_review'::text;
        return;
    end if;

    select coalesce(
        pg_catalog.array_agg(candidate.company_uid order by candidate.company_uid),
        '{}'::text[]
    )
    into v_company_uids
    from (
        select distinct resolved.company_uid
        from public.oasis_v911_company_uid_candidates(
            v_owner_user_id,
            v_business_no
        ) resolved
    ) candidate;

    if pg_catalog.cardinality(v_company_uids) > 1 then
        insert into public.oasis_customer_identity_reviews (
            owner_user_id,
            customer_id,
            reason_code,
            source_relation,
            candidate_count
        ) values (
            v_owner_user_id,
            p_customer_id,
            'multiple_company_uid_candidates',
            'oasis_prospect_companies',
            pg_catalog.cardinality(v_company_uids)
        ) on conflict (
            owner_user_id,
            customer_id,
            reason_code,
            source_relation
        ) do nothing;

        return query select null::text, 'ambiguous_review'::text;
        return;
    end if;

    if pg_catalog.cardinality(v_company_uids) = 1 then
        insert into public.oasis_customer_company_links (
            owner_user_id,
            customer_id,
            company_uid,
            match_method
        ) values (
            v_owner_user_id,
            p_customer_id,
            v_company_uids[1],
            'exact_normalized_business_no'
        ) on conflict (owner_user_id, customer_id) do nothing;

        select l.company_uid
        into v_company_uid
        from public.oasis_customer_company_links l
        where l.owner_user_id = v_owner_user_id
          and l.customer_id = p_customer_id;

        select exists (
            select 1
            from public.oasis_customer_identity_reviews r
            where r.owner_user_id = v_owner_user_id
              and r.customer_id = p_customer_id
              and r.review_status = 'pending'
        ) into v_has_pending_review;

        return query select
            v_company_uid,
            case when v_has_pending_review
                then 'linked_review_required'
                else 'linked'
            end;
        return;
    end if;

    return query select null::text, 'unlinked'::text;
end;
$$;

-- Existing customers are linked only when both sides are unambiguous. The
-- helper inserts reviews without copying a business number, company name,
-- company_uid candidate set, or any contact field into the review queue.
do $v911_initial_crosswalk$
declare
    v_customer record;
begin
    for v_customer in
        select c.id, c.owner_user_id, c.business_no
        from public.oasis_customers c
        where public.oasis_v911_normalize_business_no(c.business_no) is not null
        order by c.owner_user_id, c.id
    loop
        perform 1
        from public.oasis_v911_ensure_customer_company_link(
            v_customer.owner_user_id,
            v_customer.id,
            v_customer.business_no
        );
    end loop;
end;
$v911_initial_crosswalk$;

-- ---------------------------------------------------------------------------
-- 5. Nullable, owner-safe customer links on existing dependent tables
-- ---------------------------------------------------------------------------

create or replace function public.oasis_v911_fill_dependent_customer_id()
returns trigger
language plpgsql
volatile
security definer
set search_path = public, pg_temp
as $$
declare
    v_business_no text;
    v_candidate_ids uuid[] := '{}'::uuid[];
    v_candidate_id uuid;
begin
    -- A previously selected link is immutable here. If an owning field is
    -- changed incompatibly, the composite foreign key rejects the write.
    if new.customer_id is not null then
        return new;
    end if;

    v_business_no := public.oasis_v911_normalize_business_no(new.business_no);
    if nullif(pg_catalog.btrim(new.owner_user_id), '') is null
       or v_business_no is null then
        return new;
    end if;

    select coalesce(
        pg_catalog.array_agg(c.id order by c.id),
        '{}'::uuid[]
    )
    into v_candidate_ids
    from public.oasis_customers c
    where c.owner_user_id = new.owner_user_id
      and public.oasis_v911_normalize_business_no(c.business_no) = v_business_no;

    if pg_catalog.cardinality(v_candidate_ids) = 1 then
        new.customer_id := v_candidate_ids[1];
        return new;
    end if;

    if pg_catalog.cardinality(v_candidate_ids) > 1 then
        foreach v_candidate_id in array v_candidate_ids
        loop
            insert into public.oasis_customer_identity_reviews (
                owner_user_id,
                customer_id,
                reason_code,
                source_relation,
                candidate_count
            ) values (
                new.owner_user_id,
                v_candidate_id,
                'dependent_record_ambiguous',
                tg_table_name,
                pg_catalog.cardinality(v_candidate_ids)
            ) on conflict (
                owner_user_id,
                customer_id,
                reason_code,
                source_relation
            ) do nothing;
        end loop;
    end if;

    return new;
end;
$$;

do $v911_dependent_links$
declare
    v_table text;
    v_relation regclass;
    v_constraint_name text;
    v_index_name text;
    v_trigger_name text;
    v_has_invalid_existing_link boolean := false;
begin
    foreach v_table in array array[
        'oasis_crm',
        'oasis_financials',
        'oasis_registry',
        'oasis_matching_preferences',
        'oasis_customer_history',
        'oasis_consultation_journals',
        'oasis_customer_trash',
        'oasis_stock_valuations'
    ]
    loop
        v_relation := to_regclass('public.' || v_table);
        if v_relation is null then
            continue;
        end if;

        execute pg_catalog.format(
            'alter table public.%I add column if not exists customer_id uuid',
            v_table
        );

        execute pg_catalog.format($check_link$
            select exists (
                select 1
                from public.%I d
                left join public.oasis_customers c
                  on c.id = d.customer_id
                 and c.owner_user_id = d.owner_user_id
                where d.customer_id is not null
                  and c.id is null
            )
        $check_link$, v_table)
        into v_has_invalid_existing_link;

        if v_has_invalid_existing_link then
            raise exception using
                errcode = '23503',
                message = 'OASIS_V911_EXISTING_CUSTOMER_LINK_OWNER_MISMATCH';
        end if;

        v_constraint_name := v_table || '_customer_id_owner_fkey';
        if not exists (
            select 1
            from pg_catalog.pg_constraint c
            where c.conrelid = v_relation
              and c.conname = v_constraint_name
              and c.contype = 'f'
        ) then
            execute pg_catalog.format(
                'alter table public.%I add constraint %I '
                || 'foreign key (customer_id, owner_user_id) '
                || 'references public.oasis_customers(id, owner_user_id) not valid',
                v_table,
                v_constraint_name
            );
        end if;

        execute pg_catalog.format(
            'alter table public.%I validate constraint %I',
            v_table,
            v_constraint_name
        );

        v_index_name := 'idx_' || v_table || '_customer_owner';
        execute pg_catalog.format(
            'create index if not exists %I on public.%I '
            || '(customer_id, owner_user_id) where customer_id is not null',
            v_index_name,
            v_table
        );

        v_trigger_name := 'trg_' || v_table || '_v911_customer_id';
        if not exists (
            select 1
            from pg_catalog.pg_trigger t
            where t.tgrelid = v_relation
              and t.tgname = v_trigger_name
              and not t.tgisinternal
        ) then
            execute pg_catalog.format(
                'create trigger %I before insert or update of owner_user_id, business_no '
                || 'on public.%I for each row '
                || 'execute function public.oasis_v911_fill_dependent_customer_id()',
                v_trigger_name,
                v_table
            );
        elsif not exists (
            select 1
            from pg_catalog.pg_trigger t
            where t.tgrelid = v_relation
              and t.tgname = v_trigger_name
              and not t.tgisinternal
              and pg_catalog.pg_get_triggerdef(t.oid) like
                  '%oasis_v911_fill_dependent_customer_id()%'
        ) then
            raise exception using
                errcode = '55000',
                message = 'OASIS_V911_DEPENDENT_TRIGGER_INCOMPATIBLE';
        end if;

        -- Record every candidate customer involved in an ambiguous dependent
        -- match, but never copy the source row or identifier into the queue.
        execute pg_catalog.format($record_ambiguous$
            with matched as (
                select
                    d.ctid as row_locator,
                    c.id as customer_id,
                    c.owner_user_id,
                    pg_catalog.count(*) over (partition by d.ctid) as candidate_count
                from public.%I d
                join public.oasis_customers c
                  on c.owner_user_id = d.owner_user_id
                 and public.oasis_v911_normalize_business_no(c.business_no)
                     = public.oasis_v911_normalize_business_no(d.business_no)
                where d.customer_id is null
                  and public.oasis_v911_normalize_business_no(d.business_no) is not null
            )
            insert into public.oasis_customer_identity_reviews (
                owner_user_id,
                customer_id,
                reason_code,
                source_relation,
                candidate_count
            )
            select distinct
                m.owner_user_id,
                m.customer_id,
                'dependent_record_ambiguous',
                %L,
                m.candidate_count::integer
            from matched m
            where m.candidate_count > 1
            on conflict (
                owner_user_id,
                customer_id,
                reason_code,
                source_relation
            ) do nothing
        $record_ambiguous$, v_table, v_table);

        -- Only null links are populated, and only for a single exact
        -- owner-scoped normalized 10-digit match.
        execute pg_catalog.format($backfill$
            with matched as (
                select
                    d.ctid as row_locator,
                    (pg_catalog.array_agg(c.id order by c.id))[1] as customer_id,
                    pg_catalog.count(*) as candidate_count
                from public.%I d
                join public.oasis_customers c
                  on c.owner_user_id = d.owner_user_id
                 and public.oasis_v911_normalize_business_no(c.business_no)
                     = public.oasis_v911_normalize_business_no(d.business_no)
                where d.customer_id is null
                  and public.oasis_v911_normalize_business_no(d.business_no) is not null
                group by d.ctid
            )
            update public.%I d
            set customer_id = m.customer_id
            from matched m
            where d.ctid = m.row_locator
              and m.candidate_count = 1
              and d.customer_id is null
        $backfill$, v_table, v_table);
    end loop;
end;
$v911_dependent_links$;

-- ---------------------------------------------------------------------------
-- 6. Lossless service-role customer profile RPCs
-- ---------------------------------------------------------------------------

create or replace function public.oasis_upsert_customer_profile(
    p_owner_user_id text,
    p_business_no text,
    p_company_name text default null,
    p_representative_name text default null,
    p_industry_name text default null,
    p_address text default null,
    p_manager_name text default null,
    p_source text default 'app',
    p_customer_data jsonb default '{}'::jsonb,
    p_customer_id uuid default null,
    p_previous_business_no text default null
)
returns table (
    customer_id uuid,
    company_uid text,
    created boolean,
    link_status text
)
language plpgsql
volatile
security definer
set search_path = public, pg_temp
as $$
declare
    v_owner_user_id text := nullif(pg_catalog.btrim(p_owner_user_id), '');
    v_business_no text := public.oasis_v911_normalize_business_no(p_business_no);
    v_previous_business_no text;
    v_id_target uuid;
    v_previous_target uuid;
    v_target_id uuid;
    v_reference_count integer := 0;
    v_collision_count integer := 0;
    v_existing public.oasis_customers%rowtype;
    v_old_business_no text;
    v_company_uid text;
    v_link_status text;
    v_created boolean := false;
begin
    if v_owner_user_id is null then
        return query select null::uuid, null::text, false, 'invalid_owner'::text;
        return;
    end if;

    if v_business_no is null then
        return query select null::uuid, null::text, false, 'invalid_business_number'::text;
        return;
    end if;

    if p_customer_data is not null
       and pg_catalog.jsonb_typeof(p_customer_data) <> 'object' then
        return query select null::uuid, null::text, false, 'invalid_customer_data'::text;
        return;
    end if;

    if p_previous_business_no is not null then
        v_previous_business_no := public.oasis_v911_normalize_business_no(
            p_previous_business_no
        );
        if v_previous_business_no is null then
            return query select
                null::uuid,
                null::text,
                false,
                'invalid_previous_business_number'::text;
            return;
        end if;
    end if;

    -- Serialize all writes for the owner/business identity. Row locking below
    -- additionally serializes two corrections that reference the same UUID.
    perform pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            'oasis:v911:customer:' || v_owner_user_id || ':'
            || case
                when v_previous_business_no is null then v_business_no
                else least(v_business_no, v_previous_business_no)
            end,
            0
        )
    );

    if v_previous_business_no is not null
       and v_previous_business_no <> v_business_no then
        perform pg_catalog.pg_advisory_xact_lock(
            pg_catalog.hashtextextended(
                'oasis:v911:customer:' || v_owner_user_id || ':'
                || greatest(v_business_no, v_previous_business_no),
                0
            )
        );
    end if;

    if p_customer_id is not null then
        select c.id
        into v_id_target
        from public.oasis_customers c
        where c.id = p_customer_id
          and c.owner_user_id = v_owner_user_id
        for update;

        if v_id_target is null then
            return query select null::uuid, null::text, false, 'customer_not_found'::text;
            return;
        end if;
    end if;

    if v_previous_business_no is not null then
        select
            pg_catalog.count(*)::integer,
            (pg_catalog.array_agg(c.id order by c.id))[1]
        into v_reference_count, v_previous_target
        from public.oasis_customers c
        where c.owner_user_id = v_owner_user_id
          and public.oasis_v911_normalize_business_no(c.business_no)
              = v_previous_business_no;

        if v_reference_count = 0 then
            return query select
                null::uuid,
                null::text,
                false,
                'previous_business_not_found'::text;
            return;
        elsif v_reference_count > 1 then
            return query select
                null::uuid,
                null::text,
                false,
                'previous_business_ambiguous'::text;
            return;
        end if;
    end if;

    if v_id_target is not null
       and v_previous_target is not null
       and v_id_target <> v_previous_target then
        return query select
            null::uuid,
            null::text,
            false,
            'customer_reference_conflict'::text;
        return;
    end if;

    v_target_id := coalesce(v_id_target, v_previous_target);

    if v_target_id is null then
        select
            pg_catalog.count(*)::integer,
            (pg_catalog.array_agg(c.id order by c.id))[1]
        into v_reference_count, v_target_id
        from public.oasis_customers c
        where c.owner_user_id = v_owner_user_id
          and public.oasis_v911_normalize_business_no(c.business_no) = v_business_no;

        if v_reference_count > 1 then
            return query select
                null::uuid,
                null::text,
                false,
                'business_number_conflict'::text;
            return;
        end if;
    end if;

    if v_target_id is not null then
        select c.*
        into v_existing
        from public.oasis_customers c
        where c.id = v_target_id
          and c.owner_user_id = v_owner_user_id
        for update;

        if v_existing.id is null then
            return query select null::uuid, null::text, false, 'customer_not_found'::text;
            return;
        end if;

        if v_previous_business_no is not null
           and public.oasis_v911_normalize_business_no(v_existing.business_no)
               <> v_previous_business_no then
            return query select
                null::uuid,
                null::text,
                false,
                'customer_reference_conflict'::text;
            return;
        end if;

        select pg_catalog.count(*)::integer
        into v_collision_count
        from public.oasis_customers c
        where c.owner_user_id = v_owner_user_id
          and c.id <> v_existing.id
          and public.oasis_v911_normalize_business_no(c.business_no) = v_business_no;

        if v_collision_count > 0 then
            return query select
                null::uuid,
                null::text,
                false,
                'business_number_conflict'::text;
            return;
        end if;

        v_old_business_no := public.oasis_v911_normalize_business_no(
            v_existing.business_no
        );

        update public.oasis_customers c
        set
            business_no = v_business_no,
            company_name = coalesce(
                nullif(pg_catalog.btrim(p_company_name), ''),
                c.company_name
            ),
            representative_name = coalesce(
                nullif(pg_catalog.btrim(p_representative_name), ''),
                c.representative_name
            ),
            industry_name = coalesce(
                nullif(pg_catalog.btrim(p_industry_name), ''),
                c.industry_name
            ),
            address = coalesce(
                nullif(pg_catalog.btrim(p_address), ''),
                c.address
            ),
            manager_name = coalesce(
                nullif(pg_catalog.btrim(p_manager_name), ''),
                c.manager_name
            ),
            source = coalesce(
                nullif(pg_catalog.btrim(p_source), ''),
                c.source
            ),
            customer_data = public.oasis_v911_lossless_jsonb_merge(
                c.customer_data,
                coalesce(p_customer_data, '{}'::jsonb)
            ),
            updated_at = now()
        where c.id = v_existing.id
          and c.owner_user_id = v_owner_user_id
        returning c.id into v_target_id;
    else
        begin
            insert into public.oasis_customers (
                owner_user_id,
                business_no,
                company_name,
                representative_name,
                industry_name,
                address,
                manager_name,
                source,
                customer_data
            ) values (
                v_owner_user_id,
                v_business_no,
                nullif(pg_catalog.btrim(p_company_name), ''),
                nullif(pg_catalog.btrim(p_representative_name), ''),
                nullif(pg_catalog.btrim(p_industry_name), ''),
                nullif(pg_catalog.btrim(p_address), ''),
                nullif(pg_catalog.btrim(p_manager_name), ''),
                coalesce(nullif(pg_catalog.btrim(p_source), ''), 'app'),
                public.oasis_v911_lossless_jsonb_merge(
                    '{}'::jsonb,
                    coalesce(p_customer_data, '{}'::jsonb)
                )
            )
            returning id into v_target_id;
            v_created := true;
        exception
            when unique_violation then
                return query select
                    null::uuid,
                    null::text,
                    false,
                    'business_number_conflict'::text;
                return;
        end;
    end if;

    select l.company_uid
    into v_company_uid
    from public.oasis_customer_company_links l
    where l.owner_user_id = v_owner_user_id
      and l.customer_id = v_target_id;

    if v_company_uid is not null
       and v_old_business_no is distinct from v_business_no
       and not v_created then
        insert into public.oasis_customer_identity_reviews (
            owner_user_id,
            customer_id,
            reason_code,
            source_relation,
            candidate_count
        ) values (
            v_owner_user_id,
            v_target_id,
            'business_number_changed_link_unchanged',
            'oasis_customer_company_links',
            1
        ) on conflict (
            owner_user_id,
            customer_id,
            reason_code,
            source_relation
        ) do nothing;

        return query select
            v_target_id,
            v_company_uid,
            v_created,
            'linked_review_required'::text;
        return;
    end if;

    select resolved.company_uid, resolved.link_status
    into v_company_uid, v_link_status
    from public.oasis_v911_ensure_customer_company_link(
        v_owner_user_id,
        v_target_id,
        v_business_no
    ) resolved;

    return query select
        v_target_id,
        v_company_uid,
        v_created,
        coalesce(v_link_status, 'unlinked');
end;
$$;

create or replace function public.oasis_list_unified_customers(
    p_owner_user_id text
)
returns table (
    id uuid,
    owner_user_id text,
    business_no text,
    company_name text,
    representative_name text,
    industry_name text,
    address text,
    manager_name text,
    source text,
    customer_data jsonb,
    lifecycle_status text,
    created_at timestamptz,
    updated_at timestamptz,
    company_uid text,
    identity_status text
)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select
        c.id,
        c.owner_user_id::text,
        c.business_no::text,
        c.company_name::text,
        c.representative_name::text,
        c.industry_name::text,
        c.address::text,
        c.manager_name::text,
        c.source::text,
        c.customer_data,
        c.lifecycle_status::text,
        c.created_at,
        c.updated_at,
        l.company_uid,
        case
            when l.customer_id is not null and exists (
                select 1
                from public.oasis_customer_identity_reviews r
                where r.owner_user_id = c.owner_user_id
                  and r.customer_id = c.id
                  and r.review_status = 'pending'
            ) then 'linked_review_required'
            when l.customer_id is not null then 'linked'
            when exists (
                select 1
                from public.oasis_customer_identity_reviews r
                where r.owner_user_id = c.owner_user_id
                  and r.customer_id = c.id
                  and r.review_status = 'pending'
            ) then 'ambiguous_review'
            else 'unlinked'
        end::text
    from public.oasis_customers c
    left join public.oasis_customer_company_links l
      on l.owner_user_id = c.owner_user_id
     and l.customer_id = c.id
    where c.owner_user_id = nullif(pg_catalog.btrim(p_owner_user_id), '')
    order by c.updated_at desc, c.id;
$$;

-- Helpers are internal to the migration/RPC implementation. Only the two
-- application RPCs are executable by service_role.
revoke all on function public.oasis_v911_normalize_business_no(text)
    from PUBLIC, anon, authenticated, service_role;
revoke all on function public.oasis_v911_lossless_jsonb_merge(jsonb, jsonb)
    from PUBLIC, anon, authenticated, service_role;
revoke all on function public.oasis_v911_company_uid_candidates(text, text)
    from PUBLIC, anon, authenticated, service_role;
revoke all on function public.oasis_v911_ensure_customer_company_link(text, uuid, text)
    from PUBLIC, anon, authenticated, service_role;
revoke all on function public.oasis_v911_fill_dependent_customer_id()
    from PUBLIC, anon, authenticated, service_role;

revoke all on function public.oasis_upsert_customer_profile(
    text, text, text, text, text, text, text, text, jsonb, uuid, text
) from PUBLIC, anon, authenticated, service_role;
grant execute on function public.oasis_upsert_customer_profile(
    text, text, text, text, text, text, text, text, jsonb, uuid, text
) to service_role;

revoke all on function public.oasis_list_unified_customers(text)
    from PUBLIC, anon, authenticated, service_role;
grant execute on function public.oasis_list_unified_customers(text)
    to service_role;

commit;
