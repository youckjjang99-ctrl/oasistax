# OASIS CRM v9.10.0-kakao-guidance

## Security hardening: representative-entered authentication data

- The public mobile number found by DB discovery is used only to deliver the
  review guidance message. It is never copied into a Hometax or COMWEL
  simple-auth request.
- The customer enters the representative-owned authentication mobile number
  on the public review page. A difference from the public delivery number is
  accepted and is not treated as an error.
- The authentication phone and identity values remain only in the encrypted
  remote-job payload. They are excluded from durable `customer_ref` and
  `phone_masked` fields for this self-input flow.
- `sensitive_expires_at` enforces a 10-minute authentication-stage deadline.
  After both authentications succeed, it may be renewed only while document
  collection actively requires the authenticated context, never beyond the
  45-minute hard workflow deadline.
- Terminal, cancelled, failed, and expired jobs clear the encrypted payload
  and set the sensitive deadline to `NULL`. A database expiry RPC and a
  mandatory one-minute `pg_cron` sweep also purge it if the Railway worker
  stops. Migration aborts if the independent schedule cannot be installed and
  verified.

## 추가

- DB발굴의 확정 개인사업자·공개 휴대전화 보유·현재 담당 업체에 한해
  개인사업자 검토신청 카카오톡 안내를 개별 예약할 수 있습니다.
- 고용지원금, 정책자금, 세액공제 안내 문구는 승인 템플릿으로 고정하며
  영업사원이 본문을 변경할 수 없습니다.
- 추측 불가능한 7일 유효 검토신청 링크와 고객 직접입력 화면을
  기존 홈택스→근로복지공단 인증·자료수집 흐름에 연결했습니다.
- 7일 동일 유형 중복 방지, 일일 한도, 수신거부·연락제외, 관리자 차단,
  발송이력과 3영업일 후속업무를 추가했습니다.
- 후속업무는 영속 대기열과 `(task_type, source_id)` 정본 키로 중복 없이
  생성되며 Railway 재시작 후에도 이어집니다.

## 개인정보·인증정보 보호

- DB발굴 공개 휴대전화 번호는 안내 메시지 전달에만 사용합니다.
- 고객 링크에는 공개 업체명·전화번호·사업자번호 등 원천 DB 정보를
  넣거나 자동 입력하지 않습니다.
- 기본 서비스뿐 아니라 사용자 정의 공개 게이트웨이 adapter가 오래된
  `recipient_name`·`recipient_phone`을 반환하더라도 self-input 흐름에서는
  두 값을 강제로 비워 자동 입력을 차단합니다.
- 홈택스·근로복지공단 간편인증은 고객이 직접 입력한 대표자 이름,
  생년월일/주민번호, 대표자 본인 명의 휴대전화만 사용합니다.
- 공개 발송번호와 고객 입력 인증번호가 달라도 오류로 처리하지 않습니다.
- 인증용 개인정보는 공개 연락처와 분리된 암호화 임시 작업에만 보관하고,
  완료·실패·취소·만료 시 복구 가능한 암호문을 제거합니다.
- 인증용 휴대전화는 장기 케이스 식별 해시와 마스킹 표시값에서 제외하고,
  즉시 이어지는 2차 인증 안내 outbox도 최대 10분만 보관합니다. 인증 종료
  후 완료·실패 알림의 수신번호로 재사용하지 않습니다.
- Railway worker와 별도로 Supabase의 `pg_cron`을 필수 활성화하고, 1분 간격
  암호문 만료 정리 작업을 등록·검증합니다. 등록 실패 시 마이그레이션을
  중단합니다.
- URL, 화면 오류, 테스트 결과와 일반 로그에는 원문 개인정보를 남기지
  않습니다.

## 검증 환경

- 공개 모바일 신청 게이트웨이의 HTTP 회귀 테스트가 배포 의존성 설치
  환경에서 누락되지 않도록 `httpx` 의존성을 명시했습니다.

## 발송 안전장치

- 실제 발송은 Solapi 승인 템플릿, Railway 발송 스위치, 관리자 DB 스위치가
  모두 활성화된 경우에만 허용합니다.
- 문자 대체발송은 항상 비활성화합니다.
- 실제 수신번호는 화면/호출자가 넘긴 값을 신뢰하지 않고 해당 업체의
  공개 연락처를 서버가 다시 확인하여 선택합니다.
- 예약 시 공개 연락처 행의 비식별 버전(`id`, `updated_at`)을 고정하고,
  발송 직전에 다시 비교하여 번호 변경·수신거부 변경 뒤의 오래된 예약을
  차단합니다.
- 대기열 임대 후 발송 직전에 취소·수신거부·관리자 차단 상태를 서버에서
  다시 확인합니다.
- 취소 또는 수신거부 시 연결된 안내 발송 대기열의 암호문과 임대를 함께
  정리합니다.

## 데이터베이스

- `supabase/migrations/20260803090000_v910_kakao_guidance.sql`
- `supabase/migrations/20260803092000_v910_task_automation.sql`

신규 테이블에는 RLS를 적용하고 직접 `anon`/`authenticated` 접근을
허용하지 않습니다. 운영 적용 전에는 운영 스키마를 복제한 격리 Supabase
브랜치에서 두 번 적용 및 복구 검증을 완료해야 합니다.

## 배포 상태

- 로컬 코드와 테스트 패치 작성 완료
- 실제 카카오톡 발송 없음
- 운영 Supabase 마이그레이션 미적용
- GitHub push 및 Railway 운영 배포 미실행

## 알려진 제한사항

- 운영 행의 인증 암호문은 짧은 TTL 뒤 제거되지만, 만료 전 생성된
  PITR·백업·WAL 사본은 장기 암호화 키 보유기간 동안 복호 가능할 수
  있습니다. 백업까지 즉시 암호학적으로 폐기해야 한다면 건별 envelope key와
  외부 KMS key destruction이 후속 과제로 필요합니다.
