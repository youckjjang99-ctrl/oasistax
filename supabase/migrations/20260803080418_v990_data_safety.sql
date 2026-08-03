-- OASIS CRM v9.9.0-data-safety
-- Durable customer assets, sync outbox, backup metadata, and archive history.
--
-- This migration is additive and intentionally does not migrate or rewrite
-- existing customer rows or Storage objects. The application uses custom
-- oasis_users authentication behind Railway, so browser roles receive no
-- direct table or RPC access.

begin;

-- ---------------------------------------------------------------------------
-- 1. Additive long-term retention metadata on existing customer data
-- ---------------------------------------------------------------------------

do $$
declare
    v_has_invalid_identity boolean := false;
begin
    if to_regclass('public.oasis_customers') is null then
        raise exception using
            errcode = '42P01',
            message = 'OASIS_V990_REQUIRES_PUBLIC_OASIS_CUSTOMERS';
    end if;

    if not exists (
        select 1
        from pg_attribute a
        where a.attrelid = 'public.oasis_customers'::regclass
          and a.attname = 'id'
          and a.atttypid = 'uuid'::regtype
          and a.attnum > 0
          and not a.attisdropped
    ) or not exists (
        select 1
        from pg_attribute a
        where a.attrelid = 'public.oasis_customers'::regclass
          and a.attname = 'owner_user_id'
          and a.atttypid in ('text'::regtype, 'character varying'::regtype)
          and a.attnum > 0
          and not a.attisdropped
    ) then
        raise exception using
            errcode = '42804',
            message = 'OASIS_V990_CUSTOMER_IDENTITY_SCHEMA_INCOMPATIBLE';
    end if;

    execute $query$
        select exists (
            select 1
            from public.oasis_customers
            where id is null or owner_user_id is null
            union all
            select 1
            from public.oasis_customers
            group by id, owner_user_id
            having count(*) > 1
        )
    $query$
    into v_has_invalid_identity;

    if v_has_invalid_identity then
        raise exception using
            errcode = '23505',
            message = 'OASIS_V990_CUSTOMER_IDENTITY_DUPLICATE_OR_NULL';
    end if;
end;
$$;

alter table public.oasis_customers
    add column if not exists lifecycle_status text,
    add column if not exists archived_at timestamptz,
    add column if not exists archived_by_user_id text,
    add column if not exists archive_reason text,
    add column if not exists retention_class text,
    add column if not exists merged_into_customer_id uuid;

do $$
begin
    if not exists (
            select 1
            from pg_constraint
            where conname = 'oasis_customers_id_owner_user_id_unique'
              and conrelid = 'public.oasis_customers'::regclass
       ) then
        alter table public.oasis_customers
            add constraint oasis_customers_id_owner_user_id_unique
            unique (id, owner_user_id);
    end if;
end;
$$;

do $$
begin
    if not exists (
            select 1
            from pg_constraint
            where conname = 'oasis_customers_merged_into_customer_id_fkey'
             and conrelid = 'public.oasis_customers'::regclass
       ) then
        alter table public.oasis_customers
            add constraint oasis_customers_merged_into_customer_id_fkey
            foreign key (merged_into_customer_id, owner_user_id)
            references public.oasis_customers(id, owner_user_id);
    end if;
end;
$$;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'oasis_customers_merged_into_customer_id_not_self_check'
          and conrelid = 'public.oasis_customers'::regclass
    ) then
        alter table public.oasis_customers
            add constraint oasis_customers_merged_into_customer_id_not_self_check
            check (
                merged_into_customer_id is null
                or merged_into_customer_id <> id
            );
    end if;
end;
$$;

create index if not exists idx_oasis_customers_merged_into_customer_id
    on public.oasis_customers (merged_into_customer_id)
    where merged_into_customer_id is not null;

create index if not exists idx_oasis_customers_lifecycle_archive
    on public.oasis_customers (lifecycle_status, archived_at)
    where archived_at is not null;

-- Existing audio rows and files remain untouched. The default makes legacy
-- rows readable as active without an UPDATE/backfill statement.
alter table if exists public.oasis_consultation_audio
    add column if not exists status text default 'active',
    add column if not exists archived_at timestamptz,
    add column if not exists archived_by text,
    add column if not exists archive_reason text;

do $$
begin
    if to_regclass('public.oasis_consultation_audio') is not null then
        execute $index$
            create index if not exists idx_oasis_consultation_audio_archive_status
            on public.oasis_consultation_audio (status, archived_at)
            where archived_at is not null
        $index$;
    end if;
end;
$$;

-- ---------------------------------------------------------------------------
-- 2. Durable outbox and customer-asset metadata
-- ---------------------------------------------------------------------------

create table if not exists public.oasis_sync_outbox (
    id uuid primary key default gen_random_uuid(),
    owner_user_id text not null,
    job_type text not null,
    entity_type text not null,
    entity_id text,
    payload jsonb not null default '{}'::jsonb,
    idempotency_key text not null,
    status text not null default 'pending',
    attempt_count integer not null default 0,
    total_attempt_count bigint not null default 0,
    max_attempts integer not null default 8,
    next_retry_at timestamptz not null default now(),
    leased_by text,
    lease_token uuid,
    lease_expires_at timestamptz,
    last_error_code text,
    last_error_summary text,
    manual_retry_count integer not null default 0,
    last_manual_retry_by text,
    last_manual_retry_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    completed_at timestamptz,
    constraint oasis_sync_outbox_id_owner_user_id_unique
        unique (id, owner_user_id),
    constraint oasis_sync_outbox_owner_idempotency_key_unique
        unique (owner_user_id, idempotency_key),
    constraint oasis_sync_outbox_status_check
        check (status in ('pending', 'processing', 'retry', 'completed', 'dead_letter')),
    constraint oasis_sync_outbox_attempt_count_check
        check (attempt_count >= 0),
    constraint oasis_sync_outbox_total_attempt_count_check
        check (total_attempt_count >= 0),
    constraint oasis_sync_outbox_max_attempts_check
        check (max_attempts between 1 and 100),
    constraint oasis_sync_outbox_manual_retry_count_check
        check (manual_retry_count >= 0),
    constraint oasis_sync_outbox_payload_object_check
        check (jsonb_typeof(payload) = 'object'),
    constraint oasis_sync_outbox_idempotency_key_length_check
        check (char_length(idempotency_key) between 1 and 200),
    constraint oasis_sync_outbox_job_type_length_check
        check (char_length(job_type) between 1 and 100),
    constraint oasis_sync_outbox_entity_type_length_check
        check (char_length(entity_type) between 1 and 100),
    constraint oasis_sync_outbox_error_code_length_check
        check (last_error_code is null or char_length(last_error_code) <= 100),
    constraint oasis_sync_outbox_error_summary_length_check
        check (last_error_summary is null or char_length(last_error_summary) <= 500)
);

