# v9.14.3 — AI 방문상담 관리자 전용 전체 업체 목록

작성일: 2026-10-06. 기준 커밋: `b9eb3eaa3754ace9ff53762c4939c46fab599d98` (`origin/main`). 기존 관리형 작업 폴더와 Git 연결을 유지했습니다.

## 변경 요약

- 관리자만 AI 방문상담 메뉴·화면·서버 조회·캠페인 작업을 사용할 수 있습니다.
- 담당 영업DB만 보이던 목록을 전체 연락처 원천과 원천에 없는 기존 영업후보로 확대했습니다.
- 업체명, 개인/법인 구분, 지역·주소, 업종, 고용인원·증감·비교기간, 발굴유형, 휴대폰·일반전화 현황을 표시합니다.
- 개인/법인/미확인, 휴대폰/일반전화/모두/번호 없음, 지역, 업종, 발굴유형, 업체명 서버 필터를 추가했습니다.
- 고정 키 커서로 한 페이지 최대 100개를 조회합니다. 이름 검색을 현재 100개에만 적용하던 문제를 수정했습니다.
- 현재 페이지 전체선택은 기존 선택을 새 최대 100개로 교체합니다. 같은 연결 업체가 여러 원천 행에 있어도 캠페인에는 UID 기준으로 한 번만 접수합니다.
- 원천만 있는 업체도 조회·선택은 가능하지만 자동 배정·동의 등록·발신은 하지 않습니다. 발신 가능 업체 수와 제외 사유를 표시합니다.
- 같은 장소·원천키라도 사업자번호가 충돌하면 발신 대상으로 연결하지 않습니다. 불명확한 UID, 빈 원천키끼리의 연결도 차단합니다.
- 동의·수신거부는 실제 발신번호 기준으로 표시하며 미확인은 거부 없음으로 표시하지 않습니다.
- 권한 변경·검색 오류 시 이전 선택을 폐기합니다. 자동 새로고침 중 검색이 실패하면 반복 조회를 중단하고 수동 복구를 제공합니다.

## Supabase 변경

마이그레이션: `supabase/migrations/20261006093201_ai_visit_calling_admin_catalog.sql` (Supabase CLI로 생성).

- 기존 `oasis_voice_action(text,text,jsonb)`와 `oasis_voice_campaign_action(text,text,jsonb)`의 원래 본문을 보존하면서 첫 실행 지점에 현재 활성 관리자 검사를 삽입합니다. 재실행 시 중복 삽입하지 않으며 우회용 legacy 함수를 만들지 않습니다.
- 새 읽기 전용 `oasis_voice_catalog(text,jsonb)`와 사업자·발굴유형 표시 보조 함수 2개를 추가합니다.
- 기존 테이블/컬럼/인덱스/고객 ID/company_uid를 변경하지 않습니다. 대량 데이터 복사·갱신·삭제가 없습니다.
- 새 함수는 SECURITY INVOKER 및 빈 search_path를 사용합니다. PUBLIC/anon/authenticated 실행 권한을 회수하고 service_role만 허용합니다. 기존 서버 전용 RLS 구조를 유지합니다.
- 원천 연락처 상태·전화번호 수집 대기열·배정·동의·worker 정의는 변경하지 않습니다.
- 신규 대형 인덱스를 자동 생성하지 않습니다. 별도 수동 튜닝 예시는 실행되지 않는 주석 SQL로 제공합니다.

## 검증 결과

실제 고객 자료를 테스트 입력으로 복사하거나 유료 통화를 발신하지 않았습니다.

| 검증 | 결과 |
|---|---|
| Python 단위·회귀 테스트 | **647개 통과**. AI 상담 UI/저장소/게이트웨이/worker, 메뉴 권한, 계정 보안, 배정, CRM 동시 저장·기기 동기화, 전화번호 수집 회귀 포함 |
| 기존 통화 SQL 통합 테스트 | **133개 통과** (로컬 PGlite, 합성 자료) |
| 기존 캠페인 SQL 통합 테스트 | **84개 통과** (로컬 PGlite, 합성 자료) |
| 신규 전체 업체 목록 SQL 통합 테스트 | **187개 통과**. 새 마이그레이션 두 번 적용, 권한, 100개 커서 페이지, 필터, 동일 업체 연결 충돌, 원천 보존 포함 |
| 브라우저 화면 점검 | 합성 업체 230개로 PC/390px 모바일, 두 페이지 전체선택 교체, 개인/법인·전화 유형 필터 확인 |
| 독립 검토 | 별도 검토자가 신규 SQL 187개 재확인. worker/gateway/전화번호 수집 코드 변경 없음 확인 |
| 개인정보·파일 검사 | 변경 소스 19개 개인정보 검사 및 `git diff --check` 통과. 고객 파일·비밀키는 패치에 포함하지 않음 |

