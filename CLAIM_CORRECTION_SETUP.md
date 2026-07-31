# 경정청구 연동 설정

경정청구 화면은 기본적으로 외부 인증 요청을 보내지 않습니다. 승인된
중계 API 계약과 아래 Railway 변수가 모두 설정된 경우에만 개인사업자
카카오 인증 발송이 활성화됩니다.

- 개인사업자: 담당자가 고객정보를 입력해 홈택스·근로복지공단 카카오
  인증을 요청하고, 고객이 각 기관 인증을 승인합니다.
- 법인사업자: 고객 PC의 공동인증서 로컬 인증 모듈에서 인증합니다.

```text
CLAIM_COLLECTION_ENABLED=true
TILKO_API_KEY=<발급받은 API KEY>
TILKO_RSA_PUBLIC_KEY=<API KEY에 대응하는 RSA 공개키>
TILKO_HOMETAX_HOST=https://api.tilko.net
TILKO_COMWEL_HOST=https://api24.tilko.net
CLAIM_DOCUMENT_RETENTION_DAYS=90
```

법인 공동인증서는 단순 공용 링크만 설정하지 않습니다. 공급사 계약 후
요청 건별 서명 상태값, 결과 콜백 검증, 고객 PC용 로컬 인증 모듈까지
함께 구현·검증한 뒤 활성화합니다.

홈택스와 근로복지공단 호스트는 위 공식 주소만 허용합니다. 리다이렉트와
임의 호스트는 고객 인증정보 유출을 막기 위해 차단합니다.

현재 구현 범위는 개인사업자 인증 요청·완료 확인, 요청별 문서계획,
진행상황과 수집결과 화면, 전용 비공개 저장소, 홈택스 사업자정보·
사업자등록증명원·국세 납세증명서·종합소득세 신고도움 7개년·
종합소득세 신고서 7개년·폐업사실증명의 수집과 다운로드입니다.
저장 파일은 기본 90일 보관하며 사용자가 다운로드할 때마다 짧은
만료시간의 서명 URL을 발급합니다.

홈택스 환급금은 간편인증 API가 아니라 세무대리인 공동인증서 전용
API이므로 현재 개인사업자 카카오 인증 흐름에서는 수집하지 않습니다.
법인 공동인증서와 나머지 기관 서류는 계약 계정에서 문서별 API 사용
승인을 받고 공식 요청·응답 스키마를 확인한 뒤 순차적으로 활성화합니다.

## Supabase

운영 프로젝트의 SQL Editor에서 다음 파일을 한 번 실행합니다.

```text
supabase_v1022_claim_correction.sql
supabase_v1023_claim_document_delivery.sql
supabase_v1024_claim_document_formats.sql
supabase_v1025_claim_download_storage.sql
```

이 스크립트는 다음 항목을 만듭니다.

- `oasis_claim_cases`
- `oasis_claim_documents`
- `oasis_claim_audit_events`
- 비공개 Storage 버킷 `oasis-claim-documents`
- 서버 전용 문서 완료 처리 함수 `oasis_claim_finalize_document`

익명 사용자와 일반 인증 사용자의 직접 DB·Storage 접근은 허용하지
않습니다. 현재 앱에서는 Railway 서버의 경정청구 전용 저장 계층만 이
테이블에 접근합니다.

## 저장 금지 정보

다음 값은 Supabase, 로컬 파일, 재시도 큐, 오류 로그에 저장하지 않습니다.

- 주민등록번호
- 공동인증서 파일과 개인키
- 공동인증서 비밀번호
- 홈택스·근로복지공단 비밀번호
- 간편인증 Token, CxId, TxId, ReqTxId 원문의 영구 저장

주민등록번호는 인증 요청 또는 완료 확인 호출이 끝나는 즉시 폐기합니다.
간편인증 완료 확인용 Token, CxId, TxId, ReqTxId만 최대 10분 동안 현재
Streamlit 서버 세션 메모리에 유지하며, 완료·만료·로그아웃·사용자 변경
시 제거합니다.