create index if not exists idx_oasis_sync_outbox_claimable
    on public.oasis_sync_outbox (next_retry_at, created_at, id)
    where status in ('pending', 'retry');

create index if not exists idx_oasis_sync_outbox_expired_lease
    on public.oasis_sync_outbox (lease_expires_at, id)
    where status = 'processing';

create index if not exists idx_oasis_sync_outbox_owner_status_created
    on public.oasis_sync_outbox (owner_user_id, status, created_at desc);

create index if not exists idx_oasis_sync_outbox_entity
    on public.oasis_sync_outbox (entity_type, entity_id)
    where entity_id is not null;

create table if not exists public.oasis_sync_outbox_events (
    id uuid primary key default gen_random_uuid(),
    job_id uuid not null,
    owner_user_id text not null,
    event_type text not null,
    worker_id text,
    lease_token uuid,
    attempt_count integer not null default 0,
    total_attempt_count bigint not null default 0,
    error_code text,
    error_summary text,
    event_data jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint oasis_sync_outbox_events_job_id_fkey
        foreign key (job_id, owner_user_id)
        references public.oasis_sync_outbox(id, owner_user_id),
    constraint oasis_sync_outbox_events_event_type_check
        check (event_type in ('claim', 'complete', 'fail', 'manual_retry')),
    constraint oasis_sync_outbox_events_attempt_count_check
        check (attempt_count >= 0),
    constraint oasis_sync_outbox_events_total_attempt_count_check
        check (total_attempt_count >= 0),
    constraint oasis_sync_outbox_events_error_code_length_check
        check (error_code is null or char_length(error_code) <= 100),
    constraint oasis_sync_outbox_events_error_summary_length_check
        check (error_summary is null or char_length(error_summary) <= 500),
    constraint oasis_sync_outbox_events_data_object_check
        check (jsonb_typeof(event_data) = 'object')
);

create index if not exists idx_oasis_sync_outbox_events_job_created
    on public.oasis_sync_outbox_events (job_id, created_at, id);

create index if not exists idx_oasis_sync_outbox_events_owner_created
    on public.oasis_sync_outbox_events (owner_user_id, created_at desc, id);

create table if not exists public.oasis_customer_assets (
    id uuid primary key default gen_random_uuid(),
    owner_user_id text not null,
    customer_id uuid,
    asset_type text not null,
    storage_bucket text not null default 'oasis-customer-assets',
    storage_path text not null,
    original_filename text,
    content_type text,
    size_bytes bigint not null default 0,
    sha256 text,
    status text not null default 'active',
    duplicate_of_asset_id uuid,
    source_type text,
    source_id text,
    archived_at timestamptz,
    archived_by_user_id text,
    archive_reason text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint oasis_customer_assets_id_owner_user_id_unique
        unique (id, owner_user_id),
    constraint oasis_customer_assets_customer_id_fkey
        foreign key (customer_id, owner_user_id)
        references public.oasis_customers(id, owner_user_id),
    constraint oasis_customer_assets_duplicate_of_asset_id_fkey
        foreign key (duplicate_of_asset_id, owner_user_id)
        references public.oasis_customer_assets(id, owner_user_id),
    constraint oasis_customer_assets_storage_object_unique
        unique (storage_bucket, storage_path),
    constraint oasis_customer_assets_status_check
        check (status in ('active', 'archived', 'duplicate')),
    constraint oasis_customer_assets_size_bytes_check
        check (size_bytes >= 0)
);

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'oasis_customer_assets_duplicate_of_asset_id_not_self_check'
          and conrelid = 'public.oasis_customer_assets'::regclass
    ) then
        alter table public.oasis_customer_assets
            add constraint oasis_customer_assets_duplicate_of_asset_id_not_self_check
            check (
                duplicate_of_asset_id is null
                or duplicate_of_asset_id <> id
            );
    end if;
end;
$$;

create index if not exists idx_oasis_customer_assets_customer_id
    on public.oasis_customer_assets (customer_id);

create index if not exists idx_oasis_customer_assets_duplicate_of_asset_id
    on public.oasis_customer_assets (duplicate_of_asset_id)
    where duplicate_of_asset_id is not null;

create index if not exists idx_oasis_customer_assets_owner_status_created
    on public.oasis_customer_assets (owner_user_id, status, created_at desc);

create index if not exists idx_oasis_customer_assets_sha256
    on public.oasis_customer_assets (sha256)
    where sha256 is not null;

-- One private Storage object may legitimately be associated with multiple
-- customers or sources.  Keep the object metadata single-row and preserve
-- every logical association in this owner-scoped link table.
create table if not exists public.oasis_customer_asset_links (
    id uuid primary key default gen_random_uuid(),
    owner_user_id text not null,
    asset_id uuid not null,
    customer_id uuid,
    association_key text not null,
    source_type text,
    source_id text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint oasis_customer_asset_links_identity_unique
        unique (owner_user_id, asset_id, association_key),
    constraint oasis_customer_asset_links_asset_id_fkey
        foreign key (asset_id, owner_user_id)
        references public.oasis_customer_assets(id, owner_user_id),
    constraint oasis_customer_asset_links_customer_id_fkey
        foreign key (customer_id, owner_user_id)
        references public.oasis_customers(id, owner_user_id),
    constraint oasis_customer_asset_links_association_key_length_check
        check (char_length(association_key) between 1 and 64)
);

create index if not exists idx_oasis_customer_asset_links_asset_id
    on public.oasis_customer_asset_links (asset_id);

create index if not exists idx_oasis_customer_asset_links_customer_id
    on public.oasis_customer_asset_links (customer_id)
    where customer_id is not null;

create index if not exists idx_oasis_customer_asset_links_owner_created
    on public.oasis_customer_asset_links (owner_user_id, created_at desc);

-- Supabase owns storage.*. Only register the private bucket metadata here.
-- Existing MIME/size restrictions, if any, are preserved.
insert into storage.buckets (id, name, public)
values ('oasis-customer-assets', 'oasis-customer-assets', false)
on conflict (id) do update
set public = false;

