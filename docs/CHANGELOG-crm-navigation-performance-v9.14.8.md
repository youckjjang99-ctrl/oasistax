# v9.14.8 — CRM navigation performance

Date: 2026-10-16 (Asia/Seoul)

Baseline: latest fetched GitHub main, `b2f5bff65d528c54f27e70258b42024b31c713d0`.
Branch: `codex/crm-navigation-performance-20261016` in the existing isolated managed worktree.
The original dirty project checkout and Git connection are preserved.

## 점검 결론

느린 원인은 디자인 자체보다 화면 전환에 연결된 전체 앱 재실행, 반복 네트워크 조회와 공개 정책자료의 불필요한 동기화 쓰기입니다. 캐시·증분 동기화·선택 화면만 실행하는 분기는 이미 있으므로 없다고 가정하여 교체하지 않았습니다.

## 이번 수정

1. 기업컨설팅의 8개 세부메뉴·본문·상태 표시를 하나의 Streamlit fragment로 분리했습니다. 세부메뉴 클릭은 사이드바와 기업 검색/선택 UI를 다시 만들지 않습니다. 기업 선택과 다른 주 메뉴로 이동은 기존 전체 전환을 유지합니다.
2. fragment 안에서도 로그인 사용자·역할을 재확인합니다. 사용자/역할 변경 시 전체 앱으로 복귀합니다. 소유자 범위 최신 고객 목록과 보관 상태를 다시 확인하며, 이름만으로 다른 업체를 연결하지 않습니다.
3. CRM 동기화는 fragment 내부로 옮겨 중복 실행을 막았습니다. 기존 60초 TTL·증분 커서·일일 전체 확인·동시 편집 보호를 유지합니다. 업체명이 없는 자료는 업체별 추가 조회 대신 100개 단위의 소유자 범위 일괄 조회로 보완합니다. 모호한 중복 키는 적용 전에 차단합니다.
4. 공개 정책자료는 UI 전용 읽기 경로로 분리했습니다. 정책자금 화면을 열거나 매칭 결과를 조회할 때 seed/공고 bulk upsert와 로컬 정책캐시 전체 rewrite를 하지 않습니다. 기존 명시적/예약 동기화 쓰기 경로는 유지합니다.
5. 공개 정책자료에 30초 캐시와 7개 원천파일 변경 지문을 적용했습니다. 파일 변경은 즉시 반영하고, 반환값은 복사하여 캐시 오염을 막습니다. 클라우드 실패는 캐시하지 않아 다음 호출에서 재시도하며 마지막 정상 공개자료와 로컬자료는 보존합니다.
6. 기업자료 현황은 기존 사용자·업체·세션별 8초 캐시를 사용하고 이미 읽은 문서 컨텍스트를 재사용합니다. 직원현황의 동일 명부 조회는 렌더당 2회에서 1회로 줄였습니다. 정책설정의 중복 읽기를 제거하되 상담 키워드·이력 자동 연동은 유지합니다.

## 세부메뉴 전체 검토

| 메뉴 | 이번 적용 | 남아 있는 지연 가능 경로 |
|---|---|---|
| 기업정보 | 부분 전환, 자료 현황 캐시, 문서 재사용 | 재무 스냅샷의 소유자/버전 확인 및 자료 현황 첫 조회 |
| 상담일지 | 부분 전환, 기존 편집 revision·메모 보호 | 녹음 상담일지 조회는 사용자 전체 자료에서 회사별로 거르는 경로가 남아 있음 |
| 정책자금 | UI 읽기/쓰기 분리, 공개자료 캐시, 설정 중복 읽기 제거 | 상담 맥락·이력 연동, 최초 공고 읽기 및 명시적 매칭 계산 |
| 주가평가 | 부분 전환 유지, 다른 세부메뉴 로직은 실행하지 않음 | 첫 진입의 재무·등기 복원과 호환 fallback |
| 정관검토 | 부분 전환, 기존 읽기 전용 동작 유지 | 실제 AI/OCR 분석은 버튼 이후 실행; 단순 진입이 자동 분석하지 않음 |
| 기업히스토리 | 부분 전환, 기존 이력 보존 | 클라우드 이력 병합과 로컬 원자 저장 |
| 직원현황 | 부분 전환, 같은 렌더의 명부 조회 2→1 | 기존 사용자 명부 목록 조회가 대량이면 추가 최적화 필요 |
| 가지급금 계산기 | 부분 전환, 정책설정 중복 읽기 제거 | 계산 전 상담 맥락 연동; 계산 자체는 로컬 처리 |

## 홈페이지 전체의 후속 개선 우선순위

1. 홈 업무함은 전체 목록을 합치기보다 오늘/지연 업무 상위 목록과 별도 요약 집계로 분리하고 서버 페이지네이션을 적용합니다.
2. 고객 선택 목록은 최소 필드 검색으로 슬림화하고 선택 업체의 원문만 조회하도록 단계적으로 전환합니다. 기존 Excel 호환·ID·중복 후보 보존을 유지해야 합니다.
3. 상담일지/직원명부/자료 현황은 소유자+업체 조건을 서버에서 적용하고, 현황판에는 건수·최근 날짜만 가져오도록 개선합니다. 레거시 이름 기반 연결을 잘못 합치지 않는 별도 호환 검증이 필요합니다.
4. 메뉴별 실제 사용자 클릭→화면 완료 시간(p50/p95), 요청 수, Railway CPU/RAM, Supabase 실행계획을 같은 시간대에 계측한 뒤 서버 증설·인덱스·리전 변경을 판단합니다.

