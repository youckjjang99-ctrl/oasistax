-- Keep provider queue lookups fast while the contact table grows.
create index if not exists
    idx_oasis_employment_contacts_phone_kakao_pending
on public.oasis_employment_contacts (created_at, contact_key)
where phone_provider_stage = 'kakao'
  and phone_status = 'pending';

create index if not exists
    idx_oasis_employment_contacts_phone_naver_pending
on public.oasis_employment_contacts (created_at, contact_key)
where phone_provider_stage = 'naver'
  and phone_status = 'pending';

create index if not exists
    idx_oasis_employment_contacts_phone_kakao_due
on public.oasis_employment_contacts (
    phone_next_check_at,
    updated_at,
    contact_key
)
where phone_provider_stage = 'kakao'
  and phone_status = 'error';

create index if not exists
    idx_oasis_employment_contacts_phone_naver_due
on public.oasis_employment_contacts (
    phone_next_check_at,
    updated_at,
    contact_key
)
where phone_provider_stage = 'naver'
  and phone_status = 'error';

create index if not exists
    idx_oasis_employment_contacts_phone_kakao_processing
on public.oasis_employment_contacts (updated_at, contact_key)
where phone_provider_stage = 'kakao'
  and phone_status = 'processing';

create index if not exists
    idx_oasis_employment_contacts_phone_naver_processing
on public.oasis_employment_contacts (updated_at, contact_key)
where phone_provider_stage = 'naver'
  and phone_status = 'processing';

drop index if exists
    public.idx_oasis_employment_contacts_phone_kakao_queue;
drop index if exists
    public.idx_oasis_employment_contacts_phone_naver_queue;