-- ---------------------------------------------------------------------------
-- 3. Durable AI copilot organizational knowledge
-- ---------------------------------------------------------------------------

-- Compatibility master used by the v9.9.0 application adapter. The three
-- typed tables below remain available for later normalized workflows.
create table if not exists public.oasis_copilot_assets (
    id uuid primary key default gen_random_uuid(),
    owner_user_id text not null,
    asset_type text not null,
    asset_key text not null,
    payload jsonb not null default '{}'::jsonb,
    source_updated_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint oasis_copilot_assets_identity_unique
        unique (owner_user_id, asset_type, asset_key),
    constraint oasis_copilot_assets_type_check
        check (asset_type in ('memory', 'success_case', 'checklist')),
    constraint oasis_copilot_assets_payload_object_check
        check (jsonb_typeof(payload) = 'object')
);

create index if not exists idx_oasis_copilot_assets_owner_type_updated
    on public.oasis_copilot_assets (
        owner_user_id,
        asset_type,
        source_updated_at desc nulls last
    );

create table if not exists public.oasis_copilot_company_memory (
    id uuid primary key default gen_random_uuid(),
    owner_user_id text not null,
    customer_id uuid,
    company_ref text not null,
    memory_key text not null,
    memory_data jsonb not null default '{}'::jsonb,
    source_type text,
    source_id text,
    status text not null default 'active',
    archived_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint oasis_copilot_company_memory_customer_id_fkey
        foreign key (customer_id, owner_user_id)
        references public.oasis_customers(id, owner_user_id),
    constraint oasis_copilot_company_memory_identity_unique
        unique (owner_user_id, company_ref, memory_key),
    constraint oasis_copilot_company_memory_status_check
        check (status in ('active', 'archived')),
    constraint oasis_copilot_company_memory_data_object_check
        check (jsonb_typeof(memory_data) = 'object')
);

create index if not exists idx_oasis_copilot_company_memory_customer_id
    on public.oasis_copilot_company_memory (customer_id);

create index if not exists idx_oasis_copilot_company_memory_owner_status
    on public.oasis_copilot_company_memory (owner_user_id, status, updated_at desc);

create table if not exists public.oasis_copilot_success_cases (
    id uuid primary key default gen_random_uuid(),
    owner_user_id text not null,
    customer_id uuid,
    company_ref text not null,
    title text not null,
    case_data jsonb not null default '{}'::jsonb,
    idempotency_key text not null,
    status text not null default 'active',
    occurred_at timestamptz,
    archived_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint oasis_copilot_success_cases_customer_id_fkey
        foreign key (customer_id, owner_user_id)
        references public.oasis_customers(id, owner_user_id),
    constraint oasis_copilot_success_cases_idempotency_unique
        unique (owner_user_id, idempotency_key),
    constraint oasis_copilot_success_cases_status_check
        check (status in ('active', 'archived')),
    constraint oasis_copilot_success_cases_data_object_check
        check (jsonb_typeof(case_data) = 'object')
);

create index if not exists idx_oasis_copilot_success_cases_customer_id
    on public.oasis_copilot_success_cases (customer_id);

create index if not exists idx_oasis_copilot_success_cases_owner_status
    on public.oasis_copilot_success_cases (owner_user_id, status, created_at desc);

create table if not exists public.oasis_copilot_checklists (
    id uuid primary key default gen_random_uuid(),
    owner_user_id text not null,
    customer_id uuid,
    company_ref text not null,
    checklist_key text not null,
    checklist_data jsonb not null default '{}'::jsonb,
    status text not null default 'active',
    completed_at timestamptz,
    archived_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint oasis_copilot_checklists_customer_id_fkey
        foreign key (customer_id, owner_user_id)
        references public.oasis_customers(id, owner_user_id),
    constraint oasis_copilot_checklists_identity_unique
        unique (owner_user_id, company_ref, checklist_key),
    constraint oasis_copilot_checklists_status_check
        check (status in ('active', 'completed', 'archived')),
    constraint oasis_copilot_checklists_data_object_check
        check (jsonb_typeof(checklist_data) = 'object')
);

create index if not exists idx_oasis_copilot_checklists_customer_id
    on public.oasis_copilot_checklists (customer_id);

create index if not exists idx_oasis_copilot_checklists_owner_status
    on public.oasis_copilot_checklists (owner_user_id, status, updated_at desc);

-- ---------------------------------------------------------------------------
-- 4. Backup/restore evidence and customer archive history
-- ---------------------------------------------------------------------------

create table if not exists public.oasis_backup_runs (
    id uuid primary key default gen_random_uuid(),
    idempotency_key text not null unique,
    backup_type text not null,
    status text not null default 'pending',
    storage_bucket text,
    storage_path text,
    checksum_sha256 text,
    size_bytes bigint,
    record_counts jsonb not null default '{}'::jsonb,
    retention_until timestamptz,
    started_at timestamptz,
    completed_at timestamptz,
    initiated_by_user_id text,
    error_code text,
    error_summary text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint oasis_backup_runs_status_check
        check (status in ('pending', 'running', 'completed', 'failed')),
    constraint oasis_backup_runs_size_bytes_check
        check (size_bytes is null or size_bytes >= 0),
    constraint oasis_backup_runs_record_counts_object_check
        check (jsonb_typeof(record_counts) = 'object'),
    constraint oasis_backup_runs_error_summary_length_check
        check (error_summary is null or char_length(error_summary) <= 500)
);

create index if not exists idx_oasis_backup_runs_status_created
    on public.oasis_backup_runs (status, created_at desc);

create index if not exists idx_oasis_backup_runs_retention
    on public.oasis_backup_runs (retention_until)
    where retention_until is not null;

create table if not exists public.oasis_restore_drills (
    id uuid primary key default gen_random_uuid(),
    backup_run_id uuid not null,
    idempotency_key text not null unique,
    environment_label text not null,
    status text not null default 'pending',
    integrity_verified boolean,
    result_summary jsonb not null default '{}'::jsonb,
    started_at timestamptz,
    completed_at timestamptz,
    conducted_by_user_id text,
    error_code text,
    error_summary text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint oasis_restore_drills_backup_run_id_fkey
        foreign key (backup_run_id) references public.oasis_backup_runs(id),
    constraint oasis_restore_drills_status_check
        check (status in ('pending', 'running', 'completed', 'failed')),
    constraint oasis_restore_drills_result_summary_object_check
        check (jsonb_typeof(result_summary) = 'object'),
    constraint oasis_restore_drills_error_summary_length_check
        check (error_summary is null or char_length(error_summary) <= 500)
);

