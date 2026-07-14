# OASIS 정책자금자동화 v6.3.0 적용방법

## 적용 순서
1. 이 ZIP 파일의 내용을 기존 `정책자금자동화` 프로젝트 폴더에 압축 해제합니다.
2. `RUN_V6.3.0_UPDATE.bat`를 실행합니다.
3. 업데이트 완료 메시지와 백업 폴더 위치를 확인합니다.
4. Supabase SQL Editor에서 `supabase_v630_upgrade.sql`을 한 번 실행합니다.
5. 앱을 재실행하고 `기업컨설팅 → CRM → 녹음파일 상담일지 보기`를 확인합니다.

## 주요 변경사항
- 저장된 녹음 상담일지를 CRM에서 언제든 다시 조회
- 상담일지 본문을 로컬 JSON과 Supabase에 이중 저장
- 상담내용의 정책자금 키워드를 기존 매칭키워드에 추가 병합
- 정책자금 메뉴와 정책자금매칭 결과에서 최신 키워드 사용

## 데이터 보호
- 고객DB, CRM, 기업 히스토리, 기존 상담일지, 매칭키워드 및 녹음파일을 삭제하지 않습니다.
- 업데이트 전 `consultation_journal.py`, `enterprise_center.py`, `VERSION.txt`를 자동 백업합니다.
- 기존 수동 키워드와 제외키워드는 유지됩니다.

## SQL 안내
`supabase_v630_upgrade.sql`은 신규 `oasis_consultation_journals` 테이블만 추가합니다. 기존 Supabase 테이블과 Storage 파일을 변경하거나 초기화하지 않습니다.
