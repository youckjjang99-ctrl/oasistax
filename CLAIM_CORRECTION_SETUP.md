# 경정청구 원격 인증·자료수집 운영 설정

## 운영 흐름

- `직접입력` 방식은 기존 동작을 유지합니다.
- `카카오톡 발송` 방식은 직원이 고객 이름과 휴대전화만 입력합니다.
- 고객에게 1회용 보안 링크가 발송되고, 고객이 링크에서 주민등록번호와 필수 동의를 직접 입력합니다.
- 국세청 홈택스 인증을 먼저 요청합니다.
- 홈택스 인증 완료가 확인되면 1초 뒤 근로복지공단 인증을 이어서 요청합니다.
- 고객이 화면을 닫아도 Railway 백그라운드 작업이 인증 확인과 자료수집을 계속합니다.
- 인증 링크 원문과 주민등록번호는 평문으로 DB나 로그에 저장하지 않습니다.
- 수집 진행률은 인증 단계가 아니라 실제 수집 완료 문서 수를 기준으로 표시합니다.

## Supabase 적용 순서

운영 프로젝트에 아래 마이그레이션을 순서대로 적용합니다.

```text
supabase_v1022_claim_correction.sql
supabase_v1023_claim_document_delivery.sql
supabase_v1024_claim_document_formats.sql
supabase_v1025_claim_download_storage.sql
supabase_v1026_claim_remote_invites.sql
supabase_v1027_claim_remote_resume_exchange.sql
supabase_v1028_claim_remote_submission_reservation.sql
supabase_v1029_claim_multi_business_documents.sql
```

원격 인증용 테이블과 함수는 `service_role`만 사용할 수 있으며 RLS가 활성화되어야 합니다.

## Railway 환경변수

기존 CRM 서비스와 공개 인증 게이트웨이 서비스에 동일한 보안 설정을 적용합니다.

```text
CLAIM_COLLECTION_ENABLED=true
CLAIM_REMOTE_WORKER_ENABLED=true
CLAIM_PUBLIC_BASE_URL=https://<공개-인증-게이트웨이-도메인>
CLAIM_JOB_ENCRYPTION_KEY=<32바이트 이상 랜덤 키>
CLAIM_LINK_PEPPER=<32바이트 이상 랜덤 키>
CLAIM_SESSION_SECRET=<32바이트 이상 랜덤 키>

SUPABASE_URL=<운영 프로젝트 URL>
SUPABASE_SECRET_KEY=<운영 서버 키>
SUPABASE_SERVICE_ROLE_KEY=<운영 service_role 키>

TILKO_API_KEY=<Tilko 일반 API 키>
TILKO_RSA_PUBLIC_KEY=<Tilko RSA 공개키>
TILKO_HOMETAX_HOST=https://api.tilko.net
TILKO_COMWEL_HOST=https://api24.tilko.net

# Optional: tax-agent refund collection (official HometaxAgent API)
CLAIM_HOMETAX_REFUND_ENABLED=false
TILKO_HOMETAX_AGENT_CERT_FILE_B64=<Base64 of certificate file bytes>
TILKO_HOMETAX_AGENT_KEY_FILE_B64=<Base64 of private-key file bytes>
TILKO_HOMETAX_AGENT_CERT_PASSWORD=<certificate password>
TILKO_HOMETAX_AGENT_ID=<optional; set together with password>
TILKO_HOMETAX_AGENT_PASSWORD=<optional; set together with ID>
TILKO_HOMETAX_AGENT_DEPT_USER_ID=<optional; set together with password>
TILKO_HOMETAX_AGENT_DEPT_USER_PASSWORD=<optional; set together with ID>

# Optional: contracted simple-auth worker-status endpoint
CLAIM_COMWEL_WORKER_STATUS_ENABLED=false
TILKO_COMWEL_WORKER_STATUS_ENDPOINT=<exact endpoint supplied by Tilko>

# Refund collection requires tax-agent authority, a valid engagement/consent,
# and a joint certificate. Keep decoded certificate/key files and passwords
# out of Git, logs, and the UI. Configure the worker endpoint only from the
# exact Tilko contract specification; never reuse a competitor proxy URL.

SOLAPI_API_KEY=<Solapi API 키>
SOLAPI_API_SECRET=<Solapi API Secret>
SOLAPI_KAKAO_CHANNEL_ID=KA01PF260730115605769C9BwhBLCw8n
SOLAPI_TEMPLATE_AUTH_START_ID=KA01TP260730121012330HZah5tLMiZO
SOLAPI_TEMPLATE_AUTH_RESUME_ID=KA01TP260731065756213mJ6ZDn6HsBI
SOLAPI_TEMPLATE_NEXT_AUTH_ID=KA01TP2607310709054437Q7vJJc1fEy
SOLAPI_TEMPLATE_COMPLETE_ID=KA01TP260731071813549LDzSAd0t0JA
SOLAPI_TEMPLATE_FAILED_ID=KA01TP2607310720314767o6465D6po2
```

Solapi 템플릿은 카카오 검수 승인 후 실제 발송할 수 있습니다.
검수 중에는 기존 CRM 서비스의 `SOLAPI_TEMPLATE_*` 5개 변수를 비워
원격 발송 버튼을 비활성화합니다. 5개 템플릿이 모두 승인된 뒤에만
동일한 템플릿 ID를 CRM 서비스에도 등록하고 다시 배포합니다.

## 공개 인증 게이트웨이 서비스

같은 GitHub 저장소로 Railway 서비스를 하나 더 만들고 시작 명령을 아래처럼 설정합니다.

```text
uvicorn claim_public_gateway:app --host 0.0.0.0 --port $PORT
```

배포 후 아래 주소가 정상 응답하는지 확인합니다.

```text
https://<공개-인증-게이트웨이-도메인>/health
```

원문 1회용 링크는 `/c/<token>`에서 즉시 HttpOnly 세션 쿠키로 교환되며, 접근 로그에는 토큰이 남지 않습니다.

## 운영 보안

- API 키와 주민등록번호를 화면, 로그, Git 저장소에 출력하지 않습니다.
- 고객 입력 암호문은 수집 완료·실패·만료 시 삭제합니다.
- 다운로드 문서는 비공개 Storage 버킷에 보관하고 짧은 만료시간의 서명 URL만 발급합니다.
- 보존기간은 `CLAIM_DOCUMENT_RETENTION_DAYS`로 관리합니다. 기본값은 90일입니다.
- Railway 재시작 후에도 미완료 작업은 lease 기반으로 다시 이어집니다.
