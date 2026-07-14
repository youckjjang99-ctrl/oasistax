-- OASIS v6.3.3 audio metadata normalization upgrade
-- Safe to run more than once.

alter table if exists public.oasis_consultation_audio
    add column if not exists business_no_normalized text,
    add column if not exists original_audio_sha256 text,
    add column if not exists original_size_bytes bigint;

update public.oasis_consultation_audio
set business_no_normalized = regexp_replace(coalesce(business_no, ''), '[^0-9]', '', 'g')
where coalesce(business_no_normalized, '') = '';

update public.oasis_consultation_audio
set original_audio_sha256 = audio_sha256
where coalesce(original_audio_sha256, '') = '';

update public.oasis_consultation_audio
set original_size_bytes = size_bytes
where original_size_bytes is null;

create index if not exists idx_oasis_consultation_audio_owner_business_normalized
on public.oasis_consultation_audio(owner_user_id, business_no_normalized, created_at desc);

create index if not exists idx_oasis_consultation_audio_owner_original_hash
on public.oasis_consultation_audio(owner_user_id, original_audio_sha256, created_at desc);
