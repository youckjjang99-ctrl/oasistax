-- OASIS v4.2.0 upgrade
create table if not exists public.oasis_matching_preferences (
    id uuid primary key default gen_random_uuid(),
    owner_user_id text not null,
    business_no text not null,
    preference_data jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (owner_user_id, business_no)
);

drop trigger if exists trg_oasis_matching_preferences_updated_at
on public.oasis_matching_preferences;

create trigger trg_oasis_matching_preferences_updated_at
before update on public.oasis_matching_preferences
for each row execute function public.set_oasis_updated_at();

alter table public.oasis_matching_preferences enable row level security;
