-- OASIS CRM v4.0.0 Supabase schema
create extension if not exists pgcrypto;

create table if not exists public.oasis_customers (
    id uuid primary key default gen_random_uuid(),
    owner_user_id text not null,
    business_no text not null,
    company_name text,
    representative_name text,
    industry_name text,
    address text,
    manager_name text,
    source text default 'migration',
    customer_data jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (owner_user_id, business_no)
);

create table if not exists public.oasis_crm (
    id uuid primary key default gen_random_uuid(),
    owner_user_id text not null,
    business_no text not null,
    crm_data jsonb not null default '{}'::jsonb,
    updated_at timestamptz not null default now(),
    unique (owner_user_id, business_no)
);

create table if not exists public.oasis_financials (
    id uuid primary key default gen_random_uuid(),
    owner_user_id text not null,
    business_no text not null,
    financial_data jsonb not null default '{}'::jsonb,
    updated_at timestamptz not null default now(),
    unique (owner_user_id, business_no)
);

create table if not exists public.oasis_registry (
    id uuid primary key default gen_random_uuid(),
    owner_user_id text not null,
    business_no text not null,
    registry_data jsonb not null default '{}'::jsonb,
    updated_at timestamptz not null default now(),
    unique (owner_user_id, business_no)
);

create table if not exists public.oasis_stock_valuations (
    id uuid primary key default gen_random_uuid(),
    owner_user_id text not null,
    record_id text not null,
    business_no text,
    company_name text,
    valuation_date date,
    valuation_data jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (owner_user_id, record_id)
);

create table if not exists public.oasis_migration_runs (
    id uuid primary key default gen_random_uuid(),
    owner_user_id text not null,
    migration_version text not null,
    result_data jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create or replace function public.set_oasis_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists trg_oasis_customers_updated_at on public.oasis_customers;
create trigger trg_oasis_customers_updated_at
before update on public.oasis_customers
for each row execute function public.set_oasis_updated_at();

drop trigger if exists trg_oasis_crm_updated_at on public.oasis_crm;
create trigger trg_oasis_crm_updated_at
before update on public.oasis_crm
for each row execute function public.set_oasis_updated_at();

drop trigger if exists trg_oasis_financials_updated_at on public.oasis_financials;
create trigger trg_oasis_financials_updated_at
before update on public.oasis_financials
for each row execute function public.set_oasis_updated_at();

drop trigger if exists trg_oasis_registry_updated_at on public.oasis_registry;
create trigger trg_oasis_registry_updated_at
before update on public.oasis_registry
for each row execute function public.set_oasis_updated_at();

drop trigger if exists trg_oasis_stock_updated_at on public.oasis_stock_valuations;
create trigger trg_oasis_stock_updated_at
before update on public.oasis_stock_valuations
for each row execute function public.set_oasis_updated_at();

alter table public.oasis_customers enable row level security;
alter table public.oasis_crm enable row level security;
alter table public.oasis_financials enable row level security;
alter table public.oasis_registry enable row level security;
alter table public.oasis_stock_valuations enable row level security;
alter table public.oasis_migration_runs enable row level security;
