# OASIS CRM v9.6.1 업데이트 안내

## 수정 목적

국민연금 기본조회에서 받은 사업장 순번 `seq`를 상세조회 API에 전달해
실제 가입자 수와 사업장 상세정보를 받아온 뒤 수집조건을 적용합니다.

## 수정 파일

- `public_data_api.py`
- `prospect_db_center.py`
- `VERSION.txt`

## 적용 방법

1. ZIP 내용을 기존 `정책자금자동화` 폴더에 바로 덮어 풉니다.
2. `RUN_UPDATE_v9.6.1.bat`를 실행합니다.
3. 아래 결과를 확인합니다.

```text
UPDATE_OK
VERSION=v9.6.1
NPS_BASIC_DETAIL=LINKED
PY_COMPILE=OK
RUNTIME_SMOKE_TEST=OK
DB_SCHEMA=PRESERVED
```

4. 동봉된 Git 명령어로 GitHub `main`에 업로드합니다.
5. Railway 배포 완료 후 관리자 계정으로 로그인합니다.
6. `영업후보DB`에서 조회 건수를 먼저 10건으로 선택합니다.
7. `사업장 미리보기 수집`을 누릅니다.
8. 기본조회·상세조회 성공·상세조회 실패·최종 후보 수를 확인합니다.

## 호출량

- 10건 수집: 기본조회 1회 + 상세조회 최대 10회
- 30건 수집: 기본조회 1회 + 상세조회 최대 30회
- 실패 재시도가 발생하면 실제 호출 횟수가 늘어날 수 있습니다.
- 상세조회는 동시에 최대 5개만 실행합니다.

## 자동 백업과 롤백

- 적용 전 기존 파일은
  `_update_backups/v9.6.0_before_v9.6.1_날짜시간`에 백업됩니다.
- 적용 또는 실행검사 실패 시 v9.6.0 파일로 자동 복구됩니다.
- 기존 고객DB와 Supabase 데이터는 변경하지 않습니다.

