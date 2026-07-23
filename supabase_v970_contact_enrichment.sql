-- OASIS CRM v9.7.0
-- 영업후보 연락처 보강 결과 전용 테이블
-- 기존 테이블과 데이터는 변경하거나 삭제하지 않습니다.

create extension if not exists pgcrypto;

create table if not exists public.oasis_prospect_contacts (
    id uuid primary key default gen_random_uuid(),
    prospect_id uuid not null
        references public.oasis_prospect_companies(id) on delete cascade,
    contact_type text not null
        check (contact_type in ('phone', 'email', 'website')),
    contact_value text not null,
    contact_label text not null default '',
    source_type text not null default '',
    source_url text not null default '',
    confidence integer not null default 0
        check (confidence between 0 and 100),
    verification_status text not null default 'review_required'
        check (
            verification_status in (
                'review_required',
                'auto_verified',
                'manual_verified',
                'rejected'
            )
        ),
    is_primary boolean not null default false,
    owner_user_id text not null default '',
    metadata jsonb not null default '{}'::jsonb,
    do_not_contact boolean not null default false,
    opt_out_at timestamptz,
    collected_at timestamptz not null default now(),
    verified_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint oasis_prospect_contacts_unique
        unique (prospect_id, contact_type, contact_value)
);

create index if not exists idx_oasis_prospect_contacts_prospect
    on public.oasis_prospect_contacts (prospect_id);

create index if not exists idx_oasis_prospect_contacts_status
    on public.oasis_prospect_contacts
    (verification_status, contact_type, confidence desc);

create index if not exists idx_oasis_prospect_contacts_owner
    on public.oasis_prospect_contacts (owner_user_id, updated_at desc);

alter table public.oasis_prospect_contacts enable row level security;

comment on table public.oasis_prospect_contacts is
    'OASIS CRM 공개 출처 기반 잠재고객 연락처와 검증 상태';
comment on column public.oasis_prospect_contacts.source_url is
    '연락처를 확인한 공개 출처 URL';
comment on column public.oasis_prospect_contacts.do_not_contact is
    '수신거부 또는 연락금지 대상 표시';

select 'OASIS CRM v9.7.0 contact table ready' as result;
