-- Preserve high-recall Daum Web mobile candidates without exposing them to
-- ordinary users. Only the service-side collector and service-only admin RPCs
-- can access these rows.

create table if not exists public.oasis_daum_mobile_review_candidates (
    id uuid primary key default gen_random_uuid(),
    contact_key text not null
        references public.oasis_employment_contacts(contact_key)
        on delete cascade,
    mobile_phone text not null,
    source_url text not null default '',
    query_mode text not null default '',
    evidence jsonb not null default '{}'::jsonb,
    confidence integer not null default 0,
    occurrence_count integer not null default 1,
    review_status text not null default 'pending',
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    reviewed_at timestamptz,
    reviewed_by text not null default '',
    review_reason text not null default '',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint oasis_daum_mobile_review_candidates_phone_check
        check (mobile_phone ~ '^010[0-9]{8}$'),
    constraint oasis_daum_mobile_review_candidates_confidence_check
        check (confidence between 0 and 100),
    constraint oasis_daum_mobile_review_candidates_occurrence_check
        check (occurrence_count >= 1),
    constraint oasis_daum_mobile_review_candidates_status_check
        check (
            review_status in (
                'pending', 'approved', 'rejected',
                'auto_verified', 'superseded'
            )
        ),
    constraint oasis_daum_mobile_review_candidates_unique
        unique (contact_key, mobile_phone)
);

create index if not exists idx_oasis_daum_mobile_review_pending
    on public.oasis_daum_mobile_review_candidates (
        last_seen_at desc,
        id
    )
    where review_status = 'pending';

create index if not exists idx_oasis_daum_mobile_review_contact
    on public.oasis_daum_mobile_review_candidates (
        contact_key,
        review_status
    );

alter table public.oasis_daum_mobile_review_candidates
    enable row level security;
alter table public.oasis_daum_mobile_review_candidates
    force row level security;

revoke all on table public.oasis_daum_mobile_review_candidates
    from public, anon, authenticated;
grant select, insert, update on table
    public.oasis_daum_mobile_review_candidates to service_role;

comment on table public.oasis_daum_mobile_review_candidates is
    'Service-only review queue for public mobile candidates that did not meet automatic Daum verification.';

create table if not exists public.oasis_contact_enrichment_run_metrics (
    run_id uuid primary key,
    provider text not null,
    started_at timestamptz not null,
    updated_at timestamptz not null default now(),
    processed_count integer not null default 0,
    request_count integer not null default 0,
    diagnostics jsonb not null default '{}'::jsonb,
    constraint oasis_contact_enrichment_run_provider_check
        check (provider in ('kakao', 'daum')),
    constraint oasis_contact_enrichment_run_counts_check
        check (processed_count >= 0 and request_count >= 0)
);

create index if not exists idx_oasis_contact_enrichment_metrics_provider
    on public.oasis_contact_enrichment_run_metrics (
        provider,
        started_at desc
    );

alter table public.oasis_contact_enrichment_run_metrics
    enable row level security;
alter table public.oasis_contact_enrichment_run_metrics
    force row level security;

revoke all on table public.oasis_contact_enrichment_run_metrics
    from public, anon, authenticated;
grant select, insert, update on table
    public.oasis_contact_enrichment_run_metrics to service_role;

comment on table public.oasis_contact_enrichment_run_metrics is
    'Compact, PII-free provider funnel metrics persisted per collector run.';

create or replace function public.oasis_upsert_daum_mobile_review_candidates(
    p_contact_key text,
    p_candidates jsonb default '[]'::jsonb,
    p_auto_verified_mobile text default ''
)
returns table (
    upserted_count integer,
    auto_verified_count integer
)
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_contact_key text := btrim(coalesce(p_contact_key, ''));
    v_candidate jsonb;
    v_phone text;
    v_auto_phone text := public.oasis_normalize_sales_phone(
        p_auto_verified_mobile
    );
    v_upserted integer := 0;
    v_auto_verified integer := 0;
