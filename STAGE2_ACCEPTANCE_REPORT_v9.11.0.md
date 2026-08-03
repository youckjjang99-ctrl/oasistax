# OASIS CRM 2단계 고객정보 통합 검증 보고서

- 버전: `v9.11.0-customer-integration`
- 실행 시각: 2026-08-04 02:00–03:00 KST
- 기준 소스: GitHub `origin/main` `3ea518f7a01ed75caf95fb36012945b4828d5597`
- 범위: 2단계 고객정보 통합만 수행
- 현재 상태: 운영 DB 완료, GitHub/Railway 배포 검증 중

## 승인 기준 결과

| 게이트 | 결과 | 증빙 |
|---|---|---|
| 멱등 마이그레이션 | 통과 | 격리 환경 동일 SQL 2회 성공 |
| 단위·통합 테스트 | 통과 | 고객 병합·RPC·outbox·소유자 격리 전용 테스트 포함 |
| 전체 회귀 테스트 | 통과 | `652 passed, 1 skipped, 22 subtests passed` |
| 데이터 건수 비교 | 통과 | 운영 핵심 11개 표 전후 동일, 보호 표 감소 0 |
| 안정 식별자 보존 | 통과 | 고객 UUID·영업 `company_uid` 전후 지문 동일 |
| 백업·복구 | 통과 | 격리 관련 13개 표 논리 백업→복원 건수·지문 동일 |
| RLS·권한 | 통과 | 신규 2개 표 FORCE RLS, 익명/인증 역할 RPC 실행 불가 |
| 개인정보 검사 | 통과 | 변경 10개 파일 Privacy Guard 통과 |
| 금지 영역 회귀 | 통과 | 경정청구·Tilko·전화수집 전용 테스트 포함 전체 회귀 통과 |

## 운영 데이터 비교

운영 적용 전후 다음 핵심 표의 건수와 안정 식별자 지문이 모두 같았다.

| 표 | 적용 전 | 적용 후 |
|---|---:|---:|
| `oasis_customers` | 14 | 14 |
| `oasis_crm` | 7 | 7 |
| `oasis_financials` | 15 | 15 |
| `oasis_registry` | 3 | 3 |
| `oasis_matching_preferences` | 12 | 12 |
| `oasis_customer_history` | 174 | 174 |
| `oasis_consultation_journals` | 5 | 5 |
| `oasis_customer_trash` | 2 | 2 |
| `oasis_stock_valuations` | 3 | 3 |
| `oasis_prospect_companies` | 349 | 349 |
| `oasis_company_sales_assignments` | 352 | 352 |

대용량 보호 표도 감소하지 않았다: 전화수집 663,026건, 자격 원천 933,831건, NPS 1,182,877건, 고용 원천 2,204,930건. 수집 run/progress/outbox 건수와 상태 지문은 동일했다. 전화수집 표의 상태 지문은 검증 창 동안 실행 중인 백그라운드 작업이 최근 5분 내 3,641행을 갱신해 변했지만, 이번 SQL은 해당 표를 참조하지 않으며 행 수 감소는 없었다.

## 데이터 연결 결과

- 8개 기존 연관 표 모두 `customer_id` nullable 복합 FK, 인덱스, 미래 행 연결 트리거를 갖는다.
- 운영의 단일 정확 일치 행만 연결되었고 참조 무결성 위반은 0건이다.
- 영업 `company_uid` 교차 연결은 운영 데이터에 소유자+사업자번호 단일 정확 일치가 없어 0건이며, 기존 UID는 그대로 유지했다.
- 이름·주소 기반 자동 통합은 수행하지 않았다.

## 배포 판정

운영 Supabase는 통과했다. GitHub `main` 반영과 Railway 배포가 확인될 때까지 이 보고서는 `배포 검증 중`이며 3단계로 진행하지 않는다.
