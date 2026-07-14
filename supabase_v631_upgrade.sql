-- OASIS v6.3.1 녹취/상담일지 AI 캐시 영구 저장
create table if not exists public.oasis_consultation_ai_cache (
    id bigserial primary key,
    owner_user_id text not null,
    cache_key text not null,
    cache_type text not null,
    cache_data jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique(owner_user_id, cache_key)
);

create index if not exists idx_oasis_consultation_ai_cache_owner_type
on public.oasis_consultation_ai_cache(owner_user_id, cache_type, updated_at desc);

alter table public.oasis_consultation_ai_cache enable row level security;