begin
    if v_contact_key = '' or not exists (
        select 1
        from public.oasis_employment_contacts c
        where c.contact_key = v_contact_key
    ) then
        raise exception using errcode = '22023', message = 'INVALID_CONTACT_KEY';
    end if;

    if jsonb_typeof(coalesce(p_candidates, '[]'::jsonb)) <> 'array'
       or jsonb_array_length(coalesce(p_candidates, '[]'::jsonb)) > 8 then
        raise exception using errcode = '22023', message = 'INVALID_CANDIDATES';
    end if;

    for v_candidate in
        select value
        from jsonb_array_elements(coalesce(p_candidates, '[]'::jsonb))
    loop
        if jsonb_typeof(v_candidate) <> 'object' then
            continue;
        end if;
        v_phone := public.oasis_normalize_sales_phone(
            v_candidate ->> 'mobile_phone'
        );
        if coalesce(v_phone, '') !~ '^010[0-9]{8}$' then
            continue;
        end if;

        insert into public.oasis_daum_mobile_review_candidates (
            contact_key,
            mobile_phone,
            source_url,
            query_mode,
            evidence,
            confidence,
            occurrence_count,
            review_status,
            first_seen_at,
            last_seen_at,
            updated_at
        ) values (
            v_contact_key,
            v_phone,
            left(coalesce(v_candidate ->> 'source_url', ''), 2000),
            left(coalesce(v_candidate ->> 'query_mode', ''), 40),
            case
                when jsonb_typeof(v_candidate -> 'evidence') = 'object'
                    then v_candidate -> 'evidence'
                else '{}'::jsonb
            end,
            least(
                84,
                greatest(
                    0,
                    case
                        when coalesce(v_candidate ->> 'confidence', '')
                            ~ '^[0-9]{1,3}$'
                            then (v_candidate ->> 'confidence')::integer
                        else 0
                    end
                )
            ),
            1,
            case
                when v_phone = v_auto_phone then 'auto_verified'
                else 'pending'
            end,
            now(),
            now(),
            now()
        )
        on conflict on constraint oasis_daum_mobile_review_candidates_unique
        do update set
            source_url = case
                when excluded.source_url <> '' then excluded.source_url
                else public.oasis_daum_mobile_review_candidates.source_url
            end,
            query_mode = case
                when excluded.query_mode <> '' then excluded.query_mode
                else public.oasis_daum_mobile_review_candidates.query_mode
            end,
            evidence = case
                when excluded.confidence
                    >= public.oasis_daum_mobile_review_candidates.confidence
                    then excluded.evidence
                else public.oasis_daum_mobile_review_candidates.evidence
            end,
            confidence = greatest(
                public.oasis_daum_mobile_review_candidates.confidence,
                excluded.confidence
            ),
            occurrence_count =
                public.oasis_daum_mobile_review_candidates.occurrence_count + 1,
            review_status = case
                when excluded.review_status = 'auto_verified'
                    then 'auto_verified'
                else public.oasis_daum_mobile_review_candidates.review_status
            end,
            last_seen_at = now(),
            updated_at = now();
        v_upserted := v_upserted + 1;
    end loop;

    if v_auto_phone ~ '^010[0-9]{8}$' then
        update public.oasis_daum_mobile_review_candidates c
        set review_status = 'auto_verified',
            reviewed_at = coalesce(c.reviewed_at, now()),
            reviewed_by = case
                when c.reviewed_by = '' then 'collector'
                else c.reviewed_by
            end,
            updated_at = now()
        where c.contact_key = v_contact_key
          and c.mobile_phone = v_auto_phone
          and c.review_status = 'pending';
        get diagnostics v_auto_verified = row_count;

        update public.oasis_daum_mobile_review_candidates c
        set review_status = 'superseded',
            updated_at = now()
        where c.contact_key = v_contact_key
          and c.mobile_phone <> v_auto_phone
          and c.review_status = 'pending';
    end if;

    return query select v_upserted, v_auto_verified;
end;
$$;

revoke all on function
    public.oasis_upsert_daum_mobile_review_candidates(text, jsonb, text)
    from public, anon, authenticated;
