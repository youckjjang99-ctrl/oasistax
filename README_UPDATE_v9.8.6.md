# OASIS CRM v9.8.6 적용 안내

## 적용 기준

- v9.8.5가 적용된 프로젝트용 PATCH입니다.
- ZIP 내부 파일을 기존 `정책자금자동화` 폴더에 바로 덧붙입니다.
- 별도 상위 폴더 없이 압축을 풀고 `RUN_UPDATE_v9.8.6.bat`를 실행합니다.

## 수정 목적 · 파일 · 영향 범위

- `prospect_db_repository.py`: 같은 사업장 스냅샷을 한 번만 저장하고,
  마스킹된 개인사업자 번호는 상호·주소까지 비교해 중복을 제외합니다.
- `prospect_collection_service.py`: 스냅샷 저장 실패를 기존 DB 중복확인
  실패와 분리합니다.
- `prospect_db_center.py`: 두 오류를 각각 정확한 안내문으로 표시합니다.
- 기존 CRM, 고객관리, DB발굴 UI, 연락처 검색, 검색 이력, 메모, 엑셀,
  Supabase 데이터와 `.git`은 삭제하거나 변경하지 않습니다.

## 해결되는 현상

- `ON CONFLICT DO UPDATE command cannot affect row a second time`:
  같은 월·같은 사업장 행이 한 번의 Supabase upsert에 중복 포함되던
  문제입니다. v9.8.6은 전송 전에 한 행으로 통합합니다.
- 저장한 개인사업자가 같은 페이지 재조회 때 다시 보이는 현상:
  국민연금 원본의 `source_key` 또는 마스킹 사업자번호가 달라질 수 있어
  발생합니다. 이제 상호·주소도 함께 비교하므로 모든 사용자가 저장한
  업체는 재발굴 대상에서 제외됩니다.

## 적용 순서

1. ZIP 내용을 기존 프로젝트 폴더에 그대로 풉니다.
2. `RUN_UPDATE_v9.8.6.bat`를 실행합니다.
3. `UPDATE_OK`, `PY_COMPILE=OK`, `RUNTIME_SMOKE_TEST=OK`를 확인합니다.
4. 이번 버전은 새 Supabase SQL이 없습니다. v9.8.5의
   `supabase_v985_employee_snapshots.sql`을 아직 실행하지 않았다면 그 SQL만
   한 번 실행합니다.
5. 동봉된 Git 명령어로 GitHub main에 반영하고 Railway 배포가 완료되면
   DB발굴을 다시 조회합니다.

## 백업 · 롤백

- 적용 전 파일은 `_update_backups/before_v9.8.6`에 자동 백업됩니다.
- 컴파일 또는 실행 스모크 테스트 실패 시 업데이트 파일을 자동 복원합니다.
- 수동 롤백이 필요하면 해당 백업 폴더의 파일을 프로젝트 루트에 덮어씁니다.
