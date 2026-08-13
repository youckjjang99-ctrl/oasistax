-- 다음웹이 이어받은 기존 네이버 대기열까지 한 번에 빠르게 선택한다.
-- 실제 PostgREST 조회 조건과 동일하게 구성하며 업체/연락처 값은 변경하지 않는다.
create index if not exists
    idx_oasis_employment_contacts_phone_daum_legacy_queue
on public.oasis_employment_contacts (created_at, contact_key)
where phone_provider_stage in ('daum', 'naver')
    and phone_status = 'pending';

-- 초기 점검 중 생성된, 실제 조회문과 맞지 않는 인덱스는 제거한다.
drop index if exists
    public.idx_oasis_employment_contacts_phone_daum_legacy_pending;