create index if not exists idx_oasis_restore_drills_backup_run_id
    on public.oasis_restore_drills (backup_run_id);

create index if not exists idx_oasis_restore_drills_status_created
    on public.oasis_restore_drills (status, created_at desc);

create table if not exists public.oasis_customer_archive_events (
    id uuid primary key default gen_random_uuid(),
    customer_id uuid not null,
    owner_user_id text not null,
    actor_user_id text not null,
    action text not null,
    previous_status text,
    new_status text,
    reason text,
    event_data jsonb not null default '{}'::jsonb,
    idempotency_key text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint oasis_customer_archive_events_customer_id_fkey
        foreign key (customer_id, owner_user_id)
        references public.oasis_customers(id, owner_user_id),
    constraint oasis_customer_archive_events_action_check
        check (action in ('archive', 'reactivate', 'merge_link', 'status_change')),
    constraint oasis_customer_archive_events_data_object_check
        check (jsonb_typeof(event_data) = 'object'),
    constraint oasis_customer_archive_events_reason_length_check
        check (reason is null or char_length(reason) <= 1000),
    constraint oasis_customer_archive_events_idempotency_key_length_check
        check (
            idempotency_key is null
            or char_length(idempotency_key) between 1 and 200
        )
);

create unique index if not exists idx_oasis_customer_archive_events_idempotency
    on public.oasis_customer_archive_events (owner_user_id, idempotency_key)
    where idempotency_key is not null;

create index if not exists idx_oasis_customer_archive_events_customer_id
    on public.oasis_customer_archive_events (customer_id);

create index if not exists idx_oasis_customer_archive_events_owner_created
    on public.oasis_customer_archive_events (owner_user_id, created_at desc);

-- Composite foreign keys include owner_user_id to prevent cross-owner links.
-- Keep matching composite indexes so parent-row updates/deletes and joins do
-- not fall back to full-table scans as retained customer history grows.
create index if not exists idx_oasis_customers_merged_into_owner
    on public.oasis_customers (merged_into_customer_id, owner_user_id);

create index if not exists idx_oasis_sync_outbox_events_job_owner
    on public.oasis_sync_outbox_events (job_id, owner_user_id);

create index if not exists idx_oasis_customer_assets_customer_owner
    on public.oasis_customer_assets (customer_id, owner_user_id);

create index if not exists idx_oasis_customer_assets_duplicate_owner
    on public.oasis_customer_assets (duplicate_of_asset_id, owner_user_id);

create index if not exists idx_oasis_customer_asset_links_asset_owner
    on public.oasis_customer_asset_links (asset_id, owner_user_id);

create index if not exists idx_oasis_customer_asset_links_customer_owner
    on public.oasis_customer_asset_links (customer_id, owner_user_id);

create index if not exists idx_oasis_copilot_company_memory_customer_owner
    on public.oasis_copilot_company_memory (customer_id, owner_user_id);

create index if not exists idx_oasis_copilot_success_cases_customer_owner
    on public.oasis_copilot_success_cases (customer_id, owner_user_id);

create index if not exists idx_oasis_copilot_checklists_customer_owner
    on public.oasis_copilot_checklists (customer_id, owner_user_id);

create index if not exists idx_oasis_customer_archive_events_customer_owner
    on public.oasis_customer_archive_events (customer_id, owner_user_id);

-- ---------------------------------------------------------------------------
-- 5. Shared updated_at trigger for objects introduced in this migration
-- ---------------------------------------------------------------------------

create or replace function public.oasis_v990_touch_updated_at()
returns trigger
language plpgsql
set search_path = public, pg_temp
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

do $$
declare
    v_table text;
    v_trigger text;
begin
    foreach v_table in array array[
        'oasis_sync_outbox',
        'oasis_sync_outbox_events',
        'oasis_customer_assets',
        'oasis_customer_asset_links',
        'oasis_copilot_assets',
        'oasis_copilot_company_memory',
        'oasis_copilot_success_cases',
        'oasis_copilot_checklists',
        'oasis_backup_runs',
        'oasis_restore_drills',
        'oasis_customer_archive_events'
    ] loop
        v_trigger := 'trg_' || v_table || '_updated_at';
        if not exists (
            select 1
            from pg_trigger t
            join pg_class c on c.oid = t.tgrelid
            join pg_namespace n on n.oid = c.relnamespace
            where n.nspname = 'public'
              and c.relname = v_table
              and t.tgname = v_trigger
              and not t.tgisinternal
        ) then
            execute format(
                'create trigger %I before update on public.%I '
                'for each row execute function public.oasis_v990_touch_updated_at()',
                v_trigger,
                v_table
            );
        end if;
    end loop;
end;
$$;

-- ---------------------------------------------------------------------------
-- 6. Durable outbox RPCs (service_role only)
-- ---------------------------------------------------------------------------

create or replace function public.oasis_v990_safe_error_summary(p_value text)
returns text
language plpgsql
immutable
set search_path = public, pg_temp
as $$
declare
    v_value text := coalesce(p_value, '');
begin
    v_value := regexp_replace(v_value, '[[:cntrl:]]+', ' ', 'g');
    v_value := regexp_replace(
        v_value,
        '(?i)(^|[^[:alnum:]_])(authorization|api[_-]?key|token|secret|password)([[:space:]"'']*[:=][[:space:]"'']*|[[:space:]]+)[^,;|}]+',
        '\1\2=[REDACTED]',
        'g'
    );
    v_value := regexp_replace(
        v_value,
        '(?i)(^|[^[:alnum:]_])(customer[_-]?name|company[_-]?name|name|address|email|phone|cellphone|mobile)[[:space:]"'']*[:=][[:space:]"'']*[^,;|}]+',
        '\1\2=[REDACTED]',
        'g'
    );
    v_value := regexp_replace(v_value, '[0-9]{6}[- ]?[1-8][0-9]{6}', '[REDACTED_ID]', 'g');
    v_value := regexp_replace(
        v_value,
        '([+]82[-. ]?)?0?(1[016789]|2|[3-6][1-5]|70|80|50[2-8])[-. ]?[0-9]{3,4}[-. ]?[0-9]{4}',
        '[REDACTED_PHONE]',
        'g'
    );
    v_value := regexp_replace(v_value, '[0-9]{3}[- ][0-9]{2}[- ][0-9]{5}', '[REDACTED_BUSINESS_NO]', 'g');
    v_value := regexp_replace(v_value, '(^|[^0-9])[0-9]{10}([^0-9]|$)', '\1[REDACTED_NUMERIC_ID]\2', 'g');
    v_value := regexp_replace(
        v_value,
        '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+[.][A-Za-z]{2,}',
        '[REDACTED_EMAIL]',
        'g'
    );
    v_value := regexp_replace(v_value, '[A-Za-z]:[\\/][^[:space:]]+', '[REDACTED_PATH]', 'g');
    v_value := regexp_replace(
        v_value,
        '(^|[[:space:]])/([^/[:space:]]+/)+[^[:space:]]*',
        '\1[REDACTED_PATH]',
        'g'
    );
    return nullif(left(btrim(v_value), 500), '');
