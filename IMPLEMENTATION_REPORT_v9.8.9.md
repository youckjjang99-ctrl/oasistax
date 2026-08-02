# OASIS CRM v9.8.9 구현 보고서

## 1. 업데이트 개요

- 애플리케이션 버전: **v9.8.9**
- Supabase 마이그레이션: **v10.3.2 company sales assignments**
- 대상 기능: **DB발굴 전사 공통 배정·연락상태 및 중복연락 방지**
- 적용 방식: 기존 프로젝트와 `.git` 연결을 유지하는 **덮어쓰기 패치**
- 데이터 원칙: 기존 영업후보, 검색이력, 메모, CRM, 상담일지 및 업체 ID를 삭제하거나 초기화하지 않는 additive migration

단순 검색과 상세 조회는 업체를 선점하지 않습니다. 사용자가 `내 영업DB에 담기`를 실행할 때만 전사 배정과 기존 영업DB 저장이 하나의 PostgreSQL RPC 트랜잭션에서 처리됩니다. 실제 연락결과를 저장하면 담당자가 확정되고 이후 다른 영업사원의 신규 검색 결과에서 제외됩니다.

기존 매일 연락처 보강 작업과의 호환을 위해 카카오·네이버 제공자 단계도 원자적 갱신 조건에 포함했습니다. 네이버 조회 중 일시 오류가 발생한 건은 완료로 오인하지 않고 네이버 재시도 대기열에 남겨, 연락처 확보 작업과 신규 전사 배정 기능이 함께 운용될 때 대기열이 유실되지 않도록 했습니다.

## 2. 기존 중복연락 원인

1. 기존 `oasis_prospect_companies.owner_user_id`는 사용자별 저장 소유권을 표현했지만, 동일 실체 업체의 전사 공통 담당 상태를 원자적으로 잠그는 장치가 아니었습니다.
2. 사용자별 검색이력·저장목록·메모는 존재했으나, 서로 다른 원천 데이터의 같은 업체를 하나로 묶는 안정적인 `company_uid`가 없었습니다.
3. 화면에서 중복 여부를 확인한 뒤 저장하는 사이에 다른 사용자가 먼저 저장할 수 있어 동시 요청 경쟁 조건이 있었습니다.
4. 조회와 저장의 의미가 분명히 분리되지 않은 경로에서는 단순 조회가 선점처럼 보이거나, 저장됐지만 전사 상태와 기존 영업DB가 잠시 불일치할 수 있었습니다.
5. 기존 상담일지와 저장후보가 여러 사용자에게 흩어져 있어 자동으로 한 사람을 선택하면 상담이력이나 담당 근거가 유실될 위험이 있었습니다.

## 3. 핵심 변경사항

### 3.1 업체 공통 식별키

애플리케이션과 SQL이 같은 우선순위로 `company_uid`를 생성합니다.

1. 10자리 사업자등록번호: `business:`
2. 13자리 법인등록번호: `corporate:`
3. 국민연금 사업장관리번호: `nps:`
4. 정규화된 업체명 + 주소 + 전화번호의 SHA-256: `fallback:`
5. 위 값이 모두 불가능할 때만 원천 + 원천키의 SHA-256: `source:`

업체명만으로 동일 업체를 판단하지 않습니다. 전화번호는 국가번호·공백·하이픈을 정규화하고, 업체명과 주소는 NFKC·공백·법인표기를 정리합니다. 이미 유효한 UID가 있는 업체는 연락처 보강만으로 UID가 바뀌지 않도록 고정합니다.

검색 후보는 `oasis_resolve_candidate_company_uids`가 최대 1,000건씩 서버에서 canonical UID로 해석합니다. 기존 source + source key가 가리키는 업체와 사업자번호·법인번호·국민연금 관리번호가 일치하면 기존 source identity를 채택하고, 둘 이상의 강한 식별값 또는 source와 강한 식별값이 충돌하면 해당 후보를 `unresolved`로 분리해 저장·배정을 차단합니다.

### 3.2 배정과 기존 영업DB 저장의 단일 원자적 처리

신규 저장 경로는 `oasis_claim_and_save_company_sales_assignment` RPC를 사용합니다.

