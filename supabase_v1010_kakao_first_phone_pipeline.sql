-- 전화번호 보강을 카카오 전체 선조회 후 네이버 미매칭 후조회로 분리한다.
alter table public.oasis_employment_contacts
    add column if not exists phone_provider_stage text
    not null default 'kakao';

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname =
            'oasis_employment_contacts_phone_provider_stage_check'
          and conrelid =
            'public.oasis_employment_contacts'::regclass
    ) then
        alter table public.oasis_employment_contacts
            add constraint
                oasis_employment_contacts_phone_provider_stage_check
            check (
                phone_provider_stage in (
                    'kakao',
                    'naver',
                    'complete'
                )
            ) not valid;
    end if;
end
$$;

-- 이미 전화번호 보강이 끝난 행은 재조회하지 않는다.
update public.oasis_employment_contacts
set phone_provider_stage = 'complete'
where phone_status in ('matched', 'no_match');

-- 이전 실행이 비정상 종료된 처리 중 행은 카카오 단계부터 안전하게 재개한다.
update public.oasis_employment_contacts
set
    phone_status = 'pending',
    phone_provider_stage = 'kakao',
    phone_last_error = ''
where phone_status = 'processing';

-- 실제 작업 큐 조건에 맞춘 작은 부분 인덱스만 유지한다.
create index if not exists
    idx_oasis_employment_contacts_phone_kakao_queue
on public.oasis_employment_contacts (
    phone_status,
    phone_next_check_at,
    updated_at,
    created_at,
    contact_key
)
where phone_provider_stage = 'kakao'
  and phone_status in ('pending', 'error', 'processing');

create index if not exists
    idx_oasis_employment_contacts_phone_naver_queue
on public.oasis_employment_contacts (
    phone_status,
    phone_next_check_at,
    updated_at,
    created_at,
    contact_key
)
where phone_provider_stage = 'naver'
  and phone_status in ('pending', 'error', 'processing');

drop index if exists
    public.idx_oasis_employment_contacts_phone_pending;
drop index if exists
    public.idx_oasis_employment_contacts_phone_due;
drop index if exists
    public.idx_oasis_employment_contacts_phone_processing;

alter table public.oasis_employment_contacts
    validate constraint
        oasis_employment_contacts_phone_provider_stage_check;
