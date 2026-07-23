# OASIS CRM v9.7.0 업데이트 안내

## 수정 목적

국민연금 기본·상세조회로 저장한 영업후보에 대해 공개된 사업용
대표전화·이메일·공식 홈페이지를 순차적으로 확인하고, 출처와
검증상태를 함께 Supabase에 저장합니다.

조회 순서는 다음과 같습니다.

1. 카카오 로컬
2. 승인 인허가 API 6종
3. 네이버 웹검색
4. 공식 홈페이지

카카오에서 신뢰도 높은 대표전화를 확인하면 인허가 API 전화조회는
건너뛰지만, 이메일과 공식 홈페이지 확인을 위해 네이버 검색은 계속합니다.

## 수정 파일과 영향 범위

- 수정: `prospect_db_center.py`
  - 연락처 API 연결점검, 업체 선택, 일괄 보강, 결과 확인 화면 추가
- 수정: `prospect_db_repository.py`
  - 연락처 조회·저장 및 기존 확정값 보호 추가
- 신규: `contact_matching.py`
- 신규: `kakao_local_client.py`
- 신규: `localdata_contact_client.py`
- 신규: `naver_web_search_client.py`
- 신규: `website_contact_parser.py`
- 신규: `contact_enrichment.py`
- 신규: `supabase_v970_contact_enrichment.sql`
- 수정: `VERSION.txt`

기존 국민연금 조회, 영업후보 수집·저장 및 다른 OASIS CRM 기능에는
삭제 또는 대체 변경이 없습니다.

## Railway 환경변수

아래 4개 이름이 앱 서비스의 Variables에 있어야 합니다.

```text
DATA_GO_KR_SERVICE_KEY
KAKAO_REST_API_KEY
NAVER_CLIENT_ID
NAVER_CLIENT_SECRET
```

키 값은 코드나 GitHub에 입력하지 않습니다.

## 적용 방법

1. ZIP 내용을 기존 `정책자금자동화` 폴더에 바로 덮어 풉니다.
   ZIP 안에는 별도의 상위 패치 폴더가 없습니다.
2. `RUN_UPDATE_v9.7.0.bat`를 실행합니다.
3. 다음 결과를 확인합니다.

```text
UPDATE_OK
VERSION=v9.7.0
CONTACT_CHAIN=KAKAO_LOCALDATA_NAVER_WEBSITE
APPROVED_LOCALDATA_APIS=6
PY_COMPILE=OK
RUNTIME_SMOKE_TEST=OK
DB_SCHEMA=ADD_ONLY_SQL_INCLUDED
```

4. Supabase Dashboard의 SQL Editor에서
   `supabase_v970_contact_enrichment.sql`을 한 번 실행합니다.
5. 동봉된 Git 명령어로 GitHub `main`에 업로드합니다.
6. Railway 배포가 끝나면 관리자 계정으로 로그인합니다.
7. `영업후보DB`에서 연락처 보강 API 연결점검을 실행합니다.
8. `저장된 영업후보 새로고침` 후 연락처 테이블 연결을 확인합니다.
9. 처음에는 업체 1개만 선택해 연락처 자동 보강 결과를 확인합니다.

## Supabase SQL

추가 SQL은 `oasis_prospect_contacts` 테이블만 새로 만듭니다.
기존 고객DB와 영업후보DB의 구조·데이터는 수정하거나 삭제하지 않습니다.

## 검증 기준

- 회사명과 주소를 함께 비교합니다.
- 일반 대표전화는 점수 기준을 통과한 경우에만 자동확정합니다.
- `010` 휴대전화는 확인 필요로 저장합니다.
- 이메일은 공식 홈페이지 도메인 일치까지 확인해야 자동확정합니다.
- 수동확정 또는 자동확정된 기존 값은 재수집으로 하향 변경하지 않습니다.
- 출처 URL, 수집시각, 신뢰도와 검증상태를 함께 저장합니다.

## 자동 백업과 롤백

- 적용 전 기존 파일은
  `_update_backups/v9.6.1_before_v9.7.0_날짜시간`에 백업됩니다.
- 신규 파일 목록도 추적합니다.
- `py_compile` 또는 실행검사 실패 시 기존 파일은 복구되고,
  이번 패치에서 새로 만든 파일은 제거됩니다.
- `.git`과 Git 연결은 수정하지 않습니다.
