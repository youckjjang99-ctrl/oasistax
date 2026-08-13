-- Replace the second-stage phone enrichment provider without changing any
-- collected phone values or completed rows. Legacy `naver` queue rows remain
-- valid temporarily and are drained by the Daum worker without a bulk update.

alter table public.oasis_employment_contacts
    drop constraint if exists
        oasis_employment_contacts_phone_provider_stage_check;

alter table public.oasis_employment_contacts
    add constraint
        oasis_employment_contacts_phone_provider_stage_check
    check (
        phone_provider_stage in (
            'kakao',
            'daum',
            'naver',
            'complete'
        )
    ) not valid;

alter table public.oasis_employment_contacts
    validate constraint
        oasis_employment_contacts_phone_provider_stage_check;

create index if not exists
    idx_oasis_employment_contacts_phone_daum_pending
on public.oasis_employment_contacts (created_at, contact_key)
where phone_provider_stage = 'daum'
  and phone_status = 'pending';

create index if not exists
    idx_oasis_employment_contacts_phone_daum_due
on public.oasis_employment_contacts (
    phone_next_check_at,
    updated_at,
    contact_key
)
where phone_provider_stage = 'daum'
  and phone_status = 'error';

create index if not exists
    idx_oasis_employment_contacts_phone_daum_processing
on public.oasis_employment_contacts (updated_at, contact_key)
where phone_provider_stage = 'daum'
  and phone_status = 'processing';