- 사용자 권한과 `company_uid`를 서버에서 재검증합니다.
- UID 및 원천키에 advisory transaction lock을 적용합니다.
- 24시간 임시배정, 사용자별 미접촉 한도, 기존 담당자, 이관 충돌을 검사합니다.
- 배정 성공과 `oasis_prospect_companies`의 기존 영업DB 행 생성·갱신을 같은 PostgreSQL 트랜잭션 안에서 처리합니다.
- 기존 원천키가 다른 UID를 가리키면 `source_identity_conflict`로 거절합니다.
- 기존 source identity의 UID를 채택하기 전 강한 식별값 일치 여부를 다시 확인하고, 충돌 시 원자적 저장 RPC에서도 차단합니다.
- 어느 단계에서든 SQL 오류가 발생하면 배정과 기존 영업DB 저장이 함께 롤백됩니다.

따라서 구식인 “먼저 배정한 후 별도 REST 저장을 시도하고, 실패 시 보상 해제” 흐름은 사용하지 않습니다. 배치 저장은 업체별로 원자적 RPC를 호출하므로 일부 업체만 성공할 수 있으며, 각 업체 결과를 성공·기존 내 업체·충돌·한도초과로 구분해 표시합니다.

### 3.3 조회와 배정 분리

- 검색 결과 조회·상세 열람: `oasis_company_view_history`에 조회이력만 기록
- `내 영업DB에 담기`: 기본 24시간 임시배정 + 기존 영업DB 저장
- 실제 연락결과 저장: 담당 확정, 만료시간 제거, 최초·최근 연락일 및 연락횟수 갱신
- 본인에게 이미 배정된 업체: 중복 행을 만들지 않고 기존 저장목록에서 관리
- 다른 사용자에게 배정되거나 차단상태인 업체: 서버 RPC가 검색 결과에서 제외

### 3.4 검색 결과의 빠른 연락기록

검색 결과에서 업체 한 곳을 선택하면 `선택 업체 연락결과 바로 기록`을 사용할 수 있습니다.

1. 업체를 단일 원자적 RPC로 배정하고 기존 영업DB에 저장합니다.
2. 성공한 업체에 한해 `oasis_record_company_sales_contact`로 연락결과를 기록합니다.
3. 다른 사용자가 먼저 배정했다면 연락기록을 만들지 않고 충돌 메시지를 표시합니다.
4. 연락기록 저장이 일시적으로 실패해도 업체는 내 저장목록에 남아 있으므로 해당 화면에서 안전하게 재시도할 수 있습니다.

### 3.5 연락결과와 상태 매핑

| 연락결과 | 공통 상태·동작 |
|---|---|
| 부재중, 연결됨, 문자발송, 카카오톡 발송 | `contacted`, 연락시도 횟수와 최근 연락일 갱신 |
| 상담예약, 계약진행 | `consulting` |
| 재연락 요청 | `follow_up`, 다음 연락예정일 필수 |
| 관심없음 | `rejected`, 기본 180일 후 재활성화 가능 |
| 연락불가 | `unreachable`, 기본 30일 후 재활성화 가능 |
| 번호오류 | `wrong_number`, 유효 연락처 집합 변경 전까지 차단 |
| 기존거래처 | `contacted` |
| 계약완료 | `contracted` |

부재중·문자·카카오톡도 실제 연락시도로 집계합니다. 상담내용은 담당자와 관리자만 조회할 수 있습니다.

### 3.6 번호오류 업체의 안전한 재활성화

`wrong_number` 전환 시 정규화된 유효 전화번호 집합의 지문을 저장합니다. 만료정리 RPC는 현재 연락처 지문이 기존 지문과 실제로 달라졌을 때만 업체를 `unassigned`로 재활성화합니다.

- 하이픈, 공백, 국가번호 표기만 바뀐 경우 재활성화하지 않음
- 중복번호 정리만으로 재활성화하지 않음
- 새 유효 전화번호가 추가·변경된 경우에만 재활성화
- `wrong_number_reactivated_after_phone_change` 감사로그에 이전·신규 지문을 기록
- 기존/부분 적용된 번호오류 행은 마이그레이션에서 기준 지문을 먼저 설정
- `oasis_employment_contacts`에서 source record 또는 canonical UID로 연결되는 휴대전화·유선전화도 정규화해 지문에 포함

