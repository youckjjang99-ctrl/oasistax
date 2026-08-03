# OASIS CRM 1단계 검수 보고서

- 적용 버전: `v9.9.0-data-safety`
- 검수 기준 커밋: `7c904ddcf5afb58af1201d2f43651e7f4b6126b3`
- 검수 범위: 데이터 유실·보안 위험 제거
- 운영 반영 상태: 미반영
- GitHub push / Railway 배포: 미수행
- 신규 기능 플래그: 모두 기본값 `OFF`

## 1. 결과 요약

1단계 로컬 패치와 멱등 마이그레이션 초안을 작성하고 정적·회귀 검사를 완료했다. 고객 원본정보를 물리 삭제하지 않고 보관·비활성 상태로 전환하는 구조, 영속 동기화 대기열, 비공개 고객파일 메타데이터, 백업·복구 증거, 개인정보 유입 방지 검사를 추가했다.

운영 Supabase에는 마이그레이션을 적용하지 않았고, 고객 파일·Storage 객체·Git 이력을 삭제하거나 변경하지 않았다. 격리 Supabase에서는 최신 마이그레이션 2회 적용과 합성 Database 논리 백업·복구 훈련을 완료했다. 실제 운영 Database 물리 백업, Auth 설정, Storage 객체 바이트 복구는 운영 승인 후 별도 실행기가 필요한 범위로 남아 있다.

## 2. 핵심 변경

- Git에 새 개인정보·비밀정보가 들어오는 것을 차단하는 Privacy Guard와 CI 추가
- 로컬 동기화 실패 대기열을 보존하면서 Supabase durable outbox로 전환할 수 있는 기능 플래그 경로 추가
- 지수형 재시도, dead-letter, 임대 토큰, 수동 재처리 및 이력 보존 구조 추가
- 고객 파일을 비공개 Storage에 저장하고 짧은 유효기간 signed URL로만 내려받는 경로 추가
- 동일 파일 하나를 여러 고객·출처에 논리 연결하는 `oasis_customer_asset_links` 추가
- Storage 업로드 뒤 메타데이터/연결 저장이 실패하면 원본과 Storage 객체를 보존하고 멱등 복구 작업 등록
- 상담 녹취 물리 삭제 차단, 보관 상태 전환, 소유자 범위 조회·연결·서명 강제
- 고객 삭제 대신 아카이브·재활성화 RPC 및 변경 이력 구조 추가
- 고객 결과파일 충돌 시 덮어쓰기·삭제 없이 별도 보관
- AI 코파일럿 메모·성공사례·체크리스트의 선택적 클라우드 이중저장 경로 추가
- 관리자 화면에 동기화 대기열, 백업·복구 증거, 고객 아카이브 현황 표시 경로 추가
- 원시 예외와 개인정보를 화면·세션·오류 요약에 노출하지 않도록 공용 마스킹 경로 적용
- 공백이 있는 고객명·주소와 Bearer 자격증명 전체를 제거하도록 Python·SQL 오류 마스킹 보강
- 고객 보관·재활성화 RPC에 소유자+멱등 키 트랜잭션 잠금을 적용해 동시 중복·상충 처리를 직렬화
- 복합 외래키 10개에 소유자 범위 covering index를 추가해 장기 보존 데이터의 조인·참조검사 비용 보완

## 3. Supabase 변경 초안

### 기존 테이블에 추가하는 컬럼

- `oasis_customers`: `lifecycle_status`, `archived_at`, `archived_by_user_id`, `archive_reason`, `retention_class`, `merged_into_customer_id`
- `oasis_consultation_audio`: `status`, `archived_at`, `archived_by`, `archive_reason`

### 신규 테이블

- `oasis_sync_outbox`
- `oasis_sync_outbox_events`
- `oasis_customer_assets`
- `oasis_customer_asset_links`
- `oasis_copilot_assets`
- `oasis_copilot_company_memory`
- `oasis_copilot_success_cases`
- `oasis_copilot_checklists`
- `oasis_backup_runs`
- `oasis_restore_drills`
- `oasis_customer_archive_events`

### 권한 원칙

