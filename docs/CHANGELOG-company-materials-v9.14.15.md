# 기업 자료 공통 보존·격리와 자료함

버전: `v9.14.15-company-materials` · 작업일: 2026-10-23 Asia/Seoul

기준 소스: GitHub main `b7e07833f77213d8ce0551372d03e5763c4c7694`. 미배포 상태였던 v9.14.13 등기 업로드·v9.14.14 주가평가 복원 보완을 포함한 누적 패치입니다.

## 적용 원칙과 범위

크레탑, 등기, 세무조정계산서, 직원명부, 정관, 연구소·전담부서 인정서, 상담녹음·상담일지와 저장된 주가평가를 대상으로 합니다. 업체·사용자별 자료를 분리하고, 원본·이전 분석·계산 버전을 삭제하지 않으며, 저장 상태를 정확히 구분하고 저장 자료를 다시 열 수 있게 합니다. 기타 기존 고객 자료·변경이력도 자료함에서 확인할 수 있습니다.

Supabase 스킬의 소유자·업체 서버필터, 기존 권한과 비공개 Storage 보존 기준을 적용했습니다. Supabase 테이블·컬럼·인덱스·RLS 변경이나 마이그레이션 SQL은 없습니다. 기존 ID·company_uid·고객 원본·Git 이력은 변경하거나 삭제하지 않았습니다. 인증, 경정청구 자료수집 API, Tilko 호출, 전화번호 수집, 세법 계산식은 수정하지 않습니다.

## 발견한 문제와 변경

- 등기 업로드가 기업등록의 업체 선택 대신 주가평가 위젯 상태에 의존하던 연결을 제거했습니다.
- 계산 탭이 숨겨지면서 사라지는 위젯 상태를 사용자·업체별 작업공간으로 복원합니다. 다른 업체의 결과·할증률·세무조정 검토가 섞이지 않도록 분리했습니다.
- 주가평가 상단에서 등기 요약·상세, 최신 등기 새로고침·명시적 반영, 최근 평가 바로 열기를 제공합니다. 새 등기가 기존 평가와 다르면 기존 결과는 유지하고 재검토·재계산을 안내합니다.
- 계산 결과는 새 버전으로 보존하고 이전 평가를 덮어쓰지 않습니다. 같은 계산의 저장 재확인은 같은 버전 ID로 재시도합니다. 저장 당시 등기 출처와 세무조정 검토 이력도 보관합니다.
- 기업컨설팅에 ‘자료함’을 추가하고, 기업정보등록의 ‘이미 등록한 자료 현황 보기’에도 같은 자료함을 연결했습니다. 자료유형·저장 버전을 선택해 분석 결과를 확인하고, 권한·업체 연결·SHA 검증 후 원본을 다운로드할 수 있습니다. 원격 원본은 기존 비공개 Storage와 짧은 서명 링크를 사용합니다.
- 여러 저장소 조회는 자료함을 열 때만 수행합니다. 사용자 세션·업체·원본 변경시각 기반의 짧은 캐시로 반복 조회를 줄이며 ‘자료 목록 새로고침’으로 즉시 갱신할 수 있습니다.
- 공통 문서 메타데이터와 각 분석·이력 파일에 잠금·엄격 읽기·원자적 저장을 적용했습니다. 손상된 원본은 덮어쓰지 않습니다. 같은 원본의 재분석은 분석 버전·메모·기존 확인 항목을 보존합니다.
- 공통 문서 등록에서 모든 지원 문서유형의 원본을 기존 비공개 보관 경로로 연결합니다. 기능이 비활성화되거나 클라우드 저장이 실패하면 로컬 원본은 유지하고 확인 필요 상태를 남깁니다.
- 직원명부 20버전·상담일지 500건 절단을 제거했습니다. 로컬 미동기화 자료와 클라우드 자료를 병합하고, 정관·재무 분석의 이전 버전도 유지합니다.
- 상담·음성·직원 자료의 동일 상호만으로 연결하는 경로를 차단했습니다. 서버 응답의 소유자·업체 번호·레코드 ID와 내부 payload가 충돌하면 자동 연결하지 않습니다.
- 자료함의 이력과 저장 평가, 음성 원본은 선택한 소유자·기업을 서버에서 먼저 필터링하고 페이지 조회합니다. 기존 화면의 100개·200개 요약 제한 때문에 다른 업체의 자료에 밀리는 경로를 보완했습니다.
- 원본과 분석이력까지 클라우드 보관을 확인한 경우에만 완료로 표시합니다. 로컬 보존·클라우드 확인 필요·영구 저장 확인 실패는 구분합니다. 자료 조회 실패를 ‘자료 없음’으로 단정하지 않습니다.

