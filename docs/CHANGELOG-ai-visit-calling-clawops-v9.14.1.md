# v9.14.1 — ClawOps 070 연결 준비

기준 커밋: `f87559e` (최신 main 확인 후 fast-forward). 작업 브랜치: `codex/ai-visit-calling`.
상태: 로컬 구현·검증 완료. 운영 DB 미적용, 미커밋·미push·미배포, 미개통·실제 발신 0건.

## 변경 내용

- 기존 Twilio 경로를 유지하고 ClawOps REST/VoiceML/양방향 음성 프로토콜을 추가.
- 외부 HTTP 콜백의 별도 HMAC 서명·시간 검증. 미디어 티켓 만료·통화/계정 결속·1회용 nonce 검증.
- ClawOps 통화 ID를 별도 namespace로 저장하고 DB claim 시 통신사를 고정.
- 과금 조건 확인/내부 시험번호/실통화 승인 구분. 시험번호 외 승인 대기 건은 변경하지 않음.
- DB 마이그레이션이 누락된 경우 환경변수가 켜져도 ClawOps 발신 차단.
- 수신거부 9번을 받은 직후 고객이 연결을 끊어도 저장을 먼저 시도하도록 공통 브리지 보완.
- 준비 상태 화면에 ClawOps와 내부 시험 모드 표시. 실제 회선 연결 성공으로 오인하지 않도록 안내.
- 개통 문의 초안·서버 설정·격리 검증·운영 승인·롤백 안내 및 로컬 실행 BAT 제공.

## 변경 파일 전체 목록

1. `VERSION.txt`
2. `voice_calling.py`
3. `voice_calling_provider.py`
4. `voice_calling_worker.py`
5. `voice_calling_gateway.py`
6. `voice_calling_ui.py`
7. `voice_calling_clawops.py` (신규)
8. `voice_calling_clawops_gateway.py` (신규)
9. `supabase/migrations/20261003010404_ai_visit_calling_clawops_provider.sql` (신규)
10. `tests/voice_calling_migration.integration.mjs`
11. `tests/test_voice_calling_gateway.py`
12. `tests/test_voice_calling_clawops.py` (신규)
13. `tests/test_voice_calling_clawops_gateway.py` (신규)
14. `tests/test_voice_calling_clawops_settings.py` (신규)
15. `RUN_v9.14.1.bat` (신규)
16. `docs/ai-visit-calling-clawops-v9.14.1.md` (신규)
17. 이 CHANGELOG (신규)

## 검증 결과

- Python 합동 테스트 **375개 통과**: 기존 전화·실제 Streamlit 렌더링·권한·영업배정·영업메시지 회귀와 새 어댑터·설정·미디어 보안 테스트.
- 격리 PGlite(PostgreSQL) **133개 검사 통과**: 기존/신규 마이그레이션 각 2회 적용, 진행 중 Twilio 작업 보존, ClawOps 통화 전 과정, 작업/동의/감사 이력 보존, RLS/권한, 공급자 혼입·동시 claim·nonce 재사용 차단, 시험 큐 분리.
- `git diff --check` 통과. Python 테스트에서 FastAPI/Starlette의 향후 폐기 예정 API 경고가 있었지만 실패는 없음. 테스트 환경은 Python 3.14이며 실제 Railway 환경에서의 스모크 검증은 아직 수행하지 않음.
- 기존 개인정보·비밀값 유입 검사 `privacy_guard.py --working-tree`: 변경 파일 **17개 모두 통과**. 가상 시험번호는 명시적인 합성 fixture로 구성했으며 검사 규칙을 완화하지 않음.
- 모든 번호/업체는 합성 fixture. 실제 고객 DB·전화·유료 AI API를 호출하지 않음.

## 기존 기능 및 데이터 영향

기존 테이블 중 `oasis_voice_jobs`에 `provider` 컬럼만 추가합니다. 전화번호 수집 테이블, 고객 ID/company_uid, 연락처, 파일, 기존 RLS 경계를 바꾸지 않습니다. 전용 서버 RPC의 검사 범위를 확장하며 `anon`/`authenticated` 실행 차단과 서비스 역할 전용 권한을 유지합니다. 삭제 SQL은 없습니다.

## 남은 작업 / 제한사항

1. ClawOps 사용 계정·070 회선과 외부 스트림 추가요금/약정 서면 확인.
2. 사용자 승인 후 격리 Supabase에서 실제 기존 스키마/트리거 재검증 및 운영 마이그레이션.
3. GitHub·Railway 배포와 운영 변수 설정. 기존 Streamlit/경정청구 서비스 실행 명령은 변경하지 않음.
4. 미인증 WebSocket 슬롯 점유 위험을 막기 위한 ingress IP 허용목록/속도 제한. 고객 발신 전에 필수.
5. 내부 시험 한 통으로 한국어 품질·실제 발신번호·끼어들기·DTMF9·통화 종료·실청구액 검증. 현재 음성 품질을 확인했다고 주장할 수 없음.
6. 고객 발신은 위 조건 통과 후 별도 승인. 수신전화 처리·자동 착신·전문가 캘린더는 이번 패치에 포함하지 않음.

공식 OpenAI/ClawOps 연결 규격과 Supabase 최소 권한 지침에 맞춰 구현했습니다. 실제 개통 없이 기능 검증을 할 수 있는 부분과 통신사 환경에서만 검증 가능한 부분을 구분했습니다.

## 패치와 롤백

패치 ZIP은 상위 폴더 없이 위 17개 파일만 담습니다. 기존 `.git`, 고객 파일, 비밀 설정, 원래 작업 폴더의 미완료 변경은 포함하지 않습니다. `RUN_v9.14.1.bat`는 발신 OFF 로컬 화면만 실행하며 DB 변경/발신 worker를 실행하지 않습니다.

롤백 시 발신 OFF → worker 중지 → 공급사에서 모든 통화 종료 확인 → 코드 이전 버전 복귀. DB 신규 컬럼·통화/동의/수신거부·감사 이력은 보존합니다. 자세한 승인 절차와 환경변수는 같은 버전 연결 안내서를 참고하세요.
