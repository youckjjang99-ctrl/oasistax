# AI 방문상담 v9.14.0

## 목적과 현재 상태

정책자금 상품을 길게 설명하는 챗봇이 아니라 **방문상담 의향·희망 일정·장소를 접수하고 담당 전문가에게 넘기는 전화상담 기능**입니다.
첫 인사에서 오아시스 세무회계 AI 상담원임을 밝힙니다. 정부기관·사람으로 가장하지 않으며, 지원 대상 확정이나 승인·금액·무료 서비스를 보장하지 않습니다.

코드는 최신 `origin/main` 커밋 `7a4f703`에서 분리한 관리형 worktree에 작성했습니다. 원래 작업 폴더의 미완료 변경은 건드리지 않았습니다.

**개발·모의 검증과 격리 Supabase 검증을 완료했으며, 발신 OFF 상태의 운영 반영 대상으로 승인됐습니다.** 사용자에게 전화 회선이 아직 없으므로 Twilio 연동을 교체 가능한 최초 어댑터로 구현했습니다. 서비스 가입·번호 구매·실제 발신은 수행하지 않았습니다. 녹취의 음색/자연스러움과 비교하는 실제 음성 품질 평가는 아직 수행하지 않았습니다. 첨부 녹취 내용은 대본이나 학습데이터로 사용하지 않았으며 목소리 복제도 하지 않았습니다. 실제 배포 상태는 배포 결과 보고와 Railway 상태를 함께 확인하세요.

## 화면과 사용 순서

`주요업무 → AI 방문상담`

1. **준비 상태**: 필수 서버 설정 유무·형식 확인. 실제 연결 성공을 뜻하지 않습니다.
2. **내 영업DB 대상**: 일반 사용자는 현재 담당 업체만, 관리자는 담당 배정된 업체 전체를 페이지별로 조회합니다. 번호는 마스킹합니다.
3. **전화 안내 동의 근거 등록**: 사용자가 CRM 데이터 전체의 동의를 확보했다고 확인했습니다. 관리자가 동의 경로·일자·증빙 참조번호를 선택 업체에 일괄 기록합니다. 전화 수신동의는 번호에 결속되며 번호가 바뀌면 재확인이 필요합니다. 증빙 원문이나 개인정보를 참조번호 칸에 넣지 마세요.
4. **상담 대기 접수**: 한 번에 최대 30곳. 접수만으로 발신하지 않습니다. 기존 담당 배정과 수신거부를 다시 검사합니다.
5. **관리자 발신 승인**: 회선·서버가 준비된 뒤 건별 승인. 승인은 24시간 내에만 유효합니다.
6. 별도 worker가 평일 한국시간 09:00~18:00에 처리합니다. 기본 하루 20건, 동시 1통, 통화당 최대 180초입니다. 코드상 상한은 하루 100건·통화 300초입니다. 이 상한은 통신사 약관/법적 허용량을 뜻하지 않습니다.
7. **방문 요청·결과**: 명시적인 방문 의향·정확한 일시·장소가 확인되어야 접수됩니다. 전문가의 가용 캘린더는 아직 연결하지 않았으므로 AI는 예약 확정이 아닌 요청 접수라고 안내합니다. 담당자가 고객·전문가 양쪽에 확인한 후 화면에서 확정합니다. 같은 담당자의 이미 확정된 방문과 1시간 이내 겹치면 차단합니다.

전체 원천DB를 임의로 선점하거나 대량 복제하지 않습니다. 기존 중복연락 방지 체계와 연계하기 위해 **담당 배정 및 영업후보 연락처가 있는 업체부터** 사용합니다. 원천DB의 미배정 업체는 기존 DB발굴·배정 절차로 내 영업DB에 넣은 뒤 사용합니다.

## 대화 설계

대표자 확인 → 안내 허락 → 방문상담 의향 → 희망 날짜/시간 → 장소 → 되읽어 확인 → 결과 저장 → 전문가 후속 연락 안내.

