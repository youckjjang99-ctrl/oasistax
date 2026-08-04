begin;

-- Maintaining idx_oasis_prospects_owner_normalized_business_no evaluates this
-- immutable helper on writes. The v911 migration made the helper private, but
-- also removed the service role's execute privilege, which blocked the
-- service-only assignment RPC with SQLSTATE 42501.
revoke all on function public.oasis_v911_normalize_business_no(text)
    from public, anon, authenticated, service_role;
grant execute on function public.oasis_v911_normalize_business_no(text)
    to service_role;

commit;