end;
$$;

create or replace function public.oasis_enqueue_sync_outbox(
    p_owner_user_id text,
    p_job_type text,
    p_entity_type text,
    p_entity_id text,
    p_payload jsonb,
    p_idempotency_key text,
    p_max_attempts integer default 8
)
returns public.oasis_sync_outbox
language plpgsql
volatile
security definer
set search_path = public, pg_temp
as $$
declare
    v_job public.oasis_sync_outbox%rowtype;
begin
    if nullif(btrim(p_owner_user_id), '') is null
       or nullif(btrim(p_job_type), '') is null
       or nullif(btrim(p_entity_type), '') is null
       or nullif(btrim(p_idempotency_key), '') is null then
        raise exception using errcode = '22023', message = 'OASIS_OUTBOX_INVALID_ARGUMENT';
    end if;

    if char_length(btrim(p_idempotency_key)) > 200
       or char_length(btrim(p_job_type)) > 100
       or char_length(btrim(p_entity_type)) > 100
       or (p_payload is not null and jsonb_typeof(p_payload) <> 'object') then
        raise exception using errcode = '22023', message = 'OASIS_OUTBOX_INVALID_ARGUMENT';
    end if;

    insert into public.oasis_sync_outbox (
        owner_user_id,
        job_type,
        entity_type,
        entity_id,
        payload,
        idempotency_key,
        max_attempts
    ) values (
        btrim(p_owner_user_id),
        btrim(p_job_type),
        btrim(p_entity_type),
        nullif(btrim(p_entity_id), ''),
        coalesce(p_payload, '{}'::jsonb),
        btrim(p_idempotency_key),
        greatest(1, least(coalesce(p_max_attempts, 8), 100))
    )
    on conflict (owner_user_id, idempotency_key) do nothing;

    select q.*
    into v_job
    from public.oasis_sync_outbox q
    where q.owner_user_id = btrim(p_owner_user_id)
      and q.idempotency_key = btrim(p_idempotency_key);

    if not found then
        raise exception using errcode = 'P0001', message = 'OASIS_OUTBOX_ENQUEUE_FAILED';
    end if;

    -- Reusing an idempotency key is only safe when it represents the exact
    -- same logical request.  Silently returning an older, different job can
    -- make the caller report a successful cloud save for work that was never
    -- queued.
    if v_job.job_type is distinct from btrim(p_job_type)
       or v_job.entity_type is distinct from btrim(p_entity_type)
       or v_job.entity_id is distinct from nullif(btrim(p_entity_id), '')
       or v_job.payload is distinct from coalesce(p_payload, '{}'::jsonb)
       or v_job.max_attempts is distinct from greatest(1, least(coalesce(p_max_attempts, 8), 100)) then
        raise exception using
            errcode = '23505',
            message = 'OASIS_OUTBOX_IDEMPOTENCY_CONFLICT';
    end if;

    return v_job;
end;
$$;

create or replace function public.oasis_claim_sync_outbox(
    p_owner_user_id text,
    p_worker_id text,
    p_limit integer default 25,
    p_lease_seconds integer default 300
)
returns setof public.oasis_sync_outbox
language plpgsql
volatile
security definer
set search_path = public, pg_temp
as $$
begin
    if nullif(btrim(p_owner_user_id), '') is null then
        raise exception using errcode = '22023', message = 'OASIS_OUTBOX_OWNER_REQUIRED';
    end if;

    if nullif(btrim(p_worker_id), '') is null then
        raise exception using errcode = '22023', message = 'OASIS_OUTBOX_WORKER_REQUIRED';
    end if;

    -- Exhausted or abandoned work is retained as dead-letter evidence.
    update public.oasis_sync_outbox q
    set
        status = 'dead_letter',
        leased_by = null,
        lease_token = null,
        lease_expires_at = null,
        updated_at = now()
    where q.attempt_count >= q.max_attempts
      and q.owner_user_id = btrim(p_owner_user_id)
      and (
          q.status in ('pending', 'retry')
          or (
              q.status = 'processing'
              and coalesce(q.lease_expires_at, q.updated_at) <= now()
          )
      );

    return query
    with candidates as (
        select q.id
        from public.oasis_sync_outbox q
        where q.owner_user_id = btrim(p_owner_user_id)
          and q.attempt_count < q.max_attempts
          and (
              (q.status in ('pending', 'retry') and q.next_retry_at <= now())
              or (
                  q.status = 'processing'
                  and coalesce(q.lease_expires_at, q.updated_at) <= now()
              )
          )
        order by q.next_retry_at, q.created_at, q.id
        limit greatest(1, least(coalesce(p_limit, 25), 100))
        for update skip locked
    ), claimed as (
        update public.oasis_sync_outbox q
        set
            status = 'processing',
            attempt_count = q.attempt_count + 1,
            total_attempt_count = q.total_attempt_count + 1,
            leased_by = btrim(p_worker_id),
            lease_token = gen_random_uuid(),
            lease_expires_at = now() + make_interval(
                secs => greatest(30, least(coalesce(p_lease_seconds, 300), 3600))
            ),
            updated_at = now()
        from candidates c
        where q.id = c.id
        returning q.*
    ), recorded as (
        insert into public.oasis_sync_outbox_events (
            job_id,
            owner_user_id,
            event_type,
            worker_id,
            lease_token,
            attempt_count,
            total_attempt_count,
            event_data
        )
        select
            c.id,
            c.owner_user_id,
            'claim',
            c.leased_by,
            c.lease_token,
            c.attempt_count,
            c.total_attempt_count,
            jsonb_build_object('lease_expires_at', c.lease_expires_at)
        from claimed c
        returning job_id
    )
    select c.*
    from claimed c
    join recorded r on r.job_id = c.id
    order by c.next_retry_at, c.created_at, c.id;