Python 실행의 397개 경고는 기존 의존성의 폐기 예정 API 경고이며 테스트 실패는 없습니다. 브라우저 점검은 앞선 UI 상태의 시각 확인이고, 마지막 필터 제출·오류 자동갱신 보완은 최종 647개 테스트에 포함해 검증했습니다.

`tests/voice_calling_catalog_staging.verify.sql`은 빈 검증용 스키마 전용이며 전체 실행을 ROLLBACK합니다. 10월 6일에는 준비만 했고, **10월 7일 격리 Supabase에서 38개 검증을 실제 통과**했습니다. 세부 결과는 아래 추가 검증 기록을 참고합니다. SQL 검증을 운영 배포·REST 검증 완료로 간주하지 않습니다.

### 성능 확인의 범위

운영 원천에 읽기 전용 `EXPLAIN ANALYZE`로 **원천 101행 페이지 선택 부분만** 확인했습니다.

| 조건 | 관측 실행시간 | 비고 |
|---|---:|---|
| 기본 전체 | 약 55ms | 기존 기본키 인덱스 |
| 법인 분류 | 약 1,229ms | 기존 기본키, 제외 행 추가 검사 |
| 개인 분류 | 약 1ms 미만 | 기존 기본키 |
| 휴대폰 보유 | 약 4ms | 기존 휴대폰 부분 인덱스 |

이는 전체 RPC·연결정보 보강·네트워크·화면 렌더링을 포함한 응답시간이나 성능 보장값이 아닙니다. 캐시·서버 부하·검색 조건에 따라 달라집니다. 없는 업체명과 지역/전화 조건 조합은 실행 없이 계획만 확인했고, 많은 후보 재검사 가능성이 남아 있습니다. 희귀 포함검색은 추가 인덱스 없이 항상 빠르다고 보장할 수 없습니다.

함수에 RPC 시간제한을 지정했지만 실제 PostgREST 설정과 호출 환경에서의 제한 동작은 격리 배포 검증에서 별도로 확인해야 합니다. 로컬 PGlite 시험을 운영 REST 시간제한 검증으로 간주하지 않습니다.

## 최초 패치 준비 시점의 운영 영향·미실행 항목

- 이번 작업에서는 운영 DB 마이그레이션, GitHub push, Railway 배포를 하지 않았습니다. 따라서 현재 운영 화면이 이 버전으로 바뀌었다고 안내하면 안 됩니다.
- 유료 임시 DB 생성, 회선 구매, 구독 변경, 실제 전화, 유료 OpenAI 음성 세션을 실행하지 않았습니다.
- 기존 일반 계정의 DB발굴/내 영업DB/CRM 권한은 유지합니다. AI 방문상담에 한해서 관리자 전용으로 변경합니다.
- 전화서비스 키·OpenAI 키·실제 발신 플래그·하루 한도는 변경하지 않습니다.
- 실행 중 통화를 강제로 끊는 기능은 추가하지 않았습니다. worker/gateway는 기존 동작을 유지합니다.
- 새 SQL의 실제 격리 Supabase 적용·두 번 재적용·PostgREST 통합 시험은 운영 배포 전에 필요합니다. 별도 비용이 필요한 환경은 다시 승인받습니다.

### 2026-10-07 배포 준비 및 격리 Supabase 검증