## 사용자·관리자 사용법

1. 기업정보등록에서 기업을 선택해 자료를 등록하고, 진행 카드와 저장 상태를 확인합니다. 기업컨설팅에서 미리 선택할 필요가 없습니다.
2. 기업컨설팅 → 해당 기업 → **자료함**에서 자료유형과 저장 버전을 선택합니다. 기업정보등록의 **이미 등록한 자료 현황 보기**에서도 같은 기능을 사용할 수 있습니다.
3. 분석 결과는 화면에서 확인하고 원본이 필요하면 **원본 파일 준비**를 누릅니다. 현재 사용자 보관 영역의 파일 또는 해당 사용자·자료에 연결된 비공개 원본만 열 수 있습니다.
4. 정관·직원현황에서는 기존 화면 안에서도 저장된 버전을 선택해 확인할 수 있습니다.
5. 주가평가에서는 등기 요약을 확인하고 **최근 평가 바로 열기**로 저장 당시 입력값·결과를 복원합니다. 새 등기와 차이가 있으면 기준일을 확인한 뒤 명시적으로 반영하고 재계산합니다.
6. 다른 기기에서 등록한 자료는 자료함의 **자료 목록 새로고침**으로 조회합니다. 관리자도 기존 운영 권한을 사용하며 새 권한 설정이나 SQL 실행은 필요 없습니다.

## 검증과 제한사항

검증은 가상 자료, 임시 폴더, 모의 클라우드로 수행합니다. 운영 고객 파일과 DB·Storage에는 쓰지 않았으며 실제 고객자료의 인식 정확성이나 운영 원본 보관 완료를 대신 확인한 것은 아닙니다. 최종 테스트 결과는 완료 보고에 명시합니다.

최종 결과: 제공 실행 파일로 34개 관련 테스트 모듈 **588개 통과**(실패 0). 운영과 같은 Streamlit 1.59.1에서 탭·메뉴·업체 왕복, 등록 자료 선택, 저장 평가 즉시 열기, 등기 새로고침·명시적 반영까지 검증했습니다. 합성 음성 원본 1,205개 페이지 조회와 동시 저장·손상 JSON·권한 불일치·저장 실패 시나리오를 포함합니다. PDF 라이브러리의 기존 SWIG 사용중단 경고 5개는 있으나 테스트 실패는 아닙니다. 변경 파일 31개의 개인정보·비밀키 검사와 `git diff --check`도 통과했습니다. 운영 스모크 테스트와 실제 고객 PDF 재분석은 아직 수행하지 않았습니다.

- 이미 과거 버전에서 삭제·절단된 자료를 이번 코드만으로 복구하지는 못합니다. 남아 있는 원본·클라우드·기존 백업에서 별도 확인해야 합니다.
- 클라우드 원본 영속화는 기존 비공개 Storage 기능 설정과 정상 연결이 전제입니다. 키·기능 플래그는 변경하지 않았고, 미확인 상태를 성공으로 표시하지 않습니다.
- 아직 클라우드에 전송되지 않은 세션 초안은 다른 기기나 재접속에서 보장되지 않습니다. 계산·등록 완료 후 클라우드 상태를 확인해야 합니다.
- 기존 등기 스냅샷 테이블의 여러 기기 동시 수정에 대한 DB CAS/RPC까지 새로 도입하지는 않습니다. 첨부 원본·분석 버전·새 평가 이력은 별도로 보존하며, 현재 스냅샷 충돌은 기존 자료를 확인해 복원해야 합니다.
- 기존 고객 DB·CRM 이력·원천 자료를 임의로 재배정하거나 병합하지 않습니다. 신원이 불명확한 과거 자료는 이름만으로 자동 연결하지 않고 원본을 보존합니다.

