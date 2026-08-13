begin;

-- The 2026-08-13 Daum pass used the former landline-biased query. Requeue
-- only those same-day completed rows that still have no mobile phone so the
-- mobile-first matcher can retry them. Existing phone values, sources and
-- attempt history are intentionally preserved.
update public.oasis_employment_contacts
set
    phone_status = 'pending',
    phone_provider_stage = 'daum',
    phone_next_check_at = clock_timestamp(),
    phone_last_error = '',
    updated_at = clock_timestamp()
where phone_provider_stage = 'complete'
  and not coalesce(has_mobile_phone, false)
  and phone_checked_at >= timestamptz '2026-08-13 00:00:00+09'
  and phone_checked_at < timestamptz '2026-08-14 00:00:00+09';

commit;
