begin;

-- 관리자 승인 후 연결되는 배정/영업후보 FK 조회를 보조한다.
create index if not exists idx_oasis_specific_company_db_requests_assignment
    on public.oasis_specific_company_db_requests (assignment_id)
    where assignment_id is not null;

create index if not exists idx_oasis_specific_company_db_requests_prospect
    on public.oasis_specific_company_db_requests (prospect_id)
    where prospect_id is not null;

commit;
