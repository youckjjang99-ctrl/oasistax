# v9.14.2 — OpenAI 음성 연결 및 대량 발신 운영 화면

작성일: 2026-10-06. 기준: `origin/main` `ccb9e00`. 이전 미배포 v9.14.1 패치를 포함합니다.

## 변경 내용

- 체크박스 기반 업체 다중 선택, 검색, 발신 가능 전체선택, 페이지 간 선택 유지.
- 관리자 최대 100개/일반 계정 최대 30개 단위 캠페인 생성. 기존 단건 요청도 별도 목록으로 유지.
- 관리자 일괄 시작 승인, 일시정지/재개, 남은 요청 취소, 진행률·전체 권한 범위 집계·통화 결과·방문 희망 표시.
- 중복 생성 방지 멱등키와 부분 제외 사유 안내. 일반 계정의 페이지 간 결과 누락을 방지하도록 읽기 페이지 크기를 통일.
- 현재 담당자와 수신동의/수신거부를 서버에서 다시 확인. 캠페인 정지 상태를 worker와 음성 연결 직전에 재확인.
- 24시간 승인 만료, 하루 한도, 동시 1통, 결과 불명 시 자동 재발신 금지 유지.
- 비밀값 없는 설정 검사 및 선택적 OpenAI 모델 조회 진단. 실시간 음성/전화망 검증과 명확히 구분.
- 발신 뒤 DB 기록 예외에서도 알려진 통화 종료 시도와 불명 상태 격리. 작업자 오류 로그와 재조회 간격 제한.
- Supabase 신규 캠페인 테이블 및 jobs 외래키/인덱스/RLS/RPC, 기존 데이터 보존.

## 변경 파일 전체 목록

이전 v9.14.1 미배포 연결 패치와 이번 화면/서버 보완을 함께 제공하는 목록입니다.

```text
VERSION.txt
RUN_v9.14.1.bat
RUN_v9.14.2.bat
voice_calling.py
voice_calling_gateway.py
voice_calling_provider.py
voice_calling_clawops.py
voice_calling_clawops_gateway.py
voice_calling_repository.py
voice_calling_ui.py
voice_calling_dashboard.py
voice_calling_preflight.py
voice_calling_worker.py
supabase/migrations/20261003010404_ai_visit_calling_clawops_provider.sql
supabase/migrations/20261006081850_ai_visit_calling_bulk_campaigns.sql
tests/test_voice_calling.py
tests/test_voice_calling_gateway.py
tests/test_voice_calling_clawops.py
tests/test_voice_calling_clawops_gateway.py
tests/test_voice_calling_clawops_settings.py
tests/test_voice_calling_ui.py
tests/test_voice_calling_ui_render.py
tests/test_voice_calling_dashboard.py
tests/test_voice_calling_preflight.py
tests/test_voice_calling_worker.py
tests/voice_calling_migration.integration.mjs
tests/voice_calling_campaigns.integration.mjs
tests/voice_calling_bulk_staging.verify.sql
docs/ai-visit-calling-clawops-v9.14.1.md
docs/CHANGELOG-ai-visit-calling-clawops-v9.14.1.md
docs/ai-visit-calling-bulk-v9.14.2.md
docs/CHANGELOG-ai-visit-calling-bulk-v9.14.2.md
```

## 검증 결과

아래는 로컬 검증 결과입니다. 운영 검증과 구분합니다.

- Python 단위·통합·회귀 테스트: 447개 통과. 기존 FastAPI/Starlette 의존성의 deprecation 경고 397건은 남아 있으나 테스트 실패는 없습니다.
- UI 관련 테스트 25개(실제 Streamlit 화면 테스트 6개 포함): 일반/관리자 선택 한도, 선택·검색·해제, 저장만으로 발신하지 않음, 준비 미완료 승인 차단, 시작 확인, 일시정지, 새로고침 무부작용.
- 브라우저 합성 데이터 화면 확인: 데스크톱과 모바일 390px, 발신 가능 전체선택, 미설정 시작 버튼 차단, 진행률과 결과, 모바일 집계 2×2 배치.
- 기존 PostgreSQL 호환 통합 테스트: 133개 통과.
- 신규 캠페인 PostgreSQL 호환 통합 테스트: 84개 통과.
- exact DDL 두 번 적용 및 기존 작업/동의/수신거부/감사/캠페인 데이터 보존 검증 통과.
- RLS/권한 제한, 부분 생성·재요청 멱등성, 생성 한도, 관리자 승인, 정지·재개·취소, 이전 건별 요청 호환, 불명확 통화 재발신 차단 검증 통과.
- 테스트는 로컬 PGlite·합성 데이터·모의 HTTP/WebSocket을 사용합니다. 실제 다중 PostgreSQL 연결, 운영 Supabase, 전화망, 유료 OpenAI 음성 세션을 사용하지 않았습니다.

### 2026-10-06 격리 Supabase 추가 검증