end;
$$;

create or replace function public.oasis_complete_sync_outbox(
    p_job_id uuid,
    p_worker_id text,
    p_lease_token uuid
)
returns boolean
language plpgsql
volatile
security definer
set search_path = public, pg_temp
as $$
declare
    v_changed integer;
begin
    if p_job_id is null
       or nullif(btrim(p_worker_id), '') is null
       or p_lease_token is null then
        return false;
    end if;

    with changed as (
        update public.oasis_sync_outbox q
        set
            status = 'completed',
            completed_at = now(),
            leased_by = null,
            lease_token = null,
            lease_expires_at = null,
            last_error_code = null,
            last_error_summary = null,
            updated_at = now()
        where q.id = p_job_id
          and q.status = 'processing'
          and q.leased_by = btrim(p_worker_id)
          and q.lease_token = p_lease_token
        returning q.*
    )
    insert into public.oasis_sync_outbox_events (
        job_id,
        owner_user_id,
        event_type,
        worker_id,
        lease_token,
        attempt_count,
        total_attempt_count,
        event_data
    )
    select
        c.id,
        c.owner_user_id,
        'complete',
        btrim(p_worker_id),
        p_lease_token,
        c.attempt_count,
        c.total_attempt_count,
        jsonb_build_object('status', c.status)
    from changed c;

    get diagnostics v_changed = row_count;
    return v_changed = 1;
end;
$$;

create or replace function public.oasis_fail_sync_outbox(
    p_job_id uuid,
    p_worker_id text,
    p_lease_token uuid,
    p_error_code text,
    p_error_summary text
)
returns boolean
language plpgsql
volatile
security definer
set search_path = public, pg_temp
as $$
declare
    v_changed integer;
begin
    if p_job_id is null
       or nullif(btrim(p_worker_id), '') is null
       or p_lease_token is null then
        return false;
    end if;

    with changed as (
        update public.oasis_sync_outbox q
        set
            status = case
                when q.attempt_count >= q.max_attempts then 'dead_letter'
                else 'retry'
            end,
            next_retry_at = case
                when q.attempt_count >= q.max_attempts then q.next_retry_at
                else now() + make_interval(
                    secs => least(
                        86400,
                        (30 * power(2.0, least(q.attempt_count, 10)))::integer
                    )
                )
            end,
            leased_by = null,
            lease_token = null,
            lease_expires_at = null,
            last_error_code = nullif(left(btrim(coalesce(p_error_code, '')), 100), ''),
            last_error_summary = public.oasis_v990_safe_error_summary(p_error_summary),
            updated_at = now()
        where q.id = p_job_id
          and q.status = 'processing'
          and q.leased_by = btrim(p_worker_id)
          and q.lease_token = p_lease_token
        returning q.*
    )
    insert into public.oasis_sync_outbox_events (
        job_id,
        owner_user_id,
        event_type,
        worker_id,
        lease_token,
        attempt_count,
        total_attempt_count,
        error_code,
        error_summary,
        event_data
    )
    select
        c.id,
        c.owner_user_id,
        'fail',
        btrim(p_worker_id),
        p_lease_token,
        c.attempt_count,
        c.total_attempt_count,
        c.last_error_code,
        c.last_error_summary,
        jsonb_build_object(
            'status', c.status,
            'next_retry_at', c.next_retry_at
        )
    from changed c;

    get diagnostics v_changed = row_count;
    return v_changed = 1;
end;
$$;

create or replace function public.oasis_retry_sync_outbox(
    p_job_id uuid,
    p_actor_user_id text
)
returns boolean
language plpgsql
volatile
security definer
set search_path = public, pg_temp
as $$
declare
    v_changed integer;
begin
    if p_job_id is null or nullif(btrim(p_actor_user_id), '') is null then
        return false;
    end if;

    with target as (
        select q.id, q.status as previous_status
        from public.oasis_sync_outbox q
        where q.id = p_job_id
          and q.status in ('retry', 'dead_letter')
        for update
    ), changed as (
        update public.oasis_sync_outbox q
        set
            status = 'pending',
            attempt_count = 0,
            next_retry_at = now(),
            leased_by = null,
            lease_token = null,
            lease_expires_at = null,
            manual_retry_count = q.manual_retry_count + 1,
            last_manual_retry_by = btrim(p_actor_user_id),
            last_manual_retry_at = now(),
            completed_at = null,
            updated_at = now()
        from target t
        where q.id = t.id
        returning q.*
    )
    insert into public.oasis_sync_outbox_events (
        job_id,
        owner_user_id,
        event_type,
        attempt_count,
        total_attempt_count,
        event_data
    )
    select
        c.id,
        c.owner_user_id,
        'manual_retry',
        c.attempt_count,
        c.total_attempt_count,
        jsonb_build_object(
            'actor_user_id', btrim(p_actor_user_id),
            'previous_status', t.previous_status,
            'status', c.status,
            'manual_retry_count', c.manual_retry_count
        )
    from changed c
    join target t on t.id = c.id;

    get diagnostics v_changed = row_count;
    return v_changed = 1;
end;
$$;

-- Customer information is archived non-destructively. Both transitions lock
-- the customer row, write the customer state and append the audit event in one
-- transaction. Browser roles cannot execute these service-only functions.
create or replace function public.oasis_archive_customer(
    p_customer_id uuid,
    p_owner_user_id text,
    p_actor_user_id text,
    p_reason text,
    p_idempotency_key text
)
returns boolean
language plpgsql
volatile
security definer
set search_path = public, pg_temp
as $$
declare
    v_previous_status text;
    v_existing_customer_id uuid;
    v_existing_action text;
    v_existing_actor_user_id text;
    v_existing_reason text;
    v_owner_user_id text := btrim(p_owner_user_id);
    v_actor_user_id text := btrim(p_actor_user_id);
    v_reason text := left(btrim(p_reason), 1000);
    v_idempotency_key text := nullif(btrim(p_idempotency_key), '');
    v_state_changed boolean := false;
