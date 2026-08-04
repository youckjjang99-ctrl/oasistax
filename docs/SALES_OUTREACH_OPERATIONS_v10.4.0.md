# 저장된 영업후보 개별 발송 운영 가이드

## 적용 범위

- 저장된 영업후보 표는 업체명, 사업자번호, 사업자유형, 연락처, 이메일,
  인스타, 업종명, 가입자, 고용증가값과 이메일·문자·카카오톡 발송 버튼만
  표시합니다.
- 각 버튼은 한 업체만 대상으로 하는 자유작성 창을 엽니다.
- 발송 직전에 현재 담당자 배정, 최신 수신처, 수신거부 여부를 다시 확인합니다.
- 기존 개인사업자 검토신청 카카오톡 안내 화면은 DB발굴 화면에서 제거합니다.
- `oasis_prospect_outreach_outbox`에는 담당자·채널·요청/처리 시각·안전한
  결과코드만 자동 기록합니다. 수신처, 업체명, 제목·본문, 공급자 원응답/ID,
  녹음파일, 증빙파일과 경로는 저장하지 않습니다.
- 문자·카카오톡은 공급자 접수 확정 뒤 기존 CRM 연락이력도 기록합니다.
  이메일은 `email_sent` 결과가 아직 없으므로 `연결됨`으로 잘못 기록하지 않고
  자동 발송 이력과 하이웍스 발송내역에서 확인합니다.

## 기본 안전 상태

`OUTREACH_ENABLED`와 `OUTREACH_COMPLIANCE_CONFIRMED`가 모두 명시적으로
활성화되지 않으면 세 채널 모두 외부 요청을 보내지 않습니다. 영구 중복방지
outbox가 있더라도 공급자 계약·광고 수신동의 운영·무료 수신거부가 준비되기
전에는 두 값을 활성화하면 안 됩니다. 코드에는 운영용 모의 발송 경로가
없습니다.

담당자는 전화로 동의를 확인하고 화면의 확인 체크 1개만 누릅니다. 통화
녹음은 CRM 밖에서 보관하므로 발송 때마다 녹음이나 증빙을 업로드하지
않습니다. 이 체크값은 법적 증빙처럼 DB에 저장하지 않으며, 실제 녹음의
보존·접근·파기 정책은 별도로 운영합니다.

수신 동의와 광고성 정보 전송 요건을 확인한 담당자만 발송할 수 있습니다.
담당자는 제목과 본문을 자유롭게 쓰고, 시스템이 이메일 제목의 `(광고)`와
전송자 명칭·이메일·전화·주소·수신거부 안내를 자동으로 붙입니다. 문자도
본문 시작의 `(광고)`·전송자 명칭과 끝부분의 발신 연락처·무료수신거부 번호를
자동으로 붙입니다. 카카오톡은 광고 플래그를 강제로 사용하고 SMS 대체 발송은
하지 않습니다. 문자·카카오톡 영업 발송은 한국시간 08:00 이상, 21:00 미만에만
허용합니다.

공통 환경변수는 다음과 같습니다.

- `OUTREACH_ENABLED`: 모든 채널의 최종 외부 발송 게이트
- `OUTREACH_COMPLIANCE_CONFIRMED`: 대상별 사전 수신동의와 수신거부 운영을
  실제로 갖춘 뒤에만 켜는 준법 게이트
- `OUTREACH_SENDER_NAME`: 문자·이메일에 자동 표시할 전송자 명칭
- `OUTREACH_SENDER_EMAIL`, `OUTREACH_SENDER_PHONE`,
  `OUTREACH_SENDER_ADDRESS`: 이메일 하단에 자동 표시할 전송자 정보
- `OASIS_KAKAO_GUIDANCE_PHONE_HASH_KEY`: 32자 이상의 비밀키. 기존 전화번호
  해시 수신거부 확인과 새 발송의 수신처·본문 HMAC 중복판정에 함께 쓰며,
  실제 값은 Railway 비밀변수에만 둡니다.

## 중복방지와 자동 발송 이력

발송은 `reserved → dispatching → provider_accepted/provider_rejected/
delivery_unknown` 순서로만 진행합니다. 브라우저 세션이 아니라 Supabase의
원자적 RPC가 최초 요청 1건에만 발송 토큰을 내주므로 Railway 재시작·다중
인스턴스·반복 클릭에도 같은 요청을 다시 보내지 않습니다.

