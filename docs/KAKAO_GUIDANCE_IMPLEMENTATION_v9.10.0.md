# 개인사업자 카카오톡 검토신청 안내 운영 메모

버전: `v9.10.0-kakao-guidance`

## Authentication-number separation and lifecycle

The public DB-discovery mobile number is a guidance-delivery destination only.
It is not displayed as a default authentication number and is never forwarded
to Tilko. The customer must enter the representative-owned mobile number on
the public page. The two numbers are intentionally not compared.

For the prospect self-input flow, the representative name, identity value and
authentication mobile are encrypted only inside the remote job payload. The
durable claim case does not derive `customer_ref` or `phone_masked` from that
mobile number. During authentication the database deadline is ten minutes.
Once both authentications have succeeded, the deadline can be renewed only
for an active document-collection stage and is capped by the absolute
45-minute job deadline. Terminal state transitions erase the ciphertext.

The Railway worker calls the expiry RPC on each loop. The migration also
requires `pg_cron` and registers an independent one-minute database sweep. If
the extension or schedule cannot be installed and verified, the migration
fails closed instead of leaving encrypted self-input without a database-side
expiry path. A service-role-only, PII-free health RPC reports the cron status
and overdue encrypted-job counts.

## 데이터 경계

| 정보 | 용도 | 저장 위치 | 보존 |
|---|---|---|---|
| DB발굴 공개 휴대전화 | 정보성 안내 전달 | 기존 공개 연락처 + 암호화 발송 outbox | 기존 원천 정책 / 발송 완료 시 outbox 암호문 제거 |
| 고객 직접입력 휴대전화 | 홈택스·근로복지공단 간편인증과 즉시 이어지는 2차 인증 안내 | 암호화 원격 인증 작업·최대 10분 암호화 인증 안내 outbox | 인증 10분, 작업 최대 45분; 종료·실패·취소·만료 시 암호문 제거 |
| 고객 직접입력 주민번호 | 간편인증 | 암호화 원격 인증 작업 | 작업 종료 시 암호문 제거 |
| 검토신청 링크 | 고객 직접입력 페이지 연결 | 무작위 단회 토큰의 해시 | 기본 7일 후 만료 |

공개 발송번호와 인증용 번호는 서로 같아야 할 필요가 없으며 비교하지
않습니다. 공개 번호는 고객 입력 화면이나 Tilko 요청으로 전달하지 않습니다.
인증용 번호는 장기 케이스의 `customer_ref` 재료나 `phone_masked` 값으로도
저장하지 않습니다. 인증 종료 후 완료·실패 알림을 이 번호로 다시 보내지
않으며, 결과는 보안 상태 페이지와 담당자 CRM에서 확인합니다. Railway
worker의 만료 정리와 별도로 Supabase 마이그레이션이 `pg_cron`을 필수로
활성화하고 1분 간격 DB 만료 정리를 등록·검증합니다. 이 작업을 등록할 수
없으면 마이그레이션 자체가 실패하므로 암호화 개인정보가 독립 정리 경로
없이 운영에 반영되지 않습니다.

실제 안내 발송은 공개 연락처 원문을 메시지 테이블에 저장하지 않습니다.
서버가 선택한 공개 연락처 행의 `id`와 `updated_at`만 예약 시점에 고정하고,
발송 직전에 같은 행이 변경되지 않았는지 다시 검사합니다. 번호 변경,
수신거부 또는 연락제외 변경이 있으면 기존 예약은 닫힌 상태로 차단합니다.

## 실제 발송 활성화 조건

1. Solapi에서 고정 템플릿 3종 승인
2. Railway에 템플릿 ID, Solapi 키, 채널 ID 등록
3. 안내 발송번호 HMAC 전용 키(32자 이상) 등록
4. `OASIS_KAKAO_GUIDANCE_PROVIDER_MODE=live`
5. `OASIS_KAKAO_GUIDANCE_SEND_ENABLED=true`
6. 관리자 화면의 DB 발송 스위치 활성화 및 일일 한도 확인
7. 격리 Supabase 마이그레이션·RLS·복구 검증
8. 운영 승인 후 마이그레이션, GitHub push, Railway 배포

승인 전에는 실제 발송 스위치를 켜지 않습니다. 개발/테스트 모드는 외부
발송 대기열도 만들지 않습니다.

## 롤백

- Railway 코드만 이전 커밋으로 되돌립니다.
- 신규 테이블과 발송이력은 삭제하지 않습니다.
- 관리자 DB 발송 스위치와 Railway 발송 스위치를 먼저 끕니다.
- 미발송 outbox는 취소 상태로 전환하고 암호문을 제거합니다.
- 이미 발송된 이력과 수신거부 이력은 감사 목적상 보존합니다.

## 알려진 제한사항

- Solapi API 요청 수락을 `sent`로 기록합니다. 실제 단말 수신 여부를
  `delivered`로 확정하려면 Solapi 배송결과 웹훅 검증을 별도 추가해야 합니다.
- 운영 DB 행의 인증 암호문은 10분 인증 만료 또는 최대 45분 수집 만료 시
  제거되지만, 장기 Fernet 키를 사용하는 현재 구조에서는 만료 전 생성된
  PITR·백업·WAL 사본의 암호문이 키 보유기간 동안 복호 가능할 수 있습니다.
  백업까지 암호학적으로 즉시 폐기해야 하는 정책이라면 향후 건별 envelope
  key와 외부 KMS key destruction을 도입해야 합니다. 운영 적용 전 백업
  보존·접근정책을 개인정보 처리방침과 맞춰 별도 승인합니다.
- `supabase/migrations`만으로 빈 데이터베이스를 처음부터 구성할 수는
  없습니다. 이 패치는 기존 운영 스키마를 복제한 격리 브랜치에서 검증해야
  하며, 전체 레거시 스키마 기준선 생성은 별도 작업입니다.
