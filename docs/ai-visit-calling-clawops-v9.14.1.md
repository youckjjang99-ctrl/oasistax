# ClawOps 070 연동 v9.14.1

## 현재 상태

ClawOps 070 → VoiceML 양방향 WebSocket → 기존 OpenAI Realtime 음성 서버 연결을 위한 로컬 패치입니다. **운영 DB 적용·GitHub push·Railway 배포·가입·결제·번호 개통·실제 발신은 수행하지 않았습니다.** 코드 검증과 국내 실통화 품질 검증은 구분합니다. API 키와 계정이 없으므로 연결 성공 또는 음질을 보장하지 않습니다.

기준: GitHub main `f87559e`. 기존 Twilio 경로·고객 데이터·UI 흐름·경정청구·전화번호 수집은 보존합니다. 별도 관리형 worktree에서 작업했으며 원래 폴더의 미완료 변경은 수정하지 않았습니다. 런타임 의존성 추가는 없습니다.

## 선택한 방식과 안전장치

- ClawOps REST 발신 및 서명된 VoiceML 요청을 받고, 서명 없는 미디어 WebSocket은 90초 티켓과 DB의 1회용 nonce·통화/계정/작업 결속으로 검증합니다. 검증 전에는 OpenAI 연결을 생성하지 않습니다.
- ClawOps ID는 `clawops:` 접두어로 기존 Twilio ID와 구분합니다. 승인 대기 작업의 통신사는 발신 직전 DB 트랜잭션에서 확정합니다.
- 기본 실제 발신 OFF. 과금 조건 확인 플래그도 기본 OFF. 실통화 검증 승인 전에는 서버에 지정한 내부 시험번호(최대 5개)에만 발신합니다. 고객 대기 건은 시험 큐에서 제외합니다.
- 동의 근거·현재 담당자·관리자 승인·평일 09~18시·하루 기본 20건·동시 1통 제한은 유지합니다. 처음 시험은 하루 한 건으로 제한하세요.
- 발신 응답이 불명확하면 재발신하지 않고 관리자 확인으로 격리합니다. 수신거부는 음성 또는 키패드 9번으로 처리합니다.
- 원본 녹음/전체 전사를 앱에서 보관하지 않습니다. ClawOps 계정의 별도 자동 녹음/분석 기능도 비활성 상태인지 계약·개통 시 확인해야 합니다.
- 스트림 단절 시 통신사도 통화를 종료하도록 Connect.action을 지정하지 않습니다. 별도 종료 REST도 호출합니다. 서버 통화시간 제한은 유지하되 통신사 측 강제 시간상한 제공 여부는 별도 확인이 필요합니다.
- 음성은 μ-law 8kHz입니다. 모델 성능만으로 전화망 음질 한계를 없앨 수는 없습니다. 기존 모델 `gpt-realtime-2.1`은 그대로 두고, 다른 모델 전환은 한국어 실통화 품질/실청구액 비교 후 결정합니다.

## 개통 전에 공급사에 확인할 내용

다음 문구는 **문의 초안이며 아직 외부 전송하지 않았습니다.**

> 오아시스 세무회계 CRM에서 수신동의 근거를 관리하는 고객에게 방문상담 의향을 접수하는 AI 전화를 연결하려고 합니다. Business 070 회선과 자체 OpenAI Realtime 키를 이용해 REST 발신 → VoiceML Connect Stream → 당사 WSS 서버로 연결할 계획입니다. 아래 내용을 서면으로 확인 부탁드립니다.
>
> 1. 외부 VoiceML 양방향 스트림을 Business 요금제로 이용할 수 있는지, 통화료 외 AI/스트림/SIP 부가비가 있는지
> 2. 자체 OpenAI 키 사용 시 ClawOps 관리형 AI 요금이 별도로 발생하지 않는지
> 3. 부재중·안내음·자동응답·짧은 연결의 과금 시작점/초·분 단위 올림/해지·최소약정 조건
> 4. 070 발신번호 개통에 필요한 명의·사업자 서류와 안내전화 이용조건, 재수신 전화의 사람 연결 방법
> 5. VoiceML HTTP 서명키 설정, Stream mark/clear/DTMF9 실통화 지원, 서버 단절 시 종료 및 통신사 측 통화시간 상한
> 6. 음성/번호 처리 지역·보관기간·자동 녹음 여부, 장애 대응/SLA 및 외부 WSS 발신 IP