이 후속 항목은 이번 패치에 구현하지 않았습니다. 서버 사양 부족, 리전 간 지연 또는 특정 SQL이 현재 메뉴 지연의 주원인이라고 단정할 실측 근거는 아직 없습니다. 확인한 DB 통계는 2026-08-11 이후 누적이며 현재 클릭 시간으로 해석하지 않았습니다. 중복 인덱스 추가나 유료 사양 변경은 하지 않았습니다.

## 검증

- 운영 고정 버전 Streamlit 1.59.1 및 pypdf 5.9.0 환경에서 관련 회귀 테스트 576개와 subtest 3개 통과.
- 합성 데이터 AppTest로 8개 세부메뉴 렌더와 AI 코파일럿 이동 검증.
- 실제 로컬 브라우저에서 기업정보→상담일지→정책자금→주가평가→정관검토→기업히스토리→직원현황→가지급금 계산기 전환: 전체 앱 실행 1회 유지, 세부화면 실행 1→8. AI 코파일럿 이동 시에만 전체 앱 실행 2회로 증가.
- 위 브라우저 검증은 합성 업체 및 대체된 외부 의존성으로 수행했습니다. 운영 사용자의 실제 지연 초수나 개선 배율을 측정한 결과는 아닙니다.
- 정책 UI 읽기: cold cloud select 1회, 30초 이내 warm select 0회, UI 경로 upsert/rewrite 0회 검증.
- 업체명 보완 205개는 합성 테스트에서 6회 요청으로 처리했습니다. 잘못된 소유자·응답·키·중복 키는 고객정보/체크포인트 적용 전에 차단합니다.
- 권한·동시 저장·기기 간 동기화·PDF 등록·스냅샷 보존·업로드 보안 회귀 포함.
- 개인정보/비밀키 유입 검사 및 diff 검사 수행. 실제 고객 원문·인증정보는 테스트/ZIP에 포함하지 않습니다.

## 변경 파일 / 덮어쓰기 패치 명세

- `VERSION.txt`
- `RUN_v9.14.8-crm-navigation-performance.bat`
- `app.py`
- `cloud_crm_restore.py`
- `cloud_db.py`
- `employee_status.py`
- `enterprise_center.py`
- `enterprise_consulting_engine.py`
- `enterprise_documents.py`
- `integrated_policy_repository.py`
- `multi_source_policy.py`
- `tests/test_crm_sync_name_batch.py`
- `tests/test_enterprise_navigation_performance.py`
- `tests/test_enterprise_submenu_performance.py`
- `tests/test_rerun_reductions.py`
- `docs/CHANGELOG-crm-navigation-performance-v9.14.8.md`

## 영향과 사용방법

Supabase 테이블·컬럼·인덱스·RLS·Storage·고객 ID·company_uid 변경이 없습니다. 마이그레이션 SQL은 필요하지 않습니다. 운영 SQL 쓰기·고객정보 수정·삭제·전화 발신·수집 실행은 하지 않았습니다. 전화번호 수집, 배정/중복연락 방지, 경정청구/Tilko, 회원가입/로그인 정책과 모바일/PC 사이드바 동작은 변경하지 않습니다.

사용자는 배포 후 기존과 같이 기업을 선택하고 8개 세부메뉴를 클릭하면 됩니다. 관리자·일반 사용자 모두 기존 권한을 유지합니다. 로그인/계정 변경이나 업체 접근 상태 변경은 즉시 무시하지 않고 재검사 또는 새로고침 안내를 표시합니다. 캐시를 권한 판단이나 저장 충돌 방지의 근거로 사용하지 않습니다.

현재는 로컬 수정·검증 완료 상태이며 GitHub push와 Railway 운영 배포는 실행하지 않았습니다. 승인 후 변경 파일만 업로드하고 운영 main/배포 SHA, 화면 스모크와 데이터 건수 보존을 다시 확인해야 합니다.

## 롤백

배포 전 16개 변경 파일을 별도로 보관합니다. 회귀 시 리뷰된 변경 커밋을 새 revert 커밋으로 되돌리고 운영 main에서 재배포합니다. 이 버전은 DB 변경이 없으므로 고객정보를 되돌리거나 삭제할 이유가 없습니다. 기준 버전은 `v9.14.7-cretop-header-recovery`, 기준 main은 위의 b2f5bff 커밋입니다.

## GitHub 업로드 명령 (아직 실행하지 않음)

원래 변경이 많은 체크아웃에서 `git add .`를 사용하지 마세요. 검토한 전용 브랜치에서 변경 파일만 스테이징합니다.

```bash
git status
git add VERSION.txt RUN_v9.14.8-crm-navigation-performance.bat app.py cloud_crm_restore.py cloud_db.py employee_status.py enterprise_center.py enterprise_consulting_engine.py enterprise_documents.py integrated_policy_repository.py multi_source_policy.py
git add tests/test_crm_sync_name_batch.py tests/test_enterprise_navigation_performance.py tests/test_enterprise_submenu_performance.py tests/test_rerun_reductions.py docs/CHANGELOG-crm-navigation-performance-v9.14.8.md
git commit -m "perf: reduce CRM submenu reruns and policy read overhead"
git push -u origin codex/crm-navigation-performance-20261016
```

公開された既存リポジトリへはコードだけをアップロードし、顧客資料や秘密キーを含めません。