- 사용자 승인 한도 $0.10 안에서 데이터 없는 임시 브랜치를 사용했습니다.
- 과거 초기 테이블 생성 이력 누락으로 자동 재생이 실패하여, 현재 운영 public 스키마만 빈 브랜치에 재현했습니다. 고객 행·Storage 파일·운영 비밀값·cron 작업은 복사하지 않았습니다.
- 기존 테이블 90개·함수 200개·제약조건 491개·비제약 인덱스 267개·트리거 58개·뷰 2개·정책 1개와 권한을 재현한 실제 PostgreSQL에서 검사했습니다.
- ClawOps provider와 bulk campaign 마이그레이션을 각각 두 번 적용했습니다.
- 기존 실제 스키마 검증을 provider 적용 후와 campaign 적용 후 각각 실행하고, 새 bulk 검증을 실행하여 세 번 모두 PASS를 확인했습니다.
- 합성 데이터 트랜잭션은 모두 ROLLBACK했고 검증 대상 12개 테이블에 남은 행은 0개였습니다.
- 보안 advisor에 WARN/ERROR는 없었습니다. 서버 전용 테이블의 의도된 기본 거부인 RLS-without-policy INFO는 별도입니다.
- ACL/RLS/실행권한/security-invoker/search_path 별도 검사 66개가 모두 통과했습니다.
- 임시 브랜치는 검증 후 삭제했고, 브랜치 목록에 운영 main만 남은 것을 확인했습니다. 사용 시간은 약 14분이며 실제 청구액은 공급사 청구 내역을 기준으로 합니다.
- 이 검증도 실제 전화망, OpenAI Realtime 음성 품질, 회선 개통·과금 검증을 대체하지 않습니다.

### 2026-10-06 운영 DB 적용

- 사용자 배포 승인 후 provider → campaign 순서로 적용했습니다. 운영 이력 버전은 `20261006085644`, `20261006085648`이며 이름과 SQL 내용이 저장소의 두 신규 마이그레이션에 대응합니다.
- 적용 전/후 건수: 고객 18/18, CRM 9/9, 영업후보 1,112/1,112, 후보 연락처 801/801, 연락기록 203/203, 문서 1,020/1,020. 감소 없음.
- 기존 음성 작업·동의·수신거부·이벤트는 각 0/0, 신규 캠페인 0. 고객 발신이나 시험 고객 삽입은 실행하지 않았습니다.
- 캠페인과 작업 테이블의 RLS 활성화, anon/authenticated 직접 조회 차단을 확인했습니다.
- 운영 보안 advisor WARN/ERROR 없음. INFO 97건은 기존 서버 전용 기본 거부 구조를 포함합니다. [RLS 정책 없는 테이블 알림 설명](https://supabase.com/docs/guides/database/database-linter?lint=0008_rls_enabled_no_policy).
- Railway 직접 로그인은 만료되어 서버 환경변수 확인·키 등록은 보류했습니다. ClawOps 계정에 활성 구독과 기존 키가 없음을 확인했으며, 새 키 발급은 승인됐지만 안전한 서버 등록 경로가 준비될 때까지 발급하지 않았습니다. OpenAI 서버 키의 기존 등록 여부는 미확인입니다.

## 미적용·미검증 항목

- 운영 Supabase 신규 마이그레이션은 적용 완료. GitHub push/Railway 배포 상태는 해당 커밋의 배포 결과를 확인합니다.
- 실제 OpenAI Realtime 인증/음성 왕복, ClawOps 발신과 국내 번호 표시/끼어들기/DTMF/실청구액: 미검증.
- gateway ingress 공급사 IP 허용 및 연결 속도 제한: 운영 적용 전 필요.
- 모델 설정 검사 성공을 실제 연결 성공으로 표시하지 않습니다. 이번 로컬 환경의 연결 설정 검사 결과는 미완료였습니다.
- 백그라운드 자동 발신은 별도 서버 worker가 실행되어야 합니다. 로컬 화면만 실행하는 BAT는 전화를 걸지 않습니다.

## 운영·사용·롤백 안내

[v9.14.2 운영 안내](ai-visit-calling-bulk-v9.14.2.md)를 따릅니다. 이전 worker는 캠페인 정지를 알지 못하므로 롤백 후 바로 재가동하지 않습니다. 고객정보와 이력은 삭제하지 않습니다.

## 승인 후 GitHub 반영 명령

전용 작업 폴더에서만 실행하고, 원래 고객자료가 있는 폴더에서 무조건 `git add .`를 실행하지 않습니다. 아래는 명령 안내이며 이번 작업에서 실행하지 않았습니다.

```sh
git status
git diff --check
git add VERSION.txt RUN_v9.14.1.bat RUN_v9.14.2.bat voice_calling.py voice_calling_gateway.py voice_calling_provider.py voice_calling_clawops.py voice_calling_clawops_gateway.py voice_calling_repository.py voice_calling_ui.py voice_calling_dashboard.py voice_calling_preflight.py voice_calling_worker.py
git add supabase/migrations/20261003010404_ai_visit_calling_clawops_provider.sql supabase/migrations/20261006081850_ai_visit_calling_bulk_campaigns.sql
git add tests/test_voice_calling.py tests/test_voice_calling_gateway.py tests/test_voice_calling_clawops.py tests/test_voice_calling_clawops_gateway.py tests/test_voice_calling_clawops_settings.py tests/test_voice_calling_ui.py tests/test_voice_calling_ui_render.py tests/test_voice_calling_dashboard.py tests/test_voice_calling_preflight.py tests/test_voice_calling_worker.py tests/voice_calling_migration.integration.mjs tests/voice_calling_campaigns.integration.mjs tests/voice_calling_bulk_staging.verify.sql
git add docs/ai-visit-calling-clawops-v9.14.1.md docs/CHANGELOG-ai-visit-calling-clawops-v9.14.1.md docs/ai-visit-calling-bulk-v9.14.2.md docs/CHANGELOG-ai-visit-calling-bulk-v9.14.2.md
python tools/privacy_guard.py --staged
git commit -m "feat: add consented AI calling campaigns and OpenAI voice bridge"
git push origin codex/ai-visit-calling
```

최신 main과 충돌/CI를 검토하고 승인된 PR 병합으로 운영에 반영합니다. 위 push도 사용자 승인 후 실행합니다.