계정 가입/요금제 결제/개통 서류 제출은 사용자 확인이 필요합니다. API 키는 채팅으로 전달하지 말고 Railway 비밀 환경변수에 직접 저장합니다. 기존 개인 명의 휴대전화 번호를 발신번호로 위장하지 않습니다.

## 필요한 서버 설정

기존 Streamlit CRM을 바꾸지 않고 별도 gateway/worker 서비스를 사용합니다. 초기에는 worker를 계속 실행하지 말고 `--once`로 시험합니다.

```text
OASIS_VOICE_CALLS_ENABLED=false
OASIS_VOICE_PROVIDER=clawops
CLAWOPS_ACCOUNT_ID=<계정 식별자>
CLAWOPS_API_KEY=<서버 전용 API 키>
CLAWOPS_WEBHOOK_SIGNING_SECRET=<통신사에 설정한 별도 서명키, 32자 이상>
OASIS_VOICE_STREAM_TICKET_SECRET=<서명키와 다른 전용 난수, 32자 이상>
OASIS_VOICE_CALLER_ID=<계정에 개통한 070 번호>
OASIS_VOICE_PUBLIC_BASE_URL=https://<별도 음성 gateway 도메인>
OPENAI_API_KEY=<서버 전용 OpenAI 프로젝트 키>
OASIS_VOICE_REALTIME_MODEL=gpt-realtime-2.1
OASIS_VOICE_REALTIME_VOICE=marin
OASIS_VOICE_CLAWOPS_BILLING_CONFIRMED=false
OASIS_VOICE_CLAWOPS_LIVE_VERIFIED=false
OASIS_VOICE_TEST_NUMBERS=<소유자 동의를 받은 내부 시험번호, 쉼표 구분 최대 5개>
OASIS_VOICE_DAILY_LIMIT=1
OASIS_VOICE_MAX_CALL_SECONDS=180
SUPABASE_URL=<기존 프로젝트 URL>
SUPABASE_SECRET_KEY=<서버 전용 키; 기존 SERVICE_ROLE 설정도 지원>
```

시험번호와 키 값은 파일·Git·로그에 넣지 않습니다. 준비 상태 UI는 키의 유무/형식만 검사하며 실제 연결 성공 표시가 아닙니다. 설정은 CRM/gateway/worker에 일관되게 적용하고 비밀키는 서버에서만 사용합니다.

Gateway 실행:

```sh
uvicorn voice_calling_gateway:app --host 0.0.0.0 --port "$PORT" --no-access-log --ws-max-size 131072
```

별도 worker의 한 건 처리:

```sh
python voice_calling_worker.py --once
```

ClawOps 발신 API가 작업별 VoiceML/상태 콜백 URL을 전달합니다. 관리화면에서는 해당 070 번호에 웹훅 서명키를 설정해야 합니다. 신규 수신전화 AI 응대/자동 착신전환은 이 패치에 포함되지 않습니다. 수신전화 처리 경로도 개통 시 확인하세요.

**고객 발신 전 필수 운영 보완:** 외부 WebSocket은 인증 전에도 연결을 받아야 합니다. 현재 코드는 인증 전/후 합계 8연결, 초기 인증 10초 제한을 두지만, 미인증 연결로 슬롯을 점유하는 서비스 거부 위험은 남습니다. gateway ingress에 공급사 최신 발신 IP 허용목록과 연결수/속도 제한을 적용·검증한 후에만 고객 발신 승인 플래그를 켭니다. 임의 클라이언트가 보낸 `X-Forwarded-For`를 그대로 신뢰해 허용목록을 구현하지 마세요. IP가 바뀌는 경우의 변경 통지도 공급사에 확인합니다. 이번 로컬 패치는 운영 네트워크 설정을 변경하지 않았습니다.

## DB 변경 및 적용 순서

`supabase/migrations/20261003010404_ai_visit_calling_clawops_provider.sql`

