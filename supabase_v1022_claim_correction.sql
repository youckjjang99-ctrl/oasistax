-- OASIS CRM v10.2.2 - 경정청구 전용 서버 저장소
-- 고객 브라우저는 이 테이블과 Storage를 직접 호출하지 않는다.
-- service_role은 Railway의 좁은 경정청구 서버/수집 worker에서만 사용한다.

begin;

create table if not exists public.oasis_claim_cases (
    id uuid primary key,
    owner_user_id text not null,
    customer_ref text not null,
    company_name text not null,
    business_no_masked text,
    business_type text not null
        check (business_type in ('individual', 'corporation')),
    representative_name_masked text,
    phone_masked text,
    auth_method text not null
        check (auth_method in ('kakao', 'joint_certificate')),
    hometax_status text not null default 'not_requested',
    comwel_status text not null default 'not_requested',
    overall_status text not null default 'auth_preparing',
    consent_version text not null,
    consent_confirmed_at timestamptz not null,
    consent_text_sha256 text not null,
    consent_channel text not null,
    retention_policy_version text not null,
    collection_authority_confirmed_at timestamptz not null,
    requested_by text not null,
    requested_at timestamptz not null default now(),
    auth_requested_at timestamptz,
    auth_completed_at timestamptz,
    last_safe_error_code text,
    updated_at timestamptz not null default now()
);

alter table public.oasis_claim_cases
    add column if not exists consent_text_sha256 text;
alter table public.oasis_claim_cases
    add column if not exists consent_channel text;
alter table public.oasis_claim_cases
    add column if not exists retention_policy_version text;
alter table public.oasis_claim_cases
    add column if not exists collection_authority_confirmed_at timestamptz;

update public.oasis_claim_cases
set
    consent_text_sha256 = coalesce(
        nullif(consent_text_sha256, ''),
        repeat('0', 64)
    ),
    consent_channel = coalesce(
        nullif(consent_channel, ''),
        'legacy_staff_attestation'
    ),
    retention_policy_version = coalesce(
        nullif(retention_policy_version, ''),
        'legacy'
    ),
    collection_authority_confirmed_at = coalesce(
        collection_authority_confirmed_at,
        consent_confirmed_at,
        requested_at,
        now()
    );

alter table public.oasis_claim_cases
    alter column consent_text_sha256 set not null;
alter table public.oasis_claim_cases
    alter column consent_channel set not null;
alter table public.oasis_claim_cases
    alter column retention_policy_version set not null;
alter table public.oasis_claim_cases
    alter column collection_authority_confirmed_at set not null;

alter table public.oasis_claim_cases
    drop constraint if exists oasis_claim_cases_hometax_status_check;
alter table public.oasis_claim_cases
    add constraint oasis_claim_cases_hometax_status_check
    check (
        hometax_status in (
            'not_requested',
            'request_ready',
            'auth_preparing',
            'auth_requested',
            'auth_pending',
            'auth_complete',
            'auth_partial',
            'certificate_required',
            'collection_queued',
            'collecting',
            'collected',
            'ready',
            'integration_required',
            'failed'
        )
    );

alter table public.oasis_claim_cases
    drop constraint if exists oasis_claim_cases_comwel_status_check;
alter table public.oasis_claim_cases
    add constraint oasis_claim_cases_comwel_status_check
    check (
        comwel_status in (
            'not_requested',
            'request_ready',
            'auth_preparing',
            'auth_requested',
            'auth_pending',
            'auth_complete',
            'auth_partial',
            'certificate_required',
            'collection_queued',
            'collecting',
            'collected',
            'ready',
            'integration_required',
            'failed'
        )
    );

alter table public.oasis_claim_cases
    drop constraint if exists oasis_claim_cases_overall_status_check;
alter table public.oasis_claim_cases
    add constraint oasis_claim_cases_overall_status_check
    check (
        overall_status in (
            'auth_preparing',
            'auth_pending',
            'auth_partial',
            'auth_complete',
            'auth_complete_collection_pending',
            'certificate_required',
            'collection_queued',
            'collecting',
            'collected',
            'ready',
            'integration_required',
            'failed'
        )
    );

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'oasis_claim_cases_id_owner_key'
          and conrelid = 'public.oasis_claim_cases'::regclass
    ) then
        alter table public.oasis_claim_cases
            add constraint oasis_claim_cases_id_owner_key
            unique (id, owner_user_id);
    end if;