- 모든 신규 public 테이블에 RLS와 FORCE RLS 적용
- `PUBLIC`, `anon`, `authenticated` 직접 접근 차단
- Supabase 기본 ACL로 생길 수 있는 `service_role` 권한을 먼저 전부 회수한 뒤 필요한 최소 권한만 재부여
- outbox 상태 변경과 고객 아카이브·재활성화는 제한된 SECURITY DEFINER RPC로 처리
- RPC의 `search_path` 고정 및 직접 실행 권한 최소화
- `DELETE`, `TRUNCATE`, `DROP` 기반 데이터 파기 구문 없음

마이그레이션 파일: `supabase/migrations/20260803080418_v990_data_safety.sql`

## 4. 기능 플래그와 무효화 방식

다음 플래그는 기본값이 모두 `OFF`다.

- `OASIS_DATA_SAFETY_V1`
- `OASIS_DURABLE_OUTBOX_V1`
- `OASIS_PRIVATE_ASSETS_V1`
- `OASIS_CLOUD_COPILOT_V1`

따라서 운영 마이그레이션과 검증이 끝나기 전에는 기존 화면·저장 경로가 유지된다. 활성화는 마이그레이션 검증 후 기능별로 순차 진행해야 한다.

## 5. 수정 파일

### 보안·저장·동기화

- `.gitignore`
- `.github/workflows/privacy_guard.yml`
- `tools/privacy_guard.py`
- `runtime_error_log.py`
- `cloud_db.py`
- `cloud_sync.py`
- `sync_outbox.py`
- `data_safety_storage.py`
- `customer_asset_storage.py`
- `customer_lifecycle.py`
- `data_safety_admin.py`
- `utils.py`
- `maintenance.py`

### 기존 기능의 비파괴 저장 보완

- `app.py`
- `articles_review.py`
- `claim_correction_repository.py`
- `cloud_admin.py`
- `consultation_audio_storage.py`
- `consultation_journal.py`
- `consulting_copilot.py`
- `consulting_report.py`
- `customer_history.py`
- `prospect_db_center.py`
- `stock_valuation.py`
- `temporary_advance_ui.py`

### 버전·문서·마이그레이션

- `VERSION.txt`
- `CHANGELOG_v9.9.0.md`
- `BACKUP_RECOVERY_v9.9.0.md`
- `STAGE1_ACCEPTANCE_REPORT_v9.9.0.md`
- `supabase/migrations/20260803080418_v990_data_safety.sql`

### 테스트

- `tests/test_claim_correction.py`
- `tests/test_consultation_audio_owner_scope.py`
- `tests/test_customer_asset_storage.py`
- `tests/test_customer_lifecycle.py`
- `tests/test_customer_retention_guards.py`
- `tests/test_data_safety_admin.py`
- `tests/test_data_safety_storage.py`
- `tests/test_matching_runtime_isolation.py`
- `tests/test_privacy_guard.py`
- `tests/test_runtime_error_log.py`
- `tests/test_stage1_ui_redaction.py`
- `tests/test_sync_outbox.py`
- `tests/test_v990_data_safety_migration.py`

## 6. 검증 결과

- 1단계 지정 회귀검사: `115 passed`
- SQL·오류 마스킹·outbox 집중검사: `41 passed`
- 전체 회귀검사: `541 passed`, `1 failed`, `1 skipped`, `21 subtests passed`
- Python 구문검사: 통과
- `git diff --check`: 통과
- 추적 중인 고객·자료 Excel 6개: HEAD와 `6/6` 바이트 동일
- 삭제·이름변경된 추적 파일: `0개`
- HEAD와 `origin/main`: 동일
- 운영 Supabase 쓰기, commit, push, Railway 배포: 모두 미수행
- 격리 Supabase 최신 마이그레이션 연속 2회: 통과
- 최종 감사 보완 SQL 연속 2회 추가 재적용: 통과
- 격리 스키마 검증: 신규 테이블 11개, FORCE RLS 11개, 제한 RPC 9개, 복합 FK 인덱스 10개, 브라우저 권한 0개
- 합성 고객/녹취 메타데이터 보존: `2건/1건`, 양쪽 기준 해시 동일
- outbox 멱등성·임대·실패·수동재처리·완료 전이: 통과
- 고객 아카이브·재활성화 2세션 경합·교차동작·무변경 감사 멱등성: 통과
- 합성 Database 논리 백업·삭제·복원: 통과