grant execute on function
    public.oasis_upsert_daum_mobile_review_candidates(text, jsonb, text)
    to service_role;

create or replace function public.oasis_record_contact_enrichment_run_metrics(
    p_run_id uuid,
    p_provider text,
    p_started_at timestamptz,
    p_processed_count integer,
    p_request_count integer,
    p_diagnostics jsonb default '{}'::jsonb
)
returns void
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_provider text := lower(btrim(coalesce(p_provider, '')));
begin
    if p_run_id is null
       or v_provider not in ('kakao', 'daum')
       or coalesce(p_processed_count, -1) < 0
       or coalesce(p_request_count, -1) < 0
       or jsonb_typeof(coalesce(p_diagnostics, '{}'::jsonb)) <> 'object' then
        raise exception using errcode = '22023', message = 'INVALID_METRICS';
    end if;

    insert into public.oasis_contact_enrichment_run_metrics (
        run_id,
        provider,
        started_at,
        updated_at,
        processed_count,
        request_count,
        diagnostics
    ) values (
        p_run_id,
        v_provider,
        coalesce(p_started_at, now()),
        now(),
        p_processed_count,
        p_request_count,
        p_diagnostics
    )
    on conflict (run_id) do update set
        updated_at = now(),
        processed_count = excluded.processed_count,
        request_count = excluded.request_count,
        diagnostics = excluded.diagnostics;
end;
$$;

revoke all on function public.oasis_record_contact_enrichment_run_metrics(
    uuid, text, timestamptz, integer, integer, jsonb
) from public, anon, authenticated;
grant execute on function public.oasis_record_contact_enrichment_run_metrics(
    uuid, text, timestamptz, integer, integer, jsonb
) to service_role;

create or replace function public.oasis_list_admin_daum_mobile_candidates(
    p_current_user_id text,
    p_statuses text[] default array['pending']::text[],
    p_limit integer default 300
)
returns table (
    candidate_id uuid,
    contact_key text,
    company_name text,
    business_no text,
    address text,
    industry_name text,
    mobile_phone text,
    source_url text,
    query_mode text,
    evidence jsonb,
    confidence integer,
    occurrence_count integer,
    review_status text,
    first_seen_at timestamptz,
    last_seen_at timestamptz
)
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_statuses text[] := coalesce(p_statuses, array['pending']::text[]);
begin
    if not public.oasis_sales_actor_is_admin(p_current_user_id) then
        raise exception using errcode = '42501', message = 'ADMIN_REQUIRED';
    end if;

    return query
    select
        r.id,
        r.contact_key,
        c.company_name,
        c.business_no,
        c.address,
        c.industry_name,
        r.mobile_phone,
        r.source_url,
        r.query_mode,
        r.evidence,
        r.confidence,
        r.occurrence_count,
        r.review_status,
        r.first_seen_at,
        r.last_seen_at
    from public.oasis_daum_mobile_review_candidates r
    join public.oasis_employment_contacts c
      on c.contact_key = r.contact_key
    where r.review_status = any(v_statuses)
    order by r.last_seen_at desc, r.id
    limit greatest(1, least(coalesce(p_limit, 300), 1000));
end;
$$;

revoke all on function public.oasis_list_admin_daum_mobile_candidates(
    text, text[], integer
) from public, anon, authenticated;
grant execute on function public.oasis_list_admin_daum_mobile_candidates(
    text, text[], integer
) to service_role;

create or replace function public.oasis_admin_review_daum_mobile_candidate(
    p_current_user_id text,
    p_candidate_id uuid,
    p_action text,
    p_reason text default ''
)
returns table (
    success boolean,
    code text,
    message text,
    review_status text
)
language plpgsql
security invoker
set search_path = ''
as $$
declare
    v_action text := lower(btrim(coalesce(p_action, '')));
    v_reason text := left(btrim(coalesce(p_reason, '')), 500);
    v_candidate public.oasis_daum_mobile_review_candidates%rowtype;
    v_existing_mobile text;