- `oasis_voice_jobs.provider`: `twilio`/`clawops`, 기본 `twilio`. 기존 데이터/ID/연락 이력은 그대로 유지.
- `oasis_voice_worker`: 클레임 시 통신사 고정, 시험번호 큐 필터, 통신사 간 ID 혼입/연결 nonce 재사용 차단.
- 기존 security invoker, 빈 search_path, RLS, 서비스 역할 전용 RPC·브라우저 접근 차단 유지. 신규 고객 테이블 없음.
- 이전 마이그레이션은 수정하지 않습니다. 새 SQL은 CLI로 생성했으며 재실행 가능하도록 작성했습니다.

운영 반영 전 기존 스키마를 재현한 격리 Supabase에서 신규 마이그레이션 2회 실행, 이전 단계와 동일한 역할·트리거·연락기록 RPC를 재검증해야 합니다. 이번 로컬 PostgreSQL 모의 검증은 그 과정을 대체하지 않습니다. 유료 브랜치는 아직 만들지 않았습니다.

승인 순서: 공급사 과금 확인 → 별도 운영 반영 승인 → 격리 검증/마이그레이션 → 코드 배포(발신 OFF) → 키/번호 설정 → 내부 시험 한 건 → 실통화 결과 검토 → 고객 발신 별도 승인.

**시험 통과 조건:** 실제 발신번호 표시, 양방향 한국어 응대, 말 끊고 끼어들기, 키패드/음성 수신거부, 거절 후 종료, 최대시간 종료, 방문 희망 되읽기, 미응답/통신 오류 격리, 결과 1회 저장, 사용량/실청구액. ClawOps 공식 문서도 mark/clear/DTMF의 자체 실통화 검증을 권고합니다.

## 로컬 테스트

```sh
python -m pytest tests/test_voice_calling.py tests/test_voice_calling_provider.py tests/test_voice_calling_gateway.py tests/test_voice_calling_clawops.py tests/test_voice_calling_clawops_gateway.py tests/test_voice_calling_clawops_settings.py tests/test_voice_calling_ui.py tests/test_voice_calling_ui_render.py tests/test_company_sales_assignments.py tests/test_auth_security.py tests/test_sales_outreach.py tests/test_sales_outreach_repository.py -q
node tests/voice_calling_migration.integration.mjs
```

테스트는 합성 데이터와 대체 HTTP/WebSocket만 사용합니다. 실제 고객 연락처·통화·Supabase 운영 데이터는 사용하지 않습니다. 최종 결과는 같은 버전 CHANGELOG에 기록합니다.

## 롤백

먼저 `OASIS_VOICE_CALLS_ENABLED=false`, worker 중지, 공급사 콘솔에서 진행 중 통화 종료 확인 후 gateway를 내립니다. 코드만 이전 배포로 되돌립니다. 신규 provider 컬럼·통화·수신거부·감사 이력은 삭제하지 않습니다. 기존 Twilio 설정으로 전환할 경우에도 진행 중/불명확 통화가 모두 종료·정리되었는지 먼저 확인합니다. 이전 worker를 재개하기 전에 미처리 ClawOps 작업을 확인해야 합니다.

원래 작업 폴더에는 별도 미완료 변경이 있으므로 전체 `git add .`는 사용하지 않습니다. 전용 작업 폴더에서 변경 파일만 검토/커밋하고 사용자 승인 후 최신 main과 비교해 반영해야 합니다. 이 문서는 push나 운영 적용 명령을 자동 실행하지 않습니다. `RUN_v9.14.1.bat`도 발신 OFF 상태의 로컬 CRM 화면만 실행합니다.

## 공식 근거

- [ClawOps 발신 API](https://docs.claw-ops.com/api-reference/claw-ops-api/calls/create-call)
- [ClawOps 종료 API](https://docs.claw-ops.com/api-reference/claw-ops-api/calls/update-call)
- [VoiceML](https://platform.claw-ops.com/docs/build/voiceml)
- [양방향 미디어와 인증 한계](https://platform.claw-ops.com/docs/build/stream)
- [HTTP 서명](https://platform.claw-ops.com/docs/webhooks/signature-verification)
- [상태 콜백](https://platform.claw-ops.com/docs/webhooks/status)
- [OpenAI Realtime 대화](https://developers.openai.com/api/docs/guides/realtime-conversations)
- [Supabase 데이터 API 보안](https://supabase.com/docs/guides/api/securing-your-api)
