-- 전화번호 수집과 이메일·인스타그램 수집을 독립 단계로 운영한다.
alter table public.oasis_employment_contacts
    add column if not exists phone_status text not null default 'pending',
    add column if not exists phone_checked_at timestamptz,
    add column if not exists phone_next_check_at timestamptz,
    add column if not exists phone_attempt_count integer not null default 0,
    add column if not exists phone_last_error text not null default '',
    add column if not exists digital_status text not null default 'pending',
    add column if not exists digital_checked_at timestamptz,
    add column if not exists digital_next_check_at timestamptz,
    add column if not exists digital_attempt_count integer not null default 0,
    add column if not exists digital_last_error text not null default '';

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'oasis_employment_contacts_phone_status_check'
    ) then
        alter table public.oasis_employment_contacts
            add constraint oasis_employment_contacts_phone_status_check
            check (
                phone_status in (
                    'pending',
                    'processing',
                    'matched',
                    'no_match',
                    'error'
                )
            ) not valid;
    end if;

    if not exists (
        select 1
        from pg_constraint
        where conname = 'oasis_employment_contacts_digital_status_check'
    ) then
        alter table public.oasis_employment_contacts
            add constraint oasis_employment_contacts_digital_status_check
            check (
                digital_status in (
                    'pending',
                    'processing',
                    'matched',
                    'no_match',
                    'error'
                )
            ) not valid;
    end if;

    if not exists (
        select 1
        from pg_constraint
        where conname = 'oasis_employment_contacts_phone_attempt_check'
    ) then
        alter table public.oasis_employment_contacts
            add constraint oasis_employment_contacts_phone_attempt_check
            check (phone_attempt_count >= 0) not valid;
    end if;

    if not exists (
        select 1
        from pg_constraint
        where conname = 'oasis_employment_contacts_digital_attempt_check'
    ) then
        alter table public.oasis_employment_contacts
            add constraint oasis_employment_contacts_digital_attempt_check
            check (digital_attempt_count >= 0) not valid;
    end if;
end
$$;

-- 이미 전화 보강을 실행한 소수의 행만 새 단계 상태로 이어받는다.
update public.oasis_employment_contacts
set
    phone_status = case
        when has_mobile_phone or has_landline_phone then 'matched'
        when status = 'processing' then 'pending'
        when status = 'error' then 'error'
        else 'no_match'
    end,
    phone_checked_at = checked_at,
    phone_next_check_at = next_check_at,
    phone_attempt_count = attempt_count,
    phone_last_error = last_error
where checked_at is not null
   or has_mobile_phone
   or has_landline_phone;

update public.oasis_employment_contacts
set
    digital_status = 'matched',
    digital_checked_at = checked_at,
    digital_next_check_at = next_check_at,
    digital_attempt_count = attempt_count,
    digital_last_error = last_error
where has_email or has_instagram;

create index if not exists
    idx_oasis_employment_contacts_phone_pending
on public.oasis_employment_contacts (created_at, contact_key)
where phone_status = 'pending';

create index if not exists
    idx_oasis_employment_contacts_phone_due
on public.oasis_employment_contacts (
    phone_status,
    phone_next_check_at,
    updated_at,
    contact_key
)
where phone_status in ('matched', 'no_match', 'error');

create index if not exists
    idx_oasis_employment_contacts_phone_processing
on public.oasis_employment_contacts (updated_at, contact_key)
where phone_status = 'processing';

create index if not exists
    idx_oasis_employment_contacts_digital_pending
on public.oasis_employment_contacts (created_at, contact_key)
where digital_status = 'pending';

create index if not exists
    idx_oasis_employment_contacts_digital_due
on public.oasis_employment_contacts (
    digital_status,
    digital_next_check_at,
    updated_at,
    contact_key
)
where digital_status in ('matched', 'no_match', 'error');

create index if not exists
    idx_oasis_employment_contacts_digital_processing
on public.oasis_employment_contacts (updated_at, contact_key)
where digital_status = 'processing';

alter table public.oasis_employment_contacts
    validate constraint oasis_employment_contacts_phone_status_check;
alter table public.oasis_employment_contacts
    validate constraint oasis_employment_contacts_digital_status_check;
alter table public.oasis_employment_contacts
    validate constraint oasis_employment_contacts_phone_attempt_check;
alter table public.oasis_employment_contacts
    validate constraint oasis_employment_contacts_digital_attempt_check;