end;
$$;

create index if not exists oasis_claim_cases_owner_requested_idx
    on public.oasis_claim_cases (owner_user_id, requested_at desc);
create index if not exists oasis_claim_cases_owner_status_idx
    on public.oasis_claim_cases (owner_user_id, overall_status);
create index if not exists oasis_claim_cases_customer_ref_idx
    on public.oasis_claim_cases (owner_user_id, customer_ref);

create table if not exists public.oasis_claim_documents (
    id uuid primary key,
    owner_user_id text not null,
    case_id uuid not null,
    source text not null check (source in ('hometax', 'comwel')),
    document_code text not null,
    document_name text not null,
    period_year integer,
    status text not null default 'auth_pending',
    facts jsonb not null default '{}'::jsonb,
    storage_bucket text,
    storage_path text,
    content_sha256 text,
    content_type text,
    size_bytes bigint,
    provider_job_ref_ciphertext text,
    retention_until timestamptz,
    collected_at timestamptz,
    deleted_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique nulls not distinct (
        owner_user_id,
        case_id,
        source,
        document_code,
        period_year
    )
);

alter table public.oasis_claim_documents
    drop constraint if exists oasis_claim_documents_status_check;
alter table public.oasis_claim_documents
    add constraint oasis_claim_documents_status_check
    check (
        status in (
            'not_requested',
            'auth_pending',
            'integration_required',
            'collection_queued',
            'collecting',
            'collected',
            'ready',
            'failed'
        )
    );

alter table public.oasis_claim_documents
    drop constraint if exists oasis_claim_documents_case_id_fkey;
alter table public.oasis_claim_documents
    drop constraint if exists oasis_claim_documents_case_owner_fkey;
alter table public.oasis_claim_documents
    add constraint oasis_claim_documents_case_owner_fkey
    foreign key (case_id, owner_user_id)
    references public.oasis_claim_cases(id, owner_user_id)
    on delete cascade;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'oasis_claim_documents_retention_required'
          and conrelid = 'public.oasis_claim_documents'::regclass
    ) then
        alter table public.oasis_claim_documents
            add constraint oasis_claim_documents_retention_required
            check (
                status not in ('collected', 'ready')
                or retention_until is not null
            );
    end if;
end;
$$;

create index if not exists oasis_claim_documents_owner_case_idx
    on public.oasis_claim_documents (owner_user_id, case_id);
create index if not exists oasis_claim_documents_collection_idx
    on public.oasis_claim_documents (
        owner_user_id,
        status,
        source,
        document_code
    );