### 3.7 기존 상담이력의 실제 이관

`oasis_consultation_journals`는 단순 상태 참고가 아니라 실제 연락 증거로 이관합니다.

- 사업자등록번호를 `business:` UID로 정규화합니다.
- 단일 담당자의 상담일지는 해당 업체를 `consulting` 상태로 유지하고 담당자를 확정합니다.
- 각 상담일지를 `oasis_company_sales_contact_logs`에 `legacy_source_type = consultation_journal`로 저장합니다.
- `legacy_source_type + legacy_source_id` 고유 제약으로 재실행해도 같은 상담일지가 중복 생성되지 않습니다.
- 최초·최근 연락일, 연락횟수, 현재 배정 연락횟수를 이관된 로그 기준으로 집계합니다.
- 저장후보 담당자와 상담일지 담당자가 충돌하면 임의로 승자를 정하지 않고 관리자 충돌 목록에 보존합니다.

### 3.8 기존 중복 담당 충돌 UI

기존 저장행 또는 상담일지에서 동일 업체가 여러 사용자에게 연결된 경우 `oasis_company_assignment_conflicts`에 보존합니다.

- 원본 저장행, 메모, 상담일지를 삭제하지 않습니다.
- 충돌 업체는 일반 신규 배정을 차단합니다.
- 관리자 화면에 `담당자 확인 필요`, 기존 저장 사용자와 해결상태를 표시합니다.
- 관리자가 최종 담당자를 지정하면 기존 원본을 유지한 채 배정 및 기존 `owner_user_id`를 동기화하고 충돌을 해결합니다.
- 담당 변경자, 전·후 담당자, 사유와 시각은 감사로그에 남습니다.

### 3.9 관리자 서버 집계와 페이지 단위 조회

관리자 화면은 전체 업체를 한 번에 브라우저로 가져와 합산하지 않습니다.

- `oasis_list_company_assignment_admin_metrics`: 전체 데이터 기준 사용자별 미접촉·연락완료·장기 미처리·중복시도 및 전사 총계를 서버에서 집계
- `oasis_list_admin_company_assignments`: `LIMIT/OFFSET` 페이지 조회와 `count(*) over()` 총건수 제공
- UI 페이지 크기: 100 / 200 / 500건
- 현재 페이지의 담당자·상태·최초 조회자·최초 배정자·최초 연락자·연락일·만료일·충돌상태 표시
- 관리자 작업: 담당자 변경, 강제 해제·회수, 재활성화, 영구 제외, 사용자별 미접촉 한도 변경

대량 데이터에서도 관리자 목록 렌더링과 전사 통계가 페이지 크기에 비례하도록 분리했습니다.

## 4. Supabase 변경사항

### 기존 테이블 확장

`oasis_prospect_companies`에 다음 컬럼을 `ADD COLUMN IF NOT EXISTS`로 추가합니다.

- `company_uid text`
- `corporate_registration_no text`
- `nps_workplace_management_no text`

### 신규 테이블

- `oasis_company_sales_assignments`: 업체별 전사 공통 담당·상태·연락 집계
- `oasis_sales_assignment_settings`: 기본/사용자별 24시간·30건·재활성화 설정
- `oasis_user_prospect_notes`: `company_uid + user_id`별 비공개 메모
- `oasis_company_sales_contact_logs`: 구조화된 연락·상담이력과 기존 상담일지 원천키
- `oasis_company_view_history`: 단순 조회이력
- `oasis_company_assignment_audit_logs`: 배정·실패·만료·상태·관리자 변경 감사로그
- `oasis_company_assignment_conflicts`: 기존 다중 담당 충돌과 관리자 해결상태

### 주요 RPC

- 조회: `oasis_record_company_views`, `oasis_filter_blocked_company_uids`
- 원자적 저장: `oasis_claim_and_save_company_sales_assignment`
- 생명주기: `oasis_release_expired_company_assignments`, `oasis_release_company_sales_assignment`
- 연락: `oasis_record_company_sales_contact`, `oasis_list_company_sales_contacts`
- 사용자 목록: `oasis_list_user_company_assignments`
- 관리자 목록/집계: `oasis_list_admin_company_assignments`, `oasis_list_company_assignment_admin_metrics`
- 관리자 변경: `oasis_admin_change_company_assignee`, `oasis_admin_release_company_assignment`, `oasis_admin_reactivate_company_assignment`, `oasis_admin_permanent_exclude_company`, `oasis_admin_set_sales_user_limit`
- 감사: `oasis_list_company_assignment_audit`

