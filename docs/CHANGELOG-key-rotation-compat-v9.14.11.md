# OASIS 키 교체 호환 보완

버전: `v9.14.11-key-rotation-compat`

작업일: 2026-10-22 Asia/Seoul

기준: GitHub main `cb3b819`
상태: 로컬 호환 검증 완료. 운영 키 교체와 배포는 Railway 배포 중단으로 미완료입니다.

## 변경 내용

- 자동 로그인 쿠키는 최소 32자의 전용 `APP_SESSION_SECRET`만 사용합니다. Supabase 서비스 키와 관리자 비밀번호에서 쿠키 암호화 키를 파생하지 않습니다.
- 전용 키가 없거나 짧으면 자동 로그인 쿠키만 비활성화합니다. 기존 일반 로그인은 유지합니다. 이전 DB 키 기반 쿠키는 허용하지 않으므로 적용 후 한 번 다시 로그인해야 합니다.
- DB 조회, RPC, 비공개 파일과 녹음 저장의 Supabase 키 선택 순서를 `SUPABASE_SECRET_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_SERVICE_KEY`, `SUPABASE_KEY`로 통일합니다.
- 새 `sb_secret_` 키는 `apikey` 헤더에만 넣고, 기존 JWT 키는 기존 Bearer 헤더도 유지합니다. 새 키 인증 실패 시 이전 키로 자동 우회하지 않습니다.
- 오류 로그의 새 Supabase 키, 레거시 키 별칭, `serviceKey` 쿼리, URL 사용자명·비밀번호를 마스킹합니다. 변수명에 잘못 붙은 키도 가립니다. 예외 원문 비공개 처리는 유지합니다.

## 데이터와 기존 기능

DB 스키마, RLS, 고객 ID, `company_uid`, 고객자료, 연락이력과 수신거부 이력을 변경하거나 삭제하지 않습니다. 경정청구 수집문서, 인증 절차와 Tilko 호출 로직도 수정하지 않습니다.

`CLAIM_JOB_ENCRYPTION_KEY`, `SALES_APPLICATION_ENCRYPTION_KEY`, `OASIS_KAKAO_GUIDANCE_PHONE_HASH_KEY`는 이번 API 키 교체와 분리하여 기존 값을 보존해야 합니다. 암호화 키를 바꾸면 기존 자료를 읽지 못하고, 해시 키를 바꾸면 과거 번호 단위 수신거부 조회가 달라질 수 있습니다. 이 키들의 유출 대응은 키 버전 관리와 기존 자료 전환을 포함한 별도 검증이 필요하며, 이번 패치가 그 위험을 해소한 것은 아닙니다.

## 안전한 적용 순서

1. 기존 운영 환경과 배포 커밋을 확인합니다. 새 비밀값은 채팅·소스·Git·일반 로그에 기록하지 않습니다.
2. Railway의 보안 공지와 배포 재개 여부를 확인합니다. 배포가 중단돼 새 설정을 적용할 수 없는 동안에는 무중단 교체가 완료됐다고 간주하지 않습니다.
3. CRM에 무작위 전용 `APP_SESSION_SECRET`을 비밀 환경변수로 설정합니다. 연결 서비스의 새 키를 발급하고 모든 별칭이 같은 새 키를 가리키게 적용합니다. 가능하면 서비스별 Supabase·AI 키를 구분합니다.
4. 호환 코드를 배포하고 로그인, DB 조회·저장, 비공개 Storage, 녹음 보관과 수신거부 확인을 검증합니다. 전화번호 수집기와 게이트웨이를 포함해 이전 키를 쓰는 연계 서비스를 빠짐없이 확인합니다. 유료 전화 발신은 별도 승인 없이 테스트하지 않습니다.
5. 새 키로 정상 작동하는 것을 확인한 뒤에만 이전 API 키를 폐기합니다. Supabase의 새 키 생성만으로 레거시 서비스 키가 폐기되지는 않습니다. 모든 소비자 전환 후 레거시 키 비활성화를 따로 확인합니다.
6. 운영 로그에 이전 키 사용 또는 인증 오류가 없는지 재확인합니다. 키 폐기 전후 결과를 키 값 없이 기록합니다.

Railway가 보안 사고 후 배포를 중단한 2026-10-22 확인 시점에는 운영 키 교체와 새 배포를 완료할 수 없습니다. 기존 키를 유지하는 동안 노출 위험은 남습니다. 서비스 중단을 감수하는 긴급 폐기는 이번 무중단 호환 방식과 구분하며 별도 결정이 필요합니다.

## 검증과 롤백

`RUN_v9.14.11-key-rotation-compat.bat`는 설치나 외부 서비스 호출 없이 호환·권한·자료 보존 회귀 테스트를 실행합니다. 합성 자격증명과 모의 저장소만 사용합니다.

- CRM·저장·전화번호 수집·업로드 19개 모듈: **258개 테스트와 하위 검증 24개 통과**.
- AI 전화·게이트웨이 7개 모듈: **288개 테스트 통과**. 실제 전화 발신과 유료 AI 호출은 하지 않았습니다.
- 합계 **546개 테스트와 하위 검증 24개 통과**. 외부 라이브러리 사용 중단 예정 경고가 있으며 테스트 실패는 없습니다. 첫 실행에서는 로컬 전화 의존성이 경로에 없어 수집 단계가 중단됐고, 기존 격리 의존성 경로를 적용한 재실행에서 위 결과를 확인했습니다. 의존성이나 운영 패키지를 변경하지 않았습니다.
- 독립 검토에서 변수명에 붙은 토큰 마스킹 누락 1건을 발견해 수정하고 다시 검증했습니다.
- 변경 파일 13개 개인정보·비밀정보 유입 검사 통과.
- 별도 운영 읽기 전용 확인에서 기존 키로 최소 고객 ID 조회가 정상 동작했습니다. ID와 키 값은 출력하거나 저장하지 않았고 운영 쓰기는 하지 않았습니다.
- 새 키의 실제 연결, 전용 로그인 키 설정, 이전 키 폐기 및 운영 배포 검증은 **아직 하지 않았습니다**.

배포 실패 시 이전 소스로 롤백하되 전용 로그인 키는 유지할 수 있습니다. **이미 폐기한 API 키로 되돌리지 않습니다.** 새 유효 키로 설정을 수정한 뒤 재검증합니다. DB 및 고객자료 롤백은 필요하지 않습니다.

## 변경 파일

```text
auth.py
cloud_db.py
consultation_audio_storage.py
prospect_db_repository.py
runtime_error_log.py
supabase_api_config.py
tests/test_auth_session_key_isolation.py
tests/test_supabase_key_rotation_compat.py
tests/test_runtime_error_log.py
tests/test_key_rotation_data_preservation.py
VERSION.txt
RUN_v9.14.11-key-rotation-compat.bat
docs/CHANGELOG-key-rotation-compat-v9.14.11.md
```

## 공식 참고자료

- [OpenAI 운영 키 관리](https://developers.openai.com/api/docs/guides/production-best-practices)
- [Supabase 신규 API 키 전환](https://supabase.com/docs/guides/getting-started/migrating-to-new-api-keys)
- [Railway 상태](https://status.railway.com/)