`delivery_unknown`은 시간 경과로 자동 해제하거나 재시도하지 않습니다.
관리자가 공급자 발송내역을 대조한 뒤 `provider_accepted` 또는
`confirmed_not_sent`로 확정해야 새 발송 판단이 가능합니다. 이 관리자 확인은
담당자와 시각만 기록하며 공급자 원문이나 개인정보는 저장하지 않습니다.

## 채널별 관리자 설정

### 문자코리아

먼저 법인 API 이용 승인, API 키 발급, 발신번호 사전등록을 완료합니다.

- `SMSKOREA_USER_ID`
- `SMSKOREA_SEC_API_KEY`
- `SMSKOREA_SENDER`
- `SMSKOREA_MESSAGE_SECRET_MODE`: 승인받은 계정 안내에 따라 `include` 또는
  `omit` 중 하나를 명시합니다.
- `OUTREACH_SMS_FREE_OPT_OUT_NUMBER`: 사전 등록하고 실제 운영되는 무료
  수신거부용 080 번호

공개 예제 사이에 발송 본문의 보안 키 포함 여부가 달라 운영 계정 담당자에게
요청 형식을 확인해야 합니다. 본문은 EUC-KR 기준 90 bytes 이하면 SMS,
그보다 길면 LMS로 접수하며 2,000 bytes를 넘으면 차단합니다.

공식 문서: <https://www.smsko.co.kr/api_desk/api_help.php>

### 하이웍스

하이웍스 최고 관리자가 메일 발송 권한을 포함한 Office Token을 만들고 허용
IP를 제한한 뒤 다음 값을 설정합니다.

- `HIWORKS_OFFICE_TOKEN`
- `HIWORKS_USER_ID`
- `OUTREACH_EMAIL_OPT_OUT_TEXT`: 모든 영업 이메일 본문에 실제로 넣을
  수신거부 안내 문구

공식 문서: <https://developers.hiworks.com/docs>

### 카카오톡

카카오 비즈니스 채널의 일반 공개 REST API에는 임의 메시지 발송 기능이
없습니다. 현재 어댑터는 별도 계약이 필요한 카카오 i 커넥트 메시지의 친구톡
규격을 사용합니다. 다른 공식 딜러를 계약했다면 그 딜러 규격에 맞춰 어댑터를
교체해야 합니다.

- `KAKAO_BIZ_CLIENT_ID`
- `KAKAO_BIZ_CLIENT_SECRET`
- `KAKAO_BIZ_SENDER_KEY`
- `KAKAO_BIZ_SENDER_NO`
- `KAKAO_BIZ_TEMPLATE_CODE`
- `KAKAO_BIZ_CONTRACT_CONFIRMED=true`: 실제 계약과 발신 프로필 승인을
  확인한 뒤에만 설정합니다.

친구톡은 해당 채널 친구이면서 수신거부가 아닌 대상에게만 사용합니다.

공식 문서:

- <https://developers.kakao.com/docs/ko/kakaotalk-channel/rest-api>
- <https://docs.kakaoi.ai/kakao_i_connect_message/bizmessage/api/api_reference/>

## 현재 활성화 금지와 향후 순서

1. 각 공급자 계약·승인과 발신번호 또는 발신 프로필 등록을 완료합니다.
2. 운영 비밀 저장소에 필요한 값을 등록하되 로그나 저장소에는 값을 남기지
   않습니다.
3. 공개된 연락처를 수신동의로 간주하지 말고 전화 동의 녹음의 별도 보관·접근·
   파기 절차를 확정합니다. CRM에는 녹음이나 증빙 경로를 넣지 않습니다.
4. `OUTREACH_ENABLED=false`와 `OUTREACH_COMPLIANCE_CONFIRMED=false` 상태에서
   화면과 설정 누락 안내를 확인합니다.
5. 수신 동의가 명확한 내부 검증용 수신처로 채널별 단건 검증 계획을 세우고,
   개인정보가 아닌 상태값만 로그에 남는지 검토합니다.
6. 위 선행조건을 별도 검토한 뒤에만 두 게이트를 활성화하고 단건 검증합니다.
7. 외부 서비스에서 접수·최종 전달 이력과 OASIS CRM 연락이력을 함께
   확인합니다. 응답이 불명확하면 재발송하지 말고 공급자 이력을 먼저
   확인합니다.