## 변경 파일

운영 소스: `VERSION.txt`, `articles_review.py`, `cloud_sync.py`, `company_materials.py`, `consultation_audio_storage.py`, `consultation_journal.py`, `customer_history.py`, `employee_status.py`, `enterprise_center.py`, `enterprise_documents.py`, `stock_valuation.py`.

검증: `tests/test_company_material_retention.py`, `tests/test_company_materials_library.py`, `tests/test_consultation_material_isolation.py`, `tests/test_customer_history_material_scope.py`, `tests/test_enterprise_document_preservation.py`, `tests/test_financial_material_preservation.py`, `tests/test_registry_registration_context.py`, `tests/test_registry_snapshot_safety.py`, `tests/test_registry_upload_progress.py`, `tests/test_stock_company_state.py`, `tests/test_stock_history_company_query.py`, `tests/test_stock_history_preservation.py`, `tests/test_stock_valuation_goodwill_ui.py`, `tests/test_upload_workflow_feedback.py`.

실행·변경기록: `RUN_v9.14.13-registry-context.bat`, `RUN_v9.14.14-stock-state.bat`, `RUN_v9.14.15-company-materials.bat`, `docs/CHANGELOG-registry-context-v9.14.13.md`, `docs/CHANGELOG-stock-state-v9.14.14.md`, `docs/CHANGELOG-company-materials-v9.14.15.md`.

덮어쓰기 ZIP은 변경 파일만 기존 상대 경로로 포함하며 상위 폴더·`.git`·고객자료·비밀키를 포함하지 않습니다.

## 배포·롤백

GitHub 공개 업로드, main 반영과 연결 서비스 재배포, Railway 운영 배포는 별도 승인 후 진행합니다. 운영 반영 전후로 로그인, 업체별 자료함, 자료 업로드, 저장 버전 복원, 파일 접근권한과 기존 경정청구·수집 서비스 상태를 확인합니다.

DB 마이그레이션이 없어 직전 검증된 CRM 배포로 코드만 되돌릴 수 있습니다. 기존 형식의 최신 필드를 유지하면서 버전 메타데이터를 추가했으므로 원본·이력 파일과 테이블은 삭제하거나 롤백하지 않습니다. Git 이력 강제 변경은 하지 않습니다.

승인 후 수정 브랜치에서의 명령 예시(먼저 변경 파일과 개인정보 검사 결과를 확인):

```bash
git status
git add VERSION.txt articles_review.py cloud_sync.py company_materials.py consultation_audio_storage.py consultation_journal.py customer_history.py employee_status.py enterprise_center.py enterprise_documents.py stock_valuation.py
git add tests/test_company_material_retention.py tests/test_company_materials_library.py tests/test_consultation_material_isolation.py tests/test_customer_history_material_scope.py tests/test_enterprise_document_preservation.py tests/test_financial_material_preservation.py tests/test_registry_registration_context.py tests/test_registry_snapshot_safety.py tests/test_registry_upload_progress.py tests/test_stock_company_state.py tests/test_stock_history_company_query.py tests/test_stock_history_preservation.py tests/test_stock_valuation_goodwill_ui.py tests/test_upload_workflow_feedback.py
git add RUN_v9.14.13-registry-context.bat RUN_v9.14.14-stock-state.bat RUN_v9.14.15-company-materials.bat docs/CHANGELOG-registry-context-v9.14.13.md docs/CHANGELOG-stock-state-v9.14.14.md docs/CHANGELOG-company-materials-v9.14.15.md
git commit -m "fix: preserve and isolate company materials and analysis versions"
git push origin codex/registry-upload-context-20261023
```

관련 없는 고객자료가 있는 기존 작업 폴더에서는 `git add .`를 실행하지 않습니다.
