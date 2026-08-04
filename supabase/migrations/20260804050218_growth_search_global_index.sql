-- 지역을 선택하지 않은 성장기업 조회의 정렬과 LIMIT을 지원한다.
-- Supabase 마이그레이션 트랜잭션과 호환되도록 일반 인덱스로 생성한다.
create index if not exists idx_oasis_employment_contacts_global_growth
    on public.oasis_employment_contacts (
        employee_growth desc,
        current_employee_count desc
    )
    where employee_growth > 0;

comment on index public.idx_oasis_employment_contacts_global_growth is
    '전 지역 고용증가기업 조회의 증가폭·현재인원 정렬 최적화';