begin
    if p_customer_id is null
       or nullif(v_owner_user_id, '') is null
       or nullif(v_actor_user_id, '') is null
       or nullif(v_reason, '') is null
       or char_length(btrim(p_reason)) > 1000
       or (
           p_idempotency_key is not null
           and (
               v_idempotency_key is null
               or char_length(v_idempotency_key) not between 1 and 200
           )
       ) then
        return false;
    end if;

    if v_idempotency_key is not null then
        -- Serialize requests that share the same owner/idempotency key before
        -- inspecting the audit event. This closes the race where two sessions
        -- both observe no event and the loser later returns from the already
        -- archived state without validating the original request payload.
        perform pg_catalog.pg_advisory_xact_lock(
            pg_catalog.hashtextextended(
                'oasis:customer-lifecycle:'
                || char_length(v_owner_user_id)::text
                || ':'
                || v_owner_user_id
                || ':'
                || v_idempotency_key,
                0
            )
        );

        select e.customer_id, e.action, e.actor_user_id, e.reason
        into
            v_existing_customer_id,
            v_existing_action,
            v_existing_actor_user_id,
            v_existing_reason
        from public.oasis_customer_archive_events e
        where e.owner_user_id = v_owner_user_id
          and e.idempotency_key = v_idempotency_key;

        if found then
            if v_existing_customer_id = p_customer_id
               and v_existing_action = 'archive'
               and v_existing_actor_user_id = v_actor_user_id
               and v_existing_reason = v_reason then
                return true;
            end if;
            raise exception using
                errcode = '23505',
                message = 'OASIS_CUSTOMER_ARCHIVE_IDEMPOTENCY_CONFLICT';
        end if;
    end if;

    select c.lifecycle_status
    into v_previous_status
    from public.oasis_customers c
    where c.id = p_customer_id
      and c.owner_user_id = v_owner_user_id
    for update;

    if not found then
        return false;
    end if;

    if v_previous_status = 'archived' and v_idempotency_key is null then
        return true;
    end if;

    if v_previous_status is distinct from 'archived' then
        update public.oasis_customers c
        set
            lifecycle_status = 'archived',
            archived_at = now(),
            archived_by_user_id = v_actor_user_id,
            archive_reason = v_reason
        where c.id = p_customer_id
          and c.owner_user_id = v_owner_user_id;
        v_state_changed := true;
    end if;

    insert into public.oasis_customer_archive_events (
        customer_id,
        owner_user_id,
        actor_user_id,
        action,
        previous_status,
        new_status,
        reason,
        event_data,
        idempotency_key
    ) values (
        p_customer_id,
        v_owner_user_id,
        v_actor_user_id,
        'archive',
        v_previous_status,
        'archived',
        v_reason,
        jsonb_build_object(
            'retention_mode', 'non_destructive',
            'state_changed', v_state_changed
        ),
        v_idempotency_key
    );

    return true;
end;
$$;

create or replace function public.oasis_reactivate_customer(
    p_customer_id uuid,
    p_owner_user_id text,
    p_actor_user_id text,
    p_reason text,
    p_idempotency_key text
)
returns boolean
language plpgsql
volatile
security definer
set search_path = public, pg_temp
as $$
declare
    v_previous_status text;
    v_existing_customer_id uuid;
    v_existing_action text;
    v_existing_actor_user_id text;
    v_existing_reason text;
    v_owner_user_id text := btrim(p_owner_user_id);
    v_actor_user_id text := btrim(p_actor_user_id);
    v_reason text := left(btrim(p_reason), 1000);
    v_idempotency_key text := nullif(btrim(p_idempotency_key), '');
    v_state_changed boolean := false;
begin
    if p_customer_id is null
       or nullif(v_owner_user_id, '') is null
       or nullif(v_actor_user_id, '') is null
       or nullif(v_reason, '') is null
       or char_length(btrim(p_reason)) > 1000
       or (
           p_idempotency_key is not null
           and (
               v_idempotency_key is null
               or char_length(v_idempotency_key) not between 1 and 200
           )
       ) then
        return false;
    end if;

    if v_idempotency_key is not null then
        -- Archive and reactivate share one idempotency namespace because the
        -- audit table enforces (owner_user_id, idempotency_key) uniqueness.
        perform pg_catalog.pg_advisory_xact_lock(
            pg_catalog.hashtextextended(
                'oasis:customer-lifecycle:'
                || char_length(v_owner_user_id)::text
                || ':'
                || v_owner_user_id
                || ':'
                || v_idempotency_key,
                0
            )
        );

        select e.customer_id, e.action, e.actor_user_id, e.reason
        into
            v_existing_customer_id,
            v_existing_action,
            v_existing_actor_user_id,
            v_existing_reason
        from public.oasis_customer_archive_events e
        where e.owner_user_id = v_owner_user_id
          and e.idempotency_key = v_idempotency_key;

        if found then
            if v_existing_customer_id = p_customer_id
               and v_existing_action = 'reactivate'
               and v_existing_actor_user_id = v_actor_user_id
               and v_existing_reason = v_reason then
                return true;
            end if;
            raise exception using
                errcode = '23505',
                message = 'OASIS_CUSTOMER_REACTIVATE_IDEMPOTENCY_CONFLICT';
        end if;
    end if;

    select c.lifecycle_status
    into v_previous_status
    from public.oasis_customers c
    where c.id = p_customer_id
      and c.owner_user_id = v_owner_user_id
    for update;

    if not found then
        return false;
    end if;

    if coalesce(v_previous_status, 'active') = 'active'
       and v_idempotency_key is null then
        return true;
    end if;

    if coalesce(v_previous_status, 'active') is distinct from 'active' then
        update public.oasis_customers c
        set
            lifecycle_status = 'active',
            archived_at = null,
            archived_by_user_id = null,
            archive_reason = null
        where c.id = p_customer_id
          and c.owner_user_id = v_owner_user_id;
        v_state_changed := true;
    end if;

    insert into public.oasis_customer_archive_events (
        customer_id,
        owner_user_id,
        actor_user_id,
        action,
        previous_status,
        new_status,
        reason,
        event_data,
        idempotency_key
    ) values (
        p_customer_id,
        v_owner_user_id,
        v_actor_user_id,
        'reactivate',
        v_previous_status,
        'active',
        v_reason,
        jsonb_build_object(
            'retention_mode', 'non_destructive',
            'state_changed', v_state_changed
        ),
        v_idempotency_key
    );

    return true;
end;
$$;

-- ---------------------------------------------------------------------------
-- 7. RLS lockout and least-privilege grants
-- ---------------------------------------------------------------------------

