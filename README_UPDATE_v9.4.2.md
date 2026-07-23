# OASIS CRM v9.4.2 업데이트 안내

## 수정 목적

직원현황에서 암호가 `1111`인 근로자 고용정보 현황 Excel을 그대로
업로드하면 자동으로 열어 직원 검수표를 생성하도록 개선합니다.

## 수정 파일

- `employee_status.py`
- `encrypted_excel_reader.py` (신규)
- `requirements.txt`
- `system_precheck.py`
- `VERSION.txt`

## 영향 범위

- 직원현황의 Excel 업로드·분석 단계만 확장합니다.
- 일반 Excel, CSV, PDF, 이미지 분석은 기존 방식 그대로 유지됩니다.
- Supabase 스키마와 다른 CRM 기능은 변경하지 않습니다.

## 적용 방법

1. 압축을 별도 폴더로 풀지 말고 기존 `정책자금자동화` 폴더에 그대로
   덮어 풉니다.
2. 프로젝트 폴더에서 `RUN_UPDATE_v9.4.2.bat`를 실행합니다.
3. `UPDATE_OK`, `PY_COMPILE=OK`, `RUNTIME_SMOKE_TEST=OK`를 확인합니다.
4. 앱을 실행해 직원현황에서 근로자 고용정보 Excel을 업로드합니다.
5. 검수표 내용을 확인·수정한 뒤 기존 저장 버튼으로 등록합니다.

## 자동 백업과 롤백

- 적용 전 기존 파일은
  `_update_backups/v9.4.1_before_v9.4.2_날짜시간`에 백업됩니다.
- 적용 또는 실행 점검이 실패하면 기존 파일이 자동 복구됩니다.
- 새 파일이 적용 전에 존재하지 않았다면 롤백 때 자동 제거됩니다.

## 참고

- 기본 암호는 `1111`입니다.
- 서버별로 다른 암호가 필요할 때만
  `OASIS_EMPLOYMENT_EXCEL_PASSWORD` 환경변수로 바꿀 수 있습니다.
- 복호화 결과는 메모리에서만 사용되며 파일로 저장되지 않습니다.
- Railway는 GitHub 배포 중 `requirements.txt`를 통해 새 모듈을 설치합니다.