- 사용자 배포 승인 후 최신 `origin/main`을 다시 확인했습니다. 기준 커밋과 동일하며 충돌이 없습니다.
- Python 회귀 **647개 재통과**, 독립 SQL **187개 재통과**, UI/메뉴 **48개 재통과**했습니다.
- 이번 검증용으로 시간당 $0.01344, 최대 $0.10 및 검증 후 삭제를 별도 승인받았습니다. 데이터 없는 임시 브랜치를 만들었습니다.
- 과거 초기 스키마 생성 이력 누락 때문에 자동 마이그레이션 재생이 실패했습니다. 운영 스키마 메타데이터만 별도 검증 환경에 재현하고, 운영과 91개 테이블·1,268개 컬럼·201개 함수·269개 보조 인덱스·498개 제약조건이 일치하는지 확인했습니다. 운영 고객 행, Storage 파일, 비밀값, cron 작업 데이터는 복사하지 않았습니다.
- 새 catalog 마이그레이션 **두 번 적용 성공**, 호스팅 PostgreSQL에서 합성 205행의 3페이지·권한·데이터 보존 등 **38개 검증 PASS**, 추가 ACL/RLS **81개 검사 PASS**를 확인했습니다.
- 검증 트랜잭션을 롤백하여 users/source/prospects/assignments/permissions/jobs/events/campaigns에 잔여 합성 데이터가 0건임을 확인했습니다. worker/target 함수는 운영 정의와 동일합니다.
- 보안 Advisor WARN/ERROR는 없었습니다. 서버 전용 RLS 기본 거부 테이블의 INFO는 별도입니다. [관련 알림 설명](https://supabase.com/docs/guides/database/database-linter?lint=0008_rls_enabled_no_policy).
- 실제 PostgREST 시간제한 probe는 로컬 HTTP 네트워크 예외로 검증하지 못했습니다. 임시 probe 함수를 제거하고 권한을 재검사했습니다. 실제 REST 15초 제한과 전체 운영 규모의 RPC 성능은 확인 필요 항목입니다.
- **임시 브랜치를 삭제 완료**했고 브랜치 목록에 운영 main만 남은 것을 확인했습니다. 운영 DB 쓰기·외부 발신은 하지 않았습니다.
- Railway 운영 CRM의 CALLS_ENABLED/BILLING_CONFIRMED/LIVE_VERIFIED는 모두 false, 일 한도 1, provider clawops를 읽기 전용으로 확인했습니다. 별도 AI 발신 worker/gateway 서비스는 없습니다.
- 기존 원천 수집 서비스가 실행 중이고 main 자동배포가 연결되어 있어 main push는 보류했습니다. 전화번호 수집 서비스 두 개에는 이번 배포 전부터 최근 실패 상태가 표시되어 별도 점검이 필요합니다. 이번 패치로 수집 코드나 설정을 바꾸지 않았습니다.
- 처음에는 사용자 요청에 따라 Railway CLI 로그인을 중단했습니다. 이후 사용자가 인증 창 재실행을 요청하고 직접 인증을 완료했습니다.

### 2026-10-07 CRM 단독 운영 반영

- 최신 main과 검증된 SQL의 동일성을 다시 확인한 뒤 운영 `ai_visit_calling_admin_catalog` 마이그레이션을 적용했습니다. 고객 테이블의 행·컬럼·인덱스는 변경하지 않았습니다.
- 운영 service_role 읽기 전용 검사: 일반 계정의 catalog/action/campaign 접근이 모두 `NOT_AUTHORIZED`, 관리자 첫 두 페이지 각각 100개·중복 0개, 원문 연락처 필드 노출 0개를 확인했습니다.
- 새 함수와 변경 함수 5개 모두 SECURITY INVOKER, anon/authenticated 실행 불가, service_role 실행 가능을 확인했습니다.
- 실행 중 수집 작업 보호를 위해 main push/PR 병합은 하지 않고, CRM 서비스 ID를 지정한 CLI 단독 배포를 사용합니다. GitHub 변경본은 별도 브랜치/PR로 보존하며 main 반영은 후속 조치입니다. main에서 다시 배포하면 이전 UI로 되돌아갈 수 있으므로 후속 main 배포 전 PR 반영이 필요합니다.
- 배포 묶음에는 Git 제외 규칙에 해당하는 과거 추적 고객 파일과 로컬 비밀값을 포함하지 않습니다. 공개 정책 데이터, 빈 템플릿, `railpack.json`은 유지합니다.
- 코드 배포 성공 여부와 최종 운영 스모크 결과는 별도 완료 기록으로 확인합니다. 인증 성공이나 SQL 적용만으로 코드 배포 완료를 표시하지 않습니다.

## 사용·롤백·파일 목록

- [관리자 사용 및 롤백 안내](ai-visit-calling-admin-catalog-v9.14.3.md)
- [변경 파일 전체 목록](ai-visit-calling-admin-files-v9.14.3.md)

원천정보·배정·통화 이력을 삭제하지 않습니다. 문제 발생 시 발신 OFF를 유지하고 코드만 복구하되 관리자 전용 서버 제한을 다시 열지 않습니다.

## 승인 후 GitHub 반영

이 명령은 안내이며 이번 턴에서 실행하지 않았습니다. 변경 파일만 있는 전용 작업 폴더에서 진행합니다. 고객 자료와 무관한 변경이 있는 원래 작업 폴더에서 무조건 `git add .`를 실행하지 않습니다.

```powershell
git status
git diff --check
git add VERSION.txt app.py voice_calling_ui.py voice_calling_dashboard.py voice_calling_repository.py RUN_v9.14.3.bat
git add supabase/migrations/20261006093201_ai_visit_calling_admin_catalog.sql
git add tests/test_navigation_visibility.py tests/test_voice_calling.py tests/test_voice_calling_dashboard.py tests/test_voice_calling_ui.py tests/test_voice_calling_ui_render.py tests/voice_calling_catalog.integration.mjs tests/voice_calling_catalog_staging.verify.sql
git add docs/ai-visit-calling-admin-catalog-v9.14.3.md docs/ai-visit-calling-admin-files-v9.14.3.md docs/CHANGELOG-ai-visit-calling-admin-v9.14.3.md docs/voice_calling_catalog_optional_indexes.sql tools/build_ai_calling_admin_patch.py
python tools/privacy_guard.py --staged
git commit -m "feat: restrict AI calling to admins and add paged company catalog"
git push origin codex/ai-visit-calling
```

승인 후 최신 main과 충돌·CI를 확인하고 PR 병합으로 운영 반영합니다. SQL과 코드 배포 순서·격리 검증·운영 스모크 테스트가 끝나기 전에는 발신을 활성화하지 않습니다.