alter table public.oasis_sync_outbox enable row level security;
alter table public.oasis_sync_outbox force row level security;
alter table public.oasis_sync_outbox_events enable row level security;
alter table public.oasis_sync_outbox_events force row level security;
alter table public.oasis_customer_assets enable row level security;
alter table public.oasis_customer_assets force row level security;
alter table public.oasis_customer_asset_links enable row level security;
alter table public.oasis_customer_asset_links force row level security;
alter table public.oasis_copilot_assets enable row level security;
alter table public.oasis_copilot_assets force row level security;
alter table public.oasis_copilot_company_memory enable row level security;
alter table public.oasis_copilot_company_memory force row level security;
alter table public.oasis_copilot_success_cases enable row level security;
alter table public.oasis_copilot_success_cases force row level security;
alter table public.oasis_copilot_checklists enable row level security;
alter table public.oasis_copilot_checklists force row level security;
alter table public.oasis_backup_runs enable row level security;
alter table public.oasis_backup_runs force row level security;
alter table public.oasis_restore_drills enable row level security;
alter table public.oasis_restore_drills force row level security;
alter table public.oasis_customer_archive_events enable row level security;
alter table public.oasis_customer_archive_events force row level security;

revoke all on table public.oasis_sync_outbox from PUBLIC, anon, authenticated;
revoke all on table public.oasis_sync_outbox_events from PUBLIC, anon, authenticated;
revoke all on table public.oasis_customer_assets from PUBLIC, anon, authenticated;
revoke all on table public.oasis_customer_asset_links from PUBLIC, anon, authenticated;
revoke all on table public.oasis_copilot_assets from PUBLIC, anon, authenticated;
revoke all on table public.oasis_copilot_company_memory from PUBLIC, anon, authenticated;
revoke all on table public.oasis_copilot_success_cases from PUBLIC, anon, authenticated;
revoke all on table public.oasis_copilot_checklists from PUBLIC, anon, authenticated;
revoke all on table public.oasis_backup_runs from PUBLIC, anon, authenticated;
revoke all on table public.oasis_restore_drills from PUBLIC, anon, authenticated;
revoke all on table public.oasis_customer_archive_events from PUBLIC, anon, authenticated;

-- Supabase may grant broad privileges to service_role through pg_default_acl.
-- Reset those inherited defaults before applying the explicit least-privilege
-- matrix below so RPC-only and append-only tables cannot be deleted/truncated.
revoke all on table public.oasis_sync_outbox from service_role;
revoke all on table public.oasis_sync_outbox_events from service_role;
revoke all on table public.oasis_customer_assets from service_role;
revoke all on table public.oasis_customer_asset_links from service_role;
revoke all on table public.oasis_copilot_assets from service_role;
revoke all on table public.oasis_copilot_company_memory from service_role;
revoke all on table public.oasis_copilot_success_cases from service_role;
revoke all on table public.oasis_copilot_checklists from service_role;
revoke all on table public.oasis_backup_runs from service_role;
revoke all on table public.oasis_restore_drills from service_role;
revoke all on table public.oasis_customer_archive_events from service_role;

grant select on table public.oasis_sync_outbox to service_role;
grant select on table public.oasis_sync_outbox_events to service_role;
grant select, insert, update on table public.oasis_customer_assets to service_role;
grant select, insert, update on table public.oasis_customer_asset_links to service_role;
grant select, insert, update on table public.oasis_copilot_assets to service_role;
grant select, insert, update on table public.oasis_copilot_company_memory to service_role;
grant select, insert, update on table public.oasis_copilot_success_cases to service_role;
grant select, insert, update on table public.oasis_copilot_checklists to service_role;
grant select, insert, update on table public.oasis_backup_runs to service_role;
grant select, insert, update on table public.oasis_restore_drills to service_role;
grant select, insert on table public.oasis_customer_archive_events to service_role;

revoke all on function public.oasis_v990_touch_updated_at()
    from PUBLIC, anon, authenticated;
revoke all on function public.oasis_v990_safe_error_summary(text)
    from PUBLIC, anon, authenticated;
revoke all on function public.oasis_enqueue_sync_outbox(text, text, text, text, jsonb, text, integer)
    from PUBLIC, anon, authenticated;
revoke all on function public.oasis_claim_sync_outbox(text, text, integer, integer)
    from PUBLIC, anon, authenticated;
revoke all on function public.oasis_complete_sync_outbox(uuid, text, uuid)
    from PUBLIC, anon, authenticated;
revoke all on function public.oasis_fail_sync_outbox(uuid, text, uuid, text, text)
    from PUBLIC, anon, authenticated;
revoke all on function public.oasis_retry_sync_outbox(uuid, text)
    from PUBLIC, anon, authenticated;
revoke all on function public.oasis_archive_customer(uuid, text, text, text, text)
    from PUBLIC, anon, authenticated;
revoke all on function public.oasis_reactivate_customer(uuid, text, text, text, text)
    from PUBLIC, anon, authenticated;

grant execute on function public.oasis_v990_touch_updated_at()
    to service_role;
grant execute on function public.oasis_v990_safe_error_summary(text)
    to service_role;
grant execute on function public.oasis_enqueue_sync_outbox(text, text, text, text, jsonb, text, integer)
    to service_role;
grant execute on function public.oasis_claim_sync_outbox(text, text, integer, integer)
    to service_role;
grant execute on function public.oasis_complete_sync_outbox(uuid, text, uuid)
    to service_role;
grant execute on function public.oasis_fail_sync_outbox(uuid, text, uuid, text, text)
    to service_role;
grant execute on function public.oasis_retry_sync_outbox(uuid, text)
    to service_role;
grant execute on function public.oasis_archive_customer(uuid, text, text, text, text)
    to service_role;
grant execute on function public.oasis_reactivate_customer(uuid, text, text, text, text)
    to service_role;

comment on table public.oasis_sync_outbox is
    'Durable, idempotent Railway synchronization queue. Failed jobs are retained.';
comment on table public.oasis_sync_outbox_events is
    'Append-only claim, completion, failure, and manual-retry history for durable sync jobs.';
comment on table public.oasis_customer_assets is
    'Private metadata for long-lived customer files stored in Supabase Storage.';
comment on table public.oasis_customer_asset_links is
    'Owner-scoped logical customer/source associations for reusable private Storage objects.';
comment on table public.oasis_copilot_assets is
    'Compatibility master for durable memory, success-case, and checklist assets.';
comment on table public.oasis_copilot_company_memory is
    'Long-lived per-owner AI copilot company memory; browser roles have no direct access.';
comment on table public.oasis_customer_archive_events is
    'Append-only customer archive/reactivation/merge-link history.';

commit;
