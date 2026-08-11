create table if not exists public.oasis_claim_sales_applications (
    id uuid primary key default gen_random_uuid(),
    owner_user_id text not null,
    status text not null default 'submitted'
        check (status in ('submitted', 'reviewing', 'approved', 'rejected', 'withdrawn')),
    secure_payload_ciphertext text not null,
    payload_key_version text not null,
    consent_version text not null,
    consented_at timestamptz not null,
    retention_expires_at timestamptz not null,
    reviewed_by_user_id text,
    reviewed_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

comment on table public.oasis_claim_sales_applications is
    'Encrypted claim-correction sales applications; sensitive fields are never stored in plaintext.';

create index if not exists idx_claim_sales_applications_owner_created
    on public.oasis_claim_sales_applications (owner_user_id, created_at desc);

create index if not exists idx_claim_sales_applications_status_created
    on public.oasis_claim_sales_applications (status, created_at desc);

create index if not exists idx_claim_sales_applications_retention
    on public.oasis_claim_sales_applications (retention_expires_at)
    where status in ('submitted', 'reviewing');

alter table public.oasis_claim_sales_applications enable row level security;
alter table public.oasis_claim_sales_applications force row level security;

revoke all on table public.oasis_claim_sales_applications
    from anon, authenticated, public;
grant select, insert, update, delete
    on table public.oasis_claim_sales_applications to service_role;
