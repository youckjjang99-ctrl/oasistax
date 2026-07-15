create table if not exists public.oasis_login_sessions (
    id bigserial primary key,
    owner_user_id text not null unique,
    session_token text not null,
    updated_at timestamptz not null default now()
);

create index if not exists idx_oasis_login_sessions_owner
on public.oasis_login_sessions(owner_user_id);

alter table public.oasis_login_sessions enable row level security;