- 한 번에 1~2문장, 질문 하나씩, 고객 응답을 기다립니다.
- 고객이 말하면 재생 중 음성을 중단하고 아직 듣지 않은 부분을 대화 문맥에서 잘라냅니다.
- 자세한 제도/금액/세무판단은 전문가에게 인계합니다.
- 단순 거절은 재설득하지 않고 종료합니다. 수신거부는 음성 또는 키패드 9번으로 처리할 수 있습니다.
- AI 인사, 정책자금 가능성의 불확실성, 민감정보 요구 금지 규칙은 일반 사용자가 변경할 수 없습니다.
- 원본 음성·전체 전사·인증정보를 저장하지 않습니다. 업무 요약 및 요청 일정만 권한이 있는 담당자에게 저장합니다.

## 구성 파일

- `app.py`: 기존 로그인 이후 메뉴·화면 연결만 추가
- `voice_calling.py`: 대본, 반문 대응, 설정, 번호/일정 검증
- `voice_calling_ui.py`: Streamlit 관리 화면
- `voice_calling_repository.py`: RPC 어댑터와 오류 원문 차단
- `voice_calling_worker.py`: 승인 대기열 처리·발신·불명확 결과 격리
- `voice_calling_provider.py`: Twilio REST 및 서명·작업 티켓
- `voice_calling_gateway.py`: 별도 FastAPI 서비스, Twilio ↔ OpenAI Realtime 양방향 음성
- `supabase/migrations/20261001235500_ai_visit_calling.sql`: 추가 테이블·인덱스·권한·RPC
- `requirements.txt`, `VERSION.txt`
- `tests/test_voice_calling*.py`, `tests/voice_calling_migration.integration.mjs`
- `tests/voice_calling_staging.verify.sql`: 빈 검증용 Supabase 전용, 실제 기존 RPC 연동·권한 검증 후 전부 롤백
- `RUN_v9.14.0.bat`, 이 문서, `docs/CHANGELOG-ai-visit-calling-v9.14.0.md`

## DB 변경과 보존

새 테이블 4개:

| 테이블 | 주요 내용 |
|---|---|
| `oasis_voice_permissions` | 회사 UID, 실제 전화번호, 동의 종류·근거·일자·유효기간·철회일, 작성자 |
| `oasis_voice_suppressions` | 회사/동일 번호 전역 연락금지, 사유, 작성자 |
| `oasis_voice_jobs` | 기존 배정/후보/연락처 FK, 담당자, 요청 멱등키, 승인·발신·결과, 방문희망·확정 |
| `oasis_voice_events` | 원문 개인정보 없는 작업 감사이력 |

네 테이블 모두 RLS 및 인덱스·생성/갱신 시각을 갖습니다. 기존 프로젝트는 Supabase Auth가 아니라 `oasis_users` 기반 서버 인증이므로 `anon`/`authenticated`의 테이블·RPC 접근을 전부 차단하고, service-role 전용 RPC에서 활성 계정과 실제 담당자를 검증합니다. 서비스키는 브라우저로 전달하지 않습니다. 감사/수신거부 이력은 서비스 역할에도 UPDATE/DELETE 권한을 주지 않습니다. 고객·기존 업체 ID·수집 상태·문서를 변경하거나 삭제하지 않습니다.

기존 `oasis_prospect_contacts`에는 수신거부된 번호만 대상으로 한 보조 인덱스 하나를 추가합니다. 연락처 수집 대기열 필드는 변경하지 않습니다. 운영 적용 시 인덱스 생성 중 잠금 시간을 스테이징에서 확인하고 전화번호 수집 시간과 겹치지 않게 적용하세요.

통화결과는 기존 연락기록 RPC로 한 번 연결합니다. 기존 연락기록 저장 오류가 발생해도 고객 수신거부/방문 요청은 롤백하지 않고 `CONTACT_SYNC_PENDING`으로 남깁니다. 수신거부·거절·번호오류는 재발신을 막으며 동의 재등록으로 자동 해제되지 않습니다. 해제 정책은 별도 관리자 검토가 필요합니다.

## 발신 서비스 연결 / 배포 전 필수 사항