begin
    if not public.oasis_sales_actor_is_admin(p_current_user_id) then
        raise exception using errcode = '42501', message = 'ADMIN_REQUIRED';
    end if;
    if v_action not in ('approve', 'reject') or p_candidate_id is null then
        return query select false, 'INVALID_INPUT',
            '입력값을 확인해 주세요.', '';
        return;
    end if;
    if v_action = 'reject' and v_reason = '' then
        return query select false, 'REASON_REQUIRED',
            '제외 사유를 입력해 주세요.', '';
        return;
    end if;

    select * into v_candidate
    from public.oasis_daum_mobile_review_candidates r
    where r.id = p_candidate_id
    for update;

    if not found then
        return query select false, 'NOT_FOUND',
            '검토 후보를 찾을 수 없습니다.', '';
        return;
    end if;
    if v_candidate.review_status <> 'pending' then
        return query select false, 'ALREADY_REVIEWED',
            '이미 처리된 검토 후보입니다.', v_candidate.review_status;
        return;
    end if;

    if v_action = 'reject' then
        update public.oasis_daum_mobile_review_candidates r
        set review_status = 'rejected',
            reviewed_at = now(),
            reviewed_by = lower(btrim(p_current_user_id)),
            review_reason = v_reason,
            updated_at = now()
        where r.id = p_candidate_id;
        return query select true, 'OK',
            '핸드폰 후보를 제외했습니다.', 'rejected';
        return;
    end if;

    select public.oasis_normalize_sales_phone(c.mobile_phone)
      into v_existing_mobile
    from public.oasis_employment_contacts c
    where c.contact_key = v_candidate.contact_key
    for update;

    if not found then
        return query select false, 'NOT_FOUND',
            '연결된 업체를 찾을 수 없습니다.', '';
        return;
    end if;
    if v_existing_mobile ~ '^010[0-9]{8}$'
       and v_existing_mobile <> v_candidate.mobile_phone then
        return query select false, 'MOBILE_ALREADY_EXISTS',
            '이미 다른 핸드폰번호가 등록되어 있어 자동으로 덮어쓰지 않았습니다.',
            'pending';
        return;
    end if;

    update public.oasis_employment_contacts c
    set mobile_phone = v_candidate.mobile_phone,
        has_mobile_phone = true,
        contact_sources = jsonb_set(
            coalesce(c.contact_sources, '{}'::jsonb),
            '{mobile_phone}',
            jsonb_build_object(
                'source_type', 'daum_web_snippet',
                'source_url', v_candidate.source_url,
                'confidence', v_candidate.confidence,
                'query_mode', v_candidate.query_mode,
                'evidence', 'admin_approved_candidate',
                'approved_at', now(),
                'candidate_evidence', v_candidate.evidence
            ),
            true
        ),
        status = 'matched',
        checked_at = now(),
        next_check_at = now() + interval '30 days',
        last_error = '',
        phone_status = 'matched',
        phone_checked_at = now(),
        phone_next_check_at = now() + interval '30 days',
        phone_last_error = '',
        phone_provider_stage = 'complete',
        updated_at = now()
    where c.contact_key = v_candidate.contact_key;

    update public.oasis_daum_mobile_review_candidates r
    set review_status = case
            when r.id = p_candidate_id then 'approved'
            else 'superseded'
        end,
        reviewed_at = case
            when r.id = p_candidate_id then now()
            else r.reviewed_at
        end,
        reviewed_by = case
            when r.id = p_candidate_id
                then lower(btrim(p_current_user_id))
            else r.reviewed_by
        end,
        review_reason = case
            when r.id = p_candidate_id then v_reason
            else r.review_reason
        end,
        updated_at = now()
    where r.contact_key = v_candidate.contact_key
      and r.review_status = 'pending';

    return query select true, 'OK',
        '핸드폰 후보를 승인하여 업체 연락처에 반영했습니다.', 'approved';
end;
$$;

revoke all on function public.oasis_admin_review_daum_mobile_candidate(
    text, uuid, text, text
) from public, anon, authenticated;
grant execute on function public.oasis_admin_review_daum_mobile_candidate(
    text, uuid, text, text
) to service_role;
