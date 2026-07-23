# OASIS CRM v9.5.0 업데이트 안내

## 수정 목적

Railway에 등록한 공공데이터포털 인증키를 OASIS CRM에서 안전하게 읽고,
국민연금 가입 사업장 내역 V2 API의 인증 및 응답구조를 확인합니다.

## 수정 파일

- `app.py`
- `public_data_api.py` 신규
- `prospect_db_center.py` 신규
- `VERSION.txt`

## 적용 전 확인

Railway 앱 서비스의 `Variables`에 아래 변수가 있어야 합니다.

```text
DATA_GO_KR_SERVICE_KEY
```

Railway 화면에 `1 Change`가 남아 있으면 먼저 `Deploy`를 눌러 적용합니다.
인증키를 소스코드나 패치파일에 직접 입력하지 마세요.

## 적용 방법

1. 이 ZIP의 파일과 `payload` 폴더를 기존 `정책자금자동화` 폴더에
   그대로 덮어 풉니다.
2. 기존 `.git` 폴더는 그대로 유지합니다.
3. `RUN_UPDATE_v9.5.0.bat`를 실행합니다.
4. `UPDATE_OK`, `PY_COMPILE=OK`, `RUNTIME_SMOKE_TEST=OK`를 확인합니다.
5. 동봉된 Git 명령어로 GitHub `main`에 업로드합니다.
6. Railway 배포가 끝나면 관리자 계정으로 로그인합니다.
7. 왼쪽 `영업후보DB`에서 서울 또는 경기를 선택하고
   `국민연금 API 연결 테스트`를 누릅니다.

## 정상 결과

- Railway 인증키: `등록됨`
- 상태: `CONNECTED`
- HTTP: `200`
- 전체 건수: 1건 이상 또는 API가 반환한 해당 지역 건수
- 응답 샘플 1건 표시

## 자동 백업과 롤백

- 적용 전 기존 파일은
  `_update_backups/v9.4.4_before_v9.5.0_날짜시간`에 백업됩니다.
- 적용 또는 검사 실패 시 기존 파일을 복원하고 신규 파일을 제거합니다.
- 기존 고객DB와 Supabase 데이터는 변경하지 않습니다.

## 이번 버전의 범위

이번 버전은 연결 검증 전용입니다. 대량수집·중복제거·후보DB 저장은
연결 결과를 확인한 다음 버전에서 추가합니다.

