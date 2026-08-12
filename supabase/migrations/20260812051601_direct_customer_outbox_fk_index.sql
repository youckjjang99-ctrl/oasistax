create index if not exists idx_oasis_direct_outreach_customer_fk
    on public.oasis_direct_customer_outreach_outbox (direct_customer_id);