기존 `oasis_claim_company_sales_assignment`은 내부 생명주기 및 호환 목적으로 유지되지만, 신규 “내 영업DB에 담기” 애플리케이션 경로는 원자적 claim-and-save RPC를 사용합니다.

## 5. RLS 및 권한

현재 OASIS CRM은 Supabase Auth의 `auth.uid()`가 아니라 기존 `oasis_users` 로그인과 Railway의 서버측 `service_role`을 사용합니다. 이 구조를 유지하면서 다음을 적용합니다.

1. 신규 7개 테이블에 RLS 활성화
2. `PUBLIC`, `anon`, `authenticated`의 신규 테이블 직접 권한 회수
3. 민감 RPC 실행권한을 `service_role`로 제한
4. 모든 RPC에서 승인 사용자 또는 관리자 권한을 서버에서 재검증
5. 고정 `search_path` 사용
6. 일반 사용자는 본인 배정·본인 메모·본인 담당 업체 연락이력만 조회
7. 다른 영업사원의 이름·메모·상담내용은 일반 검색 응답에 포함하지 않음
8. 필요한 신규 시퀀스에만 권한을 부여하며 `public` 전체 시퀀스 권한은 변경하지 않음

`service_role` 키는 Railway 서버 환경변수에만 보관해야 하며 브라우저, 로그, 패치 파일과 감사로그에 포함하면 안 됩니다.

## 6. 기존 데이터 마이그레이션