1. 국내 전화 발신이 가능한 사업자/회선을 선정하고 발신번호 등록·표시, 광고전화 이용조건, 녹음/국외처리/개인정보 고지를 확인해야 합니다. Twilio가 사용자가 원하는 국내 발신번호/요금/품질을 제공한다고 아직 검증한 것은 아닙니다. 국내 공급자를 선택하면 어댑터와 미디어 연동을 맞춰야 합니다.
2. API 이용료·회선/통화료는 ChatGPT 구독료와 별개입니다. 이번 작업에서 유료 API 통화는 실행하지 않았습니다.
3. 격리된 Supabase 스테이징에서 마이그레이션을 2회 이상 적용하고 실제 기존 RLS/트리거/연락기록 연계를 검증합니다. 2026-10-02 검증은 완료했습니다. 기존 마이그레이션 이력에 초기 테이블 생성 이력이 빠져 자동 재생이 실패하므로 운영 public 스키마만 별도 읽기 조회하여 테이블 86개, 함수 195개, 제약조건 471개, 인덱스 253개, 트리거 58개와 뷰·권한을 빈 임시 브랜치에 재현했습니다. 고객 행 데이터·Storage 문서·운영 비밀값은 복사하지 않았습니다. 기존 전체 마이그레이션 이력의 재현성 보완은 별도 과제입니다.
4. 별도 Railway 음성 gateway 서비스와 worker 서비스를 배치합니다. 기존 Streamlit 서비스나 경정청구 gateway의 실행 명령을 바꾸지 마세요.

gateway 명령:

```sh
uvicorn voice_calling_gateway:app --host 0.0.0.0 --port "$PORT" --no-access-log
```

worker 명령:

```sh
python voice_calling_worker.py
```

서버 환경변수 (실제 값은 비밀관리 설정에 입력, 코드/채팅/Git에 넣지 않음):

```text
SUPABASE_URL
SUPABASE_SECRET_KEY 또는 SUPABASE_SERVICE_ROLE_KEY
OASIS_VOICE_CALLS_ENABLED=false
OASIS_VOICE_PROVIDER=twilio
TWILIO_ACCOUNT_SID
TWILIO_AUTH_TOKEN
OASIS_VOICE_CALLER_ID=등록된 E.164 발신번호
OASIS_VOICE_PUBLIC_BASE_URL=https://별도-음성-gateway-주소
OPENAI_API_KEY
OASIS_VOICE_REALTIME_MODEL=gpt-realtime-2.1
OASIS_VOICE_REALTIME_VOICE=marin
OASIS_VOICE_STREAM_TICKET_SECRET=전용 난수 32자 이상
OASIS_VOICE_DAILY_LIMIT=20
OASIS_VOICE_MAX_CALL_SECONDS=180
```

처음에는 `false` 유지 → 본인 테스트 번호/본인 동의 등록 및 대기 접수 → worker 정지와 기존 승인 대기 0건 확인 → 검증 시간에만 앱·gateway·worker 설정을 `true`로 변경 → 본인 시험 한 건만 승인 → `python voice_calling_worker.py --once` 실행 순서로 진행합니다. 시험이 끝나면 다시 `false`로 전환합니다. 승인 버튼은 준비 상태가 아니면 비활성화되므로 `false` 상태에서 승인할 수 없습니다. 고객 대량 발신은 본인 시험통화의 번호 표시·지연·끼어들기·수신거부·한도·종료를 검증하고 운영자의 승인을 받은 뒤 시행합니다. voice 서비스에 디버그/프레임 로깅을 켜지 마세요.

## 장애 처리

- 전화 생성 응답이 타임아웃이면 상대에게 이미 전화가 갔을 수 있어 `unknown`으로 격리하고 자동 재시도하지 않습니다.
- 오래 열린 작업이나 음성 서비스 오류도 격리됩니다. 미확인 작업이 있으면 전체 신규 발신을 보수적으로 멈춥니다.
- 관리자가 전화 서비스 관리화면에서 통화 종료/미생성을 확인한 뒤 근거 참조번호를 남겨 해당 작업을 종료할 수 있습니다. 이 처리는 작업을 다시 대기열에 넣지 않으며 수신거부도 해제하지 않습니다.
- 수신거부 저장 중 장애가 나면 별도 차단 이력을 남기고 관리자 확인 상태를 유지합니다. DB 자체가 완전히 중단된 경우 실제 저장을 보장할 수 없으므로 오류 작업을 자동 재개하지 않습니다.

## 검증 및 한계

