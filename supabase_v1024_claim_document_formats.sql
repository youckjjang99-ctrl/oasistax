-- OASIS CRM v10.2.4 - 경정청구 PDF·엑셀·JSON 문서 저장 허용
-- Railway service_role 전용 RPC 권한과 비공개 Storage 경로 정책은 유지한다.

begin;

create or replace function public.oasis_claim_finalize_document(
    p_owner_user_id text,
    p_case_id uuid,
    p_document_id uuid,
    p_status text,
    p_storage_bucket text default null,
    p_storage_path text default null,
    p_content_sha256 text default null,
    p_content_type text default null,
    p_size_bytes bigint default null,
    p_retention_until timestamptz default null,
    p_facts jsonb default '{}'::jsonb,
    p_safe_error_code text default null
)
returns setof public.oasis_claim_documents
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_owner_user_id text := lower(trim(p_owner_user_id));
    v_status text := lower(trim(p_status));
    v_document public.oasis_claim_documents;
    v_facts jsonb := coalesce(p_facts, '{}'::jsonb);
begin
    if v_status not in ('ready', 'failed') then
        raise exception 'invalid claim document result status';
    end if;
    if jsonb_typeof(v_facts) <> 'object' then
        raise exception 'claim document facts must be an object';
    end if;

    select d.*
    into v_document
    from public.oasis_claim_documents d
    where d.id = p_document_id
      and d.case_id = p_case_id
      and d.owner_user_id = v_owner_user_id
    for update;

    if not found then
        raise exception 'claim document not found';
    end if;

    if v_status = 'ready' then
        if nullif(trim(p_storage_bucket), '') is null
           or nullif(trim(p_storage_path), '') is null
           or nullif(trim(p_content_sha256), '') is null
           or p_content_sha256 !~ '^[0-9a-f]{64}$'
           or p_content_type not in (
               'application/pdf',
               'application/json',
               'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
               'application/vnd.ms-excel',
               'text/csv'
           )
           or (
               case p_content_type
               when 'application/pdf'
                   then lower(p_storage_path) !~ '\.pdf$'
               when 'application/json'
                   then lower(p_storage_path) !~ '\.json$'
               when 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                   then lower(p_storage_path) !~ '\.xlsx$'
               when 'application/vnd.ms-excel'
                   then lower(p_storage_path) !~ '\.xls$'
               when 'text/csv'
                   then lower(p_storage_path) !~ '\.csv$'
               else true
               end
           )
           or p_size_bytes is null
           or p_size_bytes < 5
           or p_size_bytes > 20971520
           or p_retention_until is null
           or p_retention_until <= now()
        then
            raise exception 'invalid ready claim document metadata';
        end if;
    else
        v_facts := v_facts || jsonb_build_object(
            'safe_error_code',
            left(
                coalesce(
                    p_safe_error_code,
                    'DOCUMENT_COLLECTION_FAILED'
                ),
                80
            )
        );
    end if;

    update public.oasis_claim_documents d
    set
        status = v_status,
        facts = v_facts,
        storage_bucket = case
            when v_status = 'ready' then trim(p_storage_bucket)
            else null
        end,
        storage_path = case
            when v_status = 'ready' then trim(p_storage_path)
            else null
        end,
        content_sha256 = case
            when v_status = 'ready' then lower(trim(p_content_sha256))
            else null
        end,
        content_type = case
            when v_status = 'ready' then p_content_type
            else null
        end,
        size_bytes = case
            when v_status = 'ready' then p_size_bytes
            else null
        end,
        retention_until = case
            when v_status = 'ready' then p_retention_until
            else null
        end,
        collected_at = case
            when v_status = 'ready' then now()
            else null
        end,
        deleted_at = null,
        updated_at = now()
    where d.id = p_document_id
      and d.case_id = p_case_id
      and d.owner_user_id = v_owner_user_id
    returning d.* into v_document;

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
        case
            when v_status = 'ready' then 'document_collected'
            else 'document_collection_failed'
        end,
        v_document.source,
        case when v_status = 'ready' then 'success' else 'failed' end,
        jsonb_build_object(
            'document_code', v_document.document_code,
            'document_status', v_status,
            'content_type', case
                when v_status = 'ready' then p_content_type
                else null
            end,
            'size_bytes', case
                when v_status = 'ready' then p_size_bytes
                else null
            end,
            'safe_error_code', case
                when v_status = 'failed'
                then left(coalesce(p_safe_error_code, ''), 80)
                else null
            end
        )
    );

    return next v_document;
end;
$$;

revoke all on function public.oasis_claim_finalize_document(
    text,
    uuid,
    uuid,
    text,
    text,
    text,
    text,
    text,
    bigint,
    timestamptz,
    jsonb,
    text
)
from public, anon, authenticated, service_role;

grant execute on function public.oasis_claim_finalize_document(
    text,
    uuid,
    uuid,
    text,
    text,
    text,
    text,
    text,
    bigint,
    timestamptz,
    jsonb,
    text
)
to service_role;

commit;