- 기존 업체 ID, 원천행, 검색이력, 메모, CRM, 상담일지를 삭제하지 않습니다.
- 사용자별 메모는 `company_uid + user_id` 단위로 병합 보존합니다.
- 담당자가 하나인 기존 저장업체는 기존 담당자와 상태를 유지하며 `legacy_hold = true`로 설정해 최초 이관 직후 24시간 만료시키지 않습니다.
- 기존 상태와 CRM 파이프라인을 신규 상태에 매핑합니다.
- 여러 사용자에게 저장된 업체는 `migration_conflict = true`로 표시하고 관리자 선택 전까지 신규 배정을 막습니다.
- 기존 상담일지는 구조화된 연락로그로 멱등 이관하고 담당·연락횟수·최초/최근 연락일을 복원합니다.
- `CREATE ... IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, `CREATE OR REPLACE FUNCTION`, 고유 원천키 및 충돌 안전 UPSERT를 사용합니다.

## 7. 수정 파일 전체 목록

| 파일 | 핵심 변경 |
|---|---|
| `VERSION.txt` | v9.8.9 적용 |
| `app.py` | 관리자 시스템 관리에 영업배정 관리 화면 연결 |
| `prospect_collection_service.py` | 전사 배정 가용성 필터와 기존 제외 흐름 호환 |
| `prospect_db_repository.py` | 업체별 원자적 claim-and-save 저장 및 결과 집계 |
| `prospect_db_center.py` | 조회/배정 분리, 빠른 연락기록, 저장목록 연락관리, 관리자 페이지·충돌 UI |
| `company_sales_assignment.py` | UID 정규화와 RPC 호출·응답 허용목록 계층 |
| `scheduled_employment_contact_enrichment.py` | 카카오·네이버 단계 원자적 선점 및 네이버 오류 재시도 대기열 보존 |
| `supabase_v1032_company_sales_assignments.sql` | 스키마, 마이그레이션, 원자적 RPC, 상담이력 이관, 관리자 집계, 감사, RLS |
| `supabase_v1032_company_sales_assignments_rls.sql` | 신규 객체 RLS·권한 재적용 SQL |
| `tests/test_company_sales_assignments.py` | UID·권한·RPC·응답 정제·연락·관리자 단위 테스트 |
| `tests/test_company_sales_assignment_integration.py` | 원자적 배정+기존 영업DB 저장 경로 모의 통합 테스트 |
| `tests/test_company_sales_assignment_migration.py` | 10개 요구 시나리오와 비파괴 이관 정적 계약 테스트 |
| `tests/test_scheduled_employment_contact_enrichment.py` | 제공자 단계 동시성 조건과 네이버 오류 재시도 회귀 테스트 |
| `RUN_v9.8.9.bat` | 패치 구성·문법·테스트 검증 실행 |
| `APPLY_UPDATE_v9.8.9.py` | 로컬 패치 검증기(운영 DB를 변경하지 않음) |
| `README_UPDATE_v9.8.9.md` | 적용·사용 안내 |
| `CHANGELOG_v9.8.9.md` | 변경이력 |
| `IMPLEMENTATION_REPORT_v9.8.9.md` | 구현·권한·검증 보고서 |
| `PATCH_MANIFEST_v9.8.9.txt` | 패치 ZIP 포함 목록 |
| `GITHUB_UPLOAD_COMMANDS_v9.8.9.txt` | GitHub 업로드 명령 |

## 8. 기존 기능 영향도

- DB발굴 검색조건, 사용자별 조회이력, 저장된 영업후보, 메모, 검색 제외는 유지됩니다.
- 고객관리, 기업등록, 기업컨설팅, 경정청구, AI 코파일럿 기능은 삭제하거나 축소하지 않습니다.
- 검색·상세 조회만으로는 선점되지 않습니다.
- 신규 SQL이 아직 적용되지 않은 환경에서는 기존 전사 제외 방식으로 안전하게 복귀하며, 신규 배정은 준비 확인 전 임의 허용하지 않습니다.
- 신규 저장은 성공한 원자적 RPC 결과만 저장 완료로 표시합니다.

## 9. 사용방법

### 영업사원

1. DB발굴에서 기존과 같이 조건을 지정해 검색합니다.
2. 조회와 상세 열람은 배정을 만들지 않습니다.
3. 업체를 선택해 `내 영업DB에 담기`를 누릅니다.
4. 기본 24시간 안에 저장목록에서 연락결과를 기록합니다.
5. 검색 결과 한 곳을 선택한 경우 `선택 업체 연락결과 바로 기록`으로 저장과 연락을 이어서 처리할 수 있습니다.
6. `재연락 요청`은 다음 연락예정일을 반드시 입력합니다.
7. 본인에게 이미 배정된 업체는 중복 생성하지 않고 기존 저장목록에서 관리합니다.

### 관리자

1. `관리자 > 시스템 관리 > 영업배정 관리`를 엽니다.
2. 서버 집계 통계와 페이지 단위 업체목록을 확인합니다.
3. 이관 충돌 expander에서 기존 다중 저장 사용자를 확인합니다.
4. 최종 담당자를 선택하거나 강제 해제·회수·재활성화·영구 제외를 실행합니다.
5. 변경사유를 반드시 입력하고 감사로그를 확인합니다.
6. 사용자별 미접촉 배정 한도를 변경할 수 있습니다.

## 10. 테스트 결과와 한계

패치 검증기에서 실행한 비접속 단위·모의통합·정적 계약 테스트:

```text
python APPLY_UPDATE_v9.8.9.py
71 passed
OASIS CRM v9.8.9 patch validation passed.
```

정적 테스트는 다음 계약을 확인합니다.

- 10개 요구 시나리오의 SQL·애플리케이션 구조
- 조회와 배정 분리
- advisory lock, UNIQUE, 원자적 claim-and-save 호출
- 24시간 만료·30건 한도·상태별 재활성화
- RLS/service_role 범위와 광범위 시퀀스 권한 금지
- UID 정규화의 Python/SQL 일치
- canonical UID 일괄 resolver의 source identity 채택과 강한 식별값 충돌 차단
- 번호오류 연락처 지문 변경 재활성화 및 감사
- `oasis_employment_contacts` 전화번호의 지문 포함
- 기존 데이터·메모·상담일지·충돌의 멱등·비파괴 이관
- 연락처 보강 제공자 단계의 원자적 선점과 네이버 오류 재시도 유지

요구된 10개 시나리오의 로컬 검증 결과는 다음과 같습니다.

| 시나리오 | 로컬 검증 결과 | 배포 전 실DB 확인 |
|---|---|---|
| 1. A 단순조회 후 B에게도 노출 | 통과 — 조회 RPC와 배정 RPC가 분리되고 조회이력만 생성 | A·B 테스트 계정으로 결과 노출 확인 |
| 2. A 저장 후 B에서 즉시 제외 | 통과 — 원자적 claim-and-save와 공통 차단 필터 확인 | 두 세션에서 즉시성 확인 |
| 3. A·B 동시 저장 | 통과 — UID advisory lock, UNIQUE, 원자적 UPSERT 계약 확인 | 실제 동시 요청으로 한 명만 성공하는지 확인 |
| 4. 24시간 미접촉 자동반환 | 통과 — 만료 정리 RPC와 만료 감사로그 계약 확인 | 스테이징 시각 이동 또는 만료행으로 확인 |
| 5. 연락기록 후 담당 확정 | 통과 — 만료 제거·최초/최근 연락·횟수 갱신 확인 | 실제 연락결과 저장 후 B 비노출 확인 |
| 6. 관심없음 6개월 차단 | 통과 — 거절 상태와 기본 180일 재활성화 확인 | 기준일 전·후 검색 확인 |
| 7. 번호오류 연락처 변경 전 차단 | 통과 — 정규화 전화 지문 변경 시에만 재활성화 확인 | 번호 변경 전·후 검색 확인 |
| 8. 타 담당 업체 URL 직접 접근 | 통과 — 직접 테이블 권한 회수와 담당자 검증 RPC 확인 | 일반계정으로 직접 접근 거부 확인 |
| 9. 관리자 A→B 변경 | 통과 — 관리자·사유 검증, 담당 변경 및 감사로그 계약 확인 | 관리자 변경 후 A 접근 거부 확인 |
| 10. 기존 저장·메모 이관 | 통과 — additive·멱등 SQL, 원본 미삭제, 충돌 보존 확인 | 백업 복제본의 행 수·메모·업체 ID 대조 |

전체 프로젝트 회귀시험은 `423 passed, 1 skipped, 1 failed`였고, 실패 1건은 이번 패치에서 수정하지 않은 `claim_correction_center.py`의 기존 `st.rerun()` 개수와 기존 테스트 기대값이 서로 다른 건입니다. `origin/main`과 작업본 모두 해당 파일의 호출 수가 4개이고 이번 패치에는 포함되지 않습니다. DB발굴 중복연락 방지와 연락처 보강 호환 테스트를 포함한 최종 패치 검증 71건은 모두 통과했습니다.

운영 또는 스테이징 Supabase에 접속한 실제 동시성, RLS, 시간 이동, 관리자 A→B 변경 시험은 이 로컬 문서 작업에서 수행하지 않았습니다. 배포 전 백업 복제본에서 요구사항의 10개 시나리오를 실제 계정과 동시 요청으로 별도 검증해야 합니다.

## 11. 적용 순서

1. GitHub `main`, 운영 DB, Railway 환경변수를 백업합니다.
2. 패치 ZIP을 프로젝트 루트에 덮어씁니다.
3. `RUN_v9.8.9.bat`로 파일·문법·테스트를 검증합니다.
4. Supabase SQL Editor에서 `supabase_v1032_company_sales_assignments.sql`을 실행합니다.
5. `supabase_v1032_company_sales_assignments_rls.sql`로 권한을 재확인합니다.
6. 스테이징에서 10개 시나리오와 기존 메뉴 회귀시험을 수행합니다.
7. GitHub `main`에 반영하고 Railway 배포 후 시작 로그를 확인합니다.

`RUN_v9.8.9.bat`과 `APPLY_UPDATE_v9.8.9.py`는 운영 Supabase 또는 Railway를 자동 변경하지 않습니다.

## 12. GitHub 업로드 명령

정확한 패치 파일만 올리려면 `GITHUB_UPLOAD_COMMANDS_v9.8.9.txt`의 명령을 순서대로 실행합니다. 해당 명령은 `.codex-remote-attachments`를 포함하지 않으며, `.gitignore` 대상인 변경이력만 명시적으로 추가합니다.

업로드 전 `.env`, 개인 첨부파일, 고객정보, 비밀키가 포함되지 않았는지 반드시 확인합니다.