- Python 모의/회귀 테스트: 신규 기능 77개 + 기존 배정·인증·영업메시지 관련 103개 + 실제 Streamlit 페이지 렌더링 1개 = 181개 통과.
- 격리 PostgreSQL: 실제 마이그레이션 2회 적용, 보존·권한·동의·중복·콜백 경합·철회·담당변경·관리자 복구 등 48개 검사 통과. 기존 테이블과 연락 RPC는 합성 fixture이므로 운영과 동일한 Supabase 스테이징 검증을 대체하지 않습니다.
- 격리 Supabase: 운영 public 스키마 재현 후 실제 마이그레이션 재실행 성공. 합성 행만 사용하여 실제 서비스 역할/브라우저 역할 권한, 담당자별 조회, 동의, 중복 요청, 승인, 기존 연락기록 RPC 연계, 방문 확정과 철회를 검증했습니다. 검증 트랜잭션은 롤백했습니다.
- Supabase 보안 검사: 신규 테이블의 RLS는 켜고 브라우저 역할 권한은 차단했습니다. `RLS Enabled No Policy` 정보 알림은 기존 서버 전용 접근 구조에 따른 의도된 기본 거부입니다. [공식 검사 설명](https://supabase.com/docs/guides/database/database-linter?lint=0008_rls_enabled_no_policy)
- 외부 API 요청은 테스트에서 대체했습니다. API 계정 권한, 모델 사용 가능 여부, 국내 전화 연결, 실제 지연과 음성 품질은 미검증입니다.
- 전문가 캘린더 자동 연동, 녹음/전체 전사 보존, 자동 문자 발송, 원천DB 무배정 자동발신, 상담 획득률 최적화 학습은 이번 범위에 포함하지 않습니다.
- 모델이 음성을 잘못 이해할 가능성은 남습니다. 중요 일정/장소는 AI가 재확인하고 사람이 최종 확정합니다. 수신거부에는 키패드 9번 경로도 제공합니다.

## 테스트 명령

```sh
python -m pytest tests/test_voice_calling.py tests/test_voice_calling_ui.py tests/test_voice_calling_provider.py tests/test_voice_calling_gateway.py tests/test_voice_calling_ui_render.py tests/test_company_sales_assignments.py tests/test_auth_security.py tests/test_sales_outreach.py tests/test_sales_outreach_repository.py -q
# @electric-sql/pglite 0.3.14를 테스트용 별도 폴더에 설치한 경우:
# PGLITE_PACKAGE=file:///absolute/path/node_modules/@electric-sql/pglite/dist/index.js
node tests/voice_calling_migration.integration.mjs
```

## 롤백

`OASIS_VOICE_CALLS_ENABLED=false`로 변경하고 worker를 정지합니다. 진행 중인 통화는 통신사 관리화면에서 종료하고 gateway를 내립니다. 앱 코드만 이전 배포로 되돌립니다. 신규 테이블과 상담/수신거부/감사이력은 **삭제하지 않습니다**. 패치는 최신 main 기준이므로 예전 브랜치의 변경된 `app.py` 위에 바로 덮어쓰면 안 됩니다. 먼저 Git에서 최신 main 및 기존 로컬 변경 보존 여부를 확인하세요.

사용자의 배포 승인 후 전용 작업 폴더에서 변경 파일만 검토하여 커밋·배포합니다. 기존 작업 폴더는 별도 미완료 변경이 있으므로 전체를 `git add .` 하거나 패치를 바로 덮어쓰지 마세요. 운영 DB 적용·GitHub push·Railway 확인 결과는 별도 배포 보고에서 확인합니다. 전화 회선 개통과 실제 발신 활성화는 별도 절차입니다.

## 확인한 공식 문서

- [OpenAI Realtime 대화·음성·도구 호출](https://developers.openai.com/api/docs/guides/realtime-conversations)
- [Twilio 양방향 미디어 메시지](https://www.twilio.com/docs/voice/media-streams/websocket-messages)
- [Twilio 발신 Call API](https://www.twilio.com/docs/voice/api/call-resource)
- [Twilio 웹훅 서명 검증](https://www.twilio.com/docs/usage/webhooks/webhooks-security)
- [Supabase Data API 보안](https://supabase.com/docs/guides/api/securing-your-api)
- [KISA 불법스팸 방지 안내 자료실](https://spam.kisa.or.kr/spam/na/ntt/selectNttList.do?bbsId=1002&mi=1020)
