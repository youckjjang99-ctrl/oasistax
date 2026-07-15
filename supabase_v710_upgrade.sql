-- OASIS v7.1.0 기업고객 휴지통
create table if not exists public.oasis_customer_trash (
    id bigserial primary key,
    owner_user_id text not null,
    customer_uid text not null,
    business_no text,
    company_name text,
    representative_name text,
    is_deleted boolean not null default true,
    delete_reason text,
    deleted_by text,
    deleted_at timestamptz,
    restored_at timestamptz,
    snapshot_data jsonb not null default '{}'::jsonb,
    updated_at timestamptz not null default now(),
    unique(owner_user_id, customer_uid)
);

create index if not exists idx_oasis_customer_trash_owner
on public.oasis_customer_trash(owner_user_id, is_deleted, updated_at desc);

alter table public.oasis_customer_trash enable row level security;
