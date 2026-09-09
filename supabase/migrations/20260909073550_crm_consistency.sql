-- OASIS v9.13.0. Additive CRM compare-and-swap and preserved save requests.
-- Existing custom Railway login is retained; browser roles get no RPC access.
begin;
set local lock_timeout = '5s';

alter table public.oasis_crm add column if not exists crm_version bigint not null default 1;
create index if not exists oasis_crm_owner_updated_id_idx
    on public.oasis_crm(owner_user_id, updated_at, id);

create table if not exists public.oasis_crm_save_requests (
    id uuid primary key,
    owner_user_id text not null,
    business_no text not null,
    expected_version bigint not null,
    proposed_patch jsonb not null,
    proposed_events jsonb not null,
    status text not null check (status in ('applied', 'conflict')),
    resulting_version bigint not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists oasis_crm_save_requests_owner_created_idx
    on public.oasis_crm_save_requests(owner_user_id, created_at desc);
alter table public.oasis_crm_save_requests enable row level security;
revoke all on public.oasis_crm_save_requests from public, anon, authenticated, service_role;
grant select, insert on public.oasis_crm_save_requests to service_role;

create or replace function public.oasis_guard_crm_versioned_write()
returns trigger language plpgsql security invoker set search_path = '' as $$
begin
    if coalesce(current_setting('oasis.crm_versioned_write', true), '') <> 'on' then
        raise exception using errcode='40001', message='CRM_VERSIONED_WRITE_REQUIRED';
    end if;
    if tg_op = 'UPDATE' then
        new.crm_version := old.crm_version + 1;
    else
        new.crm_version := 1;
    end if;
    return new;
end;
$$;
revoke all on function public.oasis_guard_crm_versioned_write() from public, anon, authenticated;
grant execute on function public.oasis_guard_crm_versioned_write() to service_role;
drop trigger if exists oasis_crm_versioned_write_guard on public.oasis_crm;
create trigger oasis_crm_versioned_write_guard
before insert or update of crm_data on public.oasis_crm
for each row execute function public.oasis_guard_crm_versioned_write();

create or replace function public.oasis_save_crm_versioned(
    p_owner_user_id text, p_business_no text, p_request_id uuid,
    p_expected_version bigint, p_patch jsonb, p_events jsonb
) returns jsonb language plpgsql security invoker set search_path = '' as $$
declare
    v_row public.oasis_crm%rowtype;
    v_request public.oasis_crm_save_requests%rowtype;
    v_biz text := btrim(p_business_no);
    v_digits text := regexp_replace(p_business_no, '[^0-9]', '', 'g');
    v_candidates bigint;
    v_exists boolean;
    v_status text := 'applied';
    v_events jsonb;
    v_data jsonb;
    v_prior_guard text := current_setting('oasis.crm_versioned_write', true);
begin
    if nullif(btrim(p_owner_user_id), '') is null or nullif(v_biz, '') is null
       or p_request_id is null or p_expected_version is null or p_expected_version < 0
       or p_patch is null or jsonb_typeof(p_patch) <> 'object'
       or p_events is null or jsonb_typeof(p_events) <> 'array' then
        raise exception using errcode='22023', message='CRM_SAVE_INVALID_INPUT';
    end if;
    if exists (select 1 from jsonb_object_keys(p_patch) k where k = any(array[
        '_local_revision','_cloud_base','_cloud_version','_sync_state','_cloud_latest',
        '_cloud_latest_version','_sync_error','_sync_request_id','timeline','timelines',
        'created_at','updated_at'
    ])) or exists (select 1 from jsonb_array_elements(p_events) e where jsonb_typeof(e) <> 'object') then
        raise exception using errcode='22023', message='CRM_SAVE_INVALID_PAYLOAD';
    end if;
    if length(v_digits) = 10 then
        v_biz := substr(v_digits,1,3)||'-'||substr(v_digits,4,2)||'-'||substr(v_digits,6,5);
    end if;
    -- Serializes replays first, then writes to one owner/customer, including
    -- creation when no row exists yet. No external I/O is inside this RPC.
    perform pg_advisory_xact_lock(hashtextextended('crm-request:'||p_request_id::text, 0));
    perform pg_advisory_xact_lock(hashtextextended('crm:'||p_owner_user_id||':'||v_biz, 0));
    select * into v_request from public.oasis_crm_save_requests where id=p_request_id;
    if found and (v_request.owner_user_id <> p_owner_user_id or v_request.business_no <> v_biz
        or v_request.expected_version <> p_expected_version or v_request.proposed_patch <> p_patch
        or v_request.proposed_events <> p_events) then
        raise exception using errcode='22023', message='CRM_REQUEST_ID_REUSED';
    end if;
    select count(*) into v_candidates from public.oasis_crm c
    where c.owner_user_id=p_owner_user_id and (c.business_no=v_biz
        or (length(v_digits)=10 and regexp_replace(c.business_no,'[^0-9]','','g')=v_digits));
    if v_candidates > 1 then
        raise exception using errcode='40001', message='CRM_IDENTITY_REVIEW_REQUIRED';
    end if;
    select * into v_row from public.oasis_crm c
    where c.owner_user_id=p_owner_user_id and (c.business_no=v_biz
        or (length(v_digits)=10 and regexp_replace(c.business_no,'[^0-9]','','g')=v_digits))
    for update;
    v_exists := found;
    if v_request.id is not null then
        return jsonb_build_object('status',v_request.status,'version',coalesce(v_row.crm_version,0),
            'crm_data',coalesce(v_row.crm_data,'{}'::jsonb),'replayed',true,
            'owner_user_id',p_owner_user_id,'request_id',p_request_id);
    end if;
    if (v_exists and p_patch <> '{}'::jsonb and v_row.crm_version <> p_expected_version)
       or (not v_exists and p_expected_version <> 0) then
        v_status := 'conflict';
    elsif v_exists and (jsonb_typeof(v_row.crm_data) <> 'object'
        or (v_row.crm_data ? 'timeline' and jsonb_typeof(v_row.crm_data->'timeline') <> 'array')) then
        v_status := 'conflict';
    else
        v_data := coalesce(v_row.crm_data,'{}'::jsonb) || p_patch;
        -- p_events is an append delta, not a full snapshot. The request ledger
        -- prevents replay duplicates; existing multiplicity is never reduced.
        select coalesce(jsonb_agg(event order by coalesce(event->>'at',event->>'created_at','') desc,
            seq),'[]'::jsonb) into v_events
        from jsonb_array_elements(coalesce(v_data->'timeline','[]'::jsonb)||p_events)
            with ordinality as entries(event,seq);
        v_data := jsonb_set(v_data,'{timeline}',v_events,true);
        if not v_exists or v_data <> v_row.crm_data then
            v_data := jsonb_set(v_data,'{updated_at}',to_jsonb(clock_timestamp()::text),true);
            perform set_config('oasis.crm_versioned_write','on',true);
            if v_exists then
                update public.oasis_crm set crm_data=v_data where id=v_row.id returning * into v_row;
            else
                insert into public.oasis_crm(owner_user_id,business_no,crm_data)
                    values(p_owner_user_id,v_biz,v_data) returning * into v_row;
            end if;
            perform set_config('oasis.crm_versioned_write',coalesce(v_prior_guard,''),true);
        end if;
    end if;
    insert into public.oasis_crm_save_requests(id,owner_user_id,business_no,expected_version,
        proposed_patch,proposed_events,status,resulting_version)
    values(p_request_id,p_owner_user_id,v_biz,p_expected_version,p_patch,p_events,v_status,coalesce(v_row.crm_version,0));
    return jsonb_build_object('status',v_status,'version',coalesce(v_row.crm_version,0),
        'crm_data',coalesce(v_row.crm_data,'{}'::jsonb),'replayed',false,
        'owner_user_id',p_owner_user_id,'request_id',p_request_id);
end;
$$;
revoke all on function public.oasis_save_crm_versioned(text,text,uuid,bigint,jsonb,jsonb)
    from public, anon, authenticated;
grant execute on function public.oasis_save_crm_versioned(text,text,uuid,bigint,jsonb,jsonb) to service_role;
notify pgrst, 'reload schema';
commit;