create table if not exists public.oasis_claim_audit_events (
    id bigint generated always as identity primary key,
    owner_user_id text not null,
    case_id uuid not null,
    action text not null,
    source text not null,
    outcome text not null,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

alter table public.oasis_claim_audit_events
    drop constraint if exists oasis_claim_audit_events_case_id_fkey;
alter table public.oasis_claim_audit_events
    drop constraint if exists oasis_claim_audit_events_case_owner_fkey;
alter table public.oasis_claim_audit_events
    add constraint oasis_claim_audit_events_case_owner_fkey
    foreign key (case_id, owner_user_id)
    references public.oasis_claim_cases(id, owner_user_id)
    on delete restrict;

create index if not exists oasis_claim_audit_owner_case_idx
    on public.oasis_claim_audit_events (
        owner_user_id,
        case_id,
        created_at desc
    );

create or replace function public.set_oasis_claim_updated_at()
returns trigger
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists oasis_claim_cases_updated_at
    on public.oasis_claim_cases;
create trigger oasis_claim_cases_updated_at
before update on public.oasis_claim_cases
for each row execute function public.set_oasis_claim_updated_at();

drop trigger if exists oasis_claim_documents_updated_at
    on public.oasis_claim_documents;
create trigger oasis_claim_documents_updated_at
before update on public.oasis_claim_documents
for each row execute function public.set_oasis_claim_updated_at();

create or replace function public.oasis_create_claim_case(
    p_case jsonb,
    p_documents jsonb,
    p_audit jsonb
)
returns uuid
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_case_id uuid := nullif(p_case ->> 'id', '')::uuid;
    v_owner_user_id text := lower(trim(p_case ->> 'owner_user_id'));
begin
    if v_case_id is null or coalesce(v_owner_user_id, '') = '' then
        raise exception 'invalid claim case identity';
    end if;

    if jsonb_typeof(coalesce(p_documents, '[]'::jsonb)) <> 'array' then
        raise exception 'claim documents must be an array';
    end if;

    if exists (
        select 1
        from jsonb_array_elements(coalesce(p_documents, '[]'::jsonb)) item
        where nullif(item ->> 'case_id', '')::uuid is distinct from v_case_id
           or lower(trim(item ->> 'owner_user_id')) is distinct from v_owner_user_id
    ) then
        raise exception 'claim document ownership mismatch';
    end if;

    insert into public.oasis_claim_cases
    select *
    from jsonb_populate_record(
        null::public.oasis_claim_cases,
        p_case
    );

    if jsonb_array_length(coalesce(p_documents, '[]'::jsonb)) > 0 then
        insert into public.oasis_claim_documents
        select *
        from jsonb_populate_recordset(
            null::public.oasis_claim_documents,
            p_documents
        );
    end if;

    insert into public.oasis_claim_audit_events (
        owner_user_id,
        case_id,
        action,
        source,
        outcome,
        metadata
    )
    values (
        v_owner_user_id,
        v_case_id,
        coalesce(nullif(p_audit ->> 'action', ''), 'case_created'),
        coalesce(nullif(p_audit ->> 'source', ''), 'system'),
        coalesce(nullif(p_audit ->> 'outcome', ''), 'success'),
        coalesce(p_audit -> 'metadata', '{}'::jsonb)
    );

    return v_case_id;
end;
$$;

create or replace function public.oasis_claim_list_cases(
    p_owner_user_id text,
    p_limit integer default 500
)
returns setof public.oasis_claim_cases
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select c.*
    from public.oasis_claim_cases c
    where c.owner_user_id = lower(trim(p_owner_user_id))
    order by c.requested_at desc
    limit greatest(1, least(coalesce(p_limit, 500), 1000));
$$;

create or replace function public.oasis_claim_get_case(
    p_owner_user_id text,
    p_case_id uuid
)
returns setof public.oasis_claim_cases
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select c.*
    from public.oasis_claim_cases c
    where c.owner_user_id = lower(trim(p_owner_user_id))
      and c.id = p_case_id
    limit 1;
$$;

create or replace function public.oasis_claim_list_documents(
    p_owner_user_id text,
    p_case_id uuid
)
returns setof public.oasis_claim_documents
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select d.*
    from public.oasis_claim_documents d
    where d.owner_user_id = lower(trim(p_owner_user_id))
      and d.case_id = p_case_id
    order by d.source asc, d.document_name asc, d.period_year desc nulls last
    limit 500;
$$;

create or replace function public.oasis_claim_append_audit(
    p_owner_user_id text,
    p_case_id uuid,
    p_action text,
    p_source text,
    p_outcome text,
    p_metadata jsonb default '{}'::jsonb
)
returns bigint
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_event_id bigint;
    v_owner_user_id text := lower(trim(p_owner_user_id));
    v_safe_metadata jsonb;
begin
    if not exists (
        select 1
        from public.oasis_claim_cases c
        where c.id = p_case_id
          and c.owner_user_id = v_owner_user_id
    ) then
        raise exception 'claim case not found';
    end if;

    v_safe_metadata := coalesce(p_metadata, '{}'::jsonb)
        - array[
            'identity_number',
            'birth_date',
            'cellphone',
            'token',
            'certificate',
            'password'
        ]::text[];

    insert into public.oasis_claim_audit_events (
        owner_user_id,
        case_id,
        action,
        source,
        outcome,
        metadata
    )
    values (
        v_owner_user_id,
        p_case_id,
        coalesce(nullif(trim(p_action), ''), 'unknown'),
        coalesce(nullif(trim(p_source), ''), 'system'),
        coalesce(nullif(trim(p_outcome), ''), 'unknown'),
        v_safe_metadata
    )
    returning id into v_event_id;

    return v_event_id;
end;
$$;

create or replace function public.oasis_claim_update_case_status(
    p_owner_user_id text,
    p_case_id uuid,
    p_updates jsonb
)
returns setof public.oasis_claim_cases
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_case public.oasis_claim_cases;
    v_owner_user_id text := lower(trim(p_owner_user_id));
    v_updated_fields jsonb;
begin
    if p_updates ? 'hometax_status'
       and not (
           coalesce(p_updates ->> 'hometax_status', '') = any (
               array[
                   'not_requested',
                   'request_ready',
                   'auth_preparing',
                   'auth_requested',
                   'auth_pending',
                   'auth_complete',
                   'auth_partial',
                   'certificate_required',
                   'collection_queued',
                   'collecting',
                   'collected',
                   'ready',
                   'integration_required',
                   'failed'
               ]::text[]
           )
       ) then
        raise exception 'invalid hometax status';
    end if;
    if p_updates ? 'comwel_status'
       and not (
           coalesce(p_updates ->> 'comwel_status', '') = any (
               array[
                   'not_requested',
                   'request_ready',
                   'auth_preparing',
                   'auth_requested',
                   'auth_pending',
                   'auth_complete',
                   'auth_partial',
                   'certificate_required',
                   'collection_queued',
                   'collecting',
                   'collected',
                   'ready',
                   'integration_required',
                   'failed'
               ]::text[]
           )
       ) then
        raise exception 'invalid comwel status';
    end if;
    if p_updates ? 'overall_status'
       and not (
           coalesce(p_updates ->> 'overall_status', '') = any (
               array[
                   'auth_preparing',
                   'auth_pending',
                   'auth_partial',
                   'auth_complete',
                   'auth_complete_collection_pending',
                   'certificate_required',
                   'collection_queued',
                   'collecting',
                   'collected',
                   'ready',
                   'integration_required',
                   'failed'
               ]::text[]
           )
       ) then
        raise exception 'invalid overall status';
    end if;

    update public.oasis_claim_cases c
    set
        hometax_status = case
            when p_updates ? 'hometax_status'
            then coalesce(nullif(p_updates ->> 'hometax_status', ''), c.hometax_status)
            else c.hometax_status
        end,
        comwel_status = case
            when p_updates ? 'comwel_status'
            then coalesce(nullif(p_updates ->> 'comwel_status', ''), c.comwel_status)
            else c.comwel_status
        end,
        overall_status = case
            when p_updates ? 'overall_status'
            then coalesce(nullif(p_updates ->> 'overall_status', ''), c.overall_status)
            else c.overall_status
        end,
        auth_requested_at = case
            when p_updates ? 'auth_requested_at'
            then nullif(p_updates ->> 'auth_requested_at', '')::timestamptz
            else c.auth_requested_at
        end,
        auth_completed_at = case
            when p_updates ? 'auth_completed_at'
            then nullif(p_updates ->> 'auth_completed_at', '')::timestamptz
            else c.auth_completed_at
        end,
        last_safe_error_code = case
            when p_updates ? 'last_safe_error_code'
            then nullif(p_updates ->> 'last_safe_error_code', '')
            else c.last_safe_error_code
        end,
        updated_at = now()
    where c.id = p_case_id
      and c.owner_user_id = v_owner_user_id
    returning c.* into v_case;

    if not found then
        raise exception 'claim case not found';
    end if;

    select coalesce(jsonb_agg(field_name order by field_name), '[]'::jsonb)
    into v_updated_fields
    from jsonb_object_keys(coalesce(p_updates, '{}'::jsonb)) field_name
    where field_name = any (
        array[
            'hometax_status',
            'comwel_status',
            'overall_status',
            'auth_requested_at',
            'auth_completed_at',
            'last_safe_error_code'
        ]
    );

    insert into public.oasis_claim_audit_events (
        owner_user_id,
        case_id,
        action,
        source,
        outcome,
        metadata
    )
    values (
        v_owner_user_id,
        p_case_id,
        'case_status_updated',
        'system',
        'success',
        jsonb_build_object('updated_fields', v_updated_fields)
    );

    return next v_case;
end;
$$;

create or replace function public.oasis_claim_update_document_status(
    p_owner_user_id text,
    p_case_id uuid,
    p_source text,
    p_status text
)
returns integer
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_count integer;
    v_owner_user_id text := lower(trim(p_owner_user_id));
    v_source text := lower(trim(p_source));
begin
    if v_source not in ('hometax', 'comwel') then
        raise exception 'invalid claim source';
    end if;
    if not (
        nullif(trim(p_status), '') = any (
            array[
                'not_requested',
                'auth_pending',
                'integration_required',
                'collection_queued',
                'collecting',
                'collected',
                'ready',
                'failed'
            ]::text[]
        )
    ) then
        raise exception 'invalid claim document status';
    end if;
    if not exists (
        select 1
        from public.oasis_claim_cases c
        where c.id = p_case_id
          and c.owner_user_id = v_owner_user_id
    ) then
        raise exception 'claim case not found';
    end if;

    update public.oasis_claim_documents d
    set status = nullif(trim(p_status), ''), updated_at = now()
    where d.case_id = p_case_id
      and d.owner_user_id = v_owner_user_id
      and d.source = v_source;
    get diagnostics v_count = row_count;

    insert into public.oasis_claim_audit_events (
        owner_user_id,
        case_id,
        action,
        source,
        outcome,
        metadata
    )
    values (
        v_owner_user_id,
        p_case_id,
        'document_status_updated',
        v_source,
        'success',
        jsonb_build_object('document_count', v_count)
    );

    return v_count;
end;
$$;

alter table public.oasis_claim_cases enable row level security;
alter table public.oasis_claim_documents enable row level security;
alter table public.oasis_claim_audit_events enable row level security;

revoke all on table public.oasis_claim_cases
from public, anon, authenticated, service_role;
revoke all on table public.oasis_claim_documents
from public, anon, authenticated, service_role;
revoke all on table public.oasis_claim_audit_events
from public, anon, authenticated, service_role;
revoke all on sequence public.oasis_claim_audit_events_id_seq
from public, anon, authenticated, service_role;

revoke all on function public.set_oasis_claim_updated_at()
from public, anon, authenticated, service_role;
revoke all on function public.oasis_create_claim_case(jsonb, jsonb, jsonb)
from public, anon, authenticated, service_role;
grant execute
on function public.oasis_create_claim_case(jsonb, jsonb, jsonb)
to service_role;
revoke all on function public.oasis_claim_list_cases(text, integer)
from public, anon, authenticated, service_role;
grant execute
on function public.oasis_claim_list_cases(text, integer)
to service_role;
revoke all on function public.oasis_claim_get_case(text, uuid)
from public, anon, authenticated, service_role;
grant execute
on function public.oasis_claim_get_case(text, uuid)
to service_role;
revoke all on function public.oasis_claim_list_documents(text, uuid)
from public, anon, authenticated, service_role;
grant execute
on function public.oasis_claim_list_documents(text, uuid)
to service_role;
revoke all on function public.oasis_claim_append_audit(
    text,
    uuid,
    text,
    text,
    text,
    jsonb
)
from public, anon, authenticated, service_role;
grant execute
on function public.oasis_claim_append_audit(
    text,
    uuid,
    text,
    text,
    text,
    jsonb
)
to service_role;
revoke all on function public.oasis_claim_update_case_status(text, uuid, jsonb)
from public, anon, authenticated, service_role;
grant execute
on function public.oasis_claim_update_case_status(text, uuid, jsonb)
to service_role;
revoke all on function public.oasis_claim_update_document_status(
    text,
    uuid,
    text,
    text
)
from public, anon, authenticated, service_role;
grant execute
on function public.oasis_claim_update_document_status(
    text,
    uuid,
    text,
    text
)
to service_role;

insert into storage.buckets (
    id,
    name,
    public,
    file_size_limit,
    allowed_mime_types
)
values (
    'oasis-claim-documents',
    'oasis-claim-documents',
    false,
    20971520,
    array[
        'application/pdf',
        'application/json',
        'application/xml',
        'text/xml'
    ]
)
on conflict (id) do update
set
    public = false,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists "claim_documents_public_read"
on storage.objects;
drop policy if exists "claim_documents_authenticated_read"
on storage.objects;
drop policy if exists "claim_documents_authenticated_write"
on storage.objects;

commit;