전체 회귀의 실패 1건은 변경하지 않은 `claim_correction_center.py`의 `st.rerun()` 실제 4개와 테스트 기대값 2개의 차이다. 이번 단계의 금지 범위인 경정청구 API·인증·수집 흐름을 변경하지 않았으며, 해당 파일도 수정하지 않았다.

작업 트리 Privacy Guard는 사용자 소유 미추적 파일 2개를 명시적으로 제외한 1단계 변경 파일 43개를 검사해 통과했다. 전체 작업 트리 검사에서는 별도 사용자 소유 미추적 테스트 파일의 기존 형식화 fixture만 감지했다. 해당 사용자 파일은 수정·스테이징하지 않았다.

## 7. 데이터 보존 확인

- 고객 원본정보 자동 파기 없음
- 고객 화면 삭제는 아카이브·비활성 상태 전환으로 설계
- 녹취 원본 물리 삭제 차단
- 파일 업로드 후 메타데이터 실패 시 원본과 Storage 객체 보존
- 중복 파일은 물리 삭제하지 않고 논리 연결
- 실패 작업과 재처리 이력 보존
- 기존 고객 ID와 원천 레코드 변경 없음
- 경정청구 문서목록·Tilko endpoint·인증 절차·수집 기간 로직 변경 없음

## 8. 알려진 제한사항과 다음 승인 항목

1. 격리 Supabase의 실제 PostgreSQL에서 최신 마이그레이션 최초 실행과 재실행을 완료했고, 멱등성·RLS·RPC 권한을 확인했다.
2. 합성 Database 행의 논리 백업·삭제·복원 훈련은 통과했다. 실제 운영 Database 물리 백업 파일, Auth 설정, Storage 객체 바이트 백업·복원은 아직 수행하지 않았다.
3. 저장소에 운영 기본 스키마 전체를 재구성하는 baseline migration이 없어, 빈 preview branch의 자동 복구는 실패한다. 운영 적용 전에 별도의 baseline 정비 계획이 필요하다.
4. 동시 최초 업로드 시 동일 체크섬 Storage 고아 객체가 생길 가능성은 남아 있어 운영 활성화 전 동시성 시험이 필요하다. 데이터나 고객 연결이 덮어써지는 구조는 아니다.
5. Railway의 사용자 인증 구조상 신규 테이블의 브라우저 RLS는 기본 차단이며, 실제 소유자 격리는 서버 쿼리의 소유자 조건과 제한 RPC를 함께 사용한다. 운영 점검에서 교차 사용자 접근 테스트가 필수다.
6. 기존 고객 테이블 인덱스 생성은 트랜잭션 내부의 일반 인덱스 생성이므로 데이터 규모에 따라 잠금 시간이 생길 수 있다. 운영 전 격리 복제본에서 실행시간을 측정해야 한다.
7. 동일 이름의 비호환 테이블·함수·제약조건이 이미 존재하면 `IF NOT EXISTS`만으로 구조가 교정되지 않는다. 적용 전 카탈로그 사전검사를 수행해야 한다.
8. 운영 적용은 다음 순서의 별도 승인이 필요하다.
   - 운영 Supabase 마이그레이션
   - 기능 플래그 순차 활성화
   - GitHub push 및 Railway 배포

## 9. 롤백

- 현재는 운영 미반영이므로 운영 롤백 작업이 없다.
- 배포 후 기능 이상 시 우선 신규 기능 플래그 4종을 `OFF`로 되돌려 기존 경로를 유지한다.
- 추가 테이블과 컬럼은 고객정보 장기보존 원칙상 자동 삭제하지 않는다.
- 코드 롤백이 필요하면 배포 직전 커밋으로 애플리케이션만 되돌리고, 신규 데이터는 보존한 채 관리자 검토 후 후속 마이그레이션으로 처리한다.
