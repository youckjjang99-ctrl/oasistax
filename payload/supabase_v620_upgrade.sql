-- OASIS v6.2.0 상담 녹음 영구보관
insert into storage.buckets (id, name, public)
values ('oasis-consultation-audio', 'oasis-consultation-audio', false)
on conflict (id) do nothing;

create table if not exists public.oasis_consultation_audio (
    id bigserial primary key,
    audio_id text not null unique,
    owner_user_id text not null,
    user_name text,
    company_name text,
    business_no text,
    original_filename text,
    storage_bucket text not null default 'oasis-consultation-audio',
    storage_path text not null unique,
    audio_sha256 text not null,
    size_bytes bigint not null default 0,
    content_type text,
    journal_id text,
    consultation_title text,
    summary text,
    created_at timestamptz not null default now()
);

create index if not exists idx_oasis_consultation_audio_owner_business
on public.oasis_consultation_audio(owner_user_id, business_no, created_at desc);

create index if not exists idx_oasis_consultation_audio_hash
on public.oasis_consultation_audio(owner_user_id, business_no, audio_sha256);

alter table public.oasis_consultation_audio enable row level security;
