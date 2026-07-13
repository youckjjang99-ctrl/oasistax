# CHANGELOG v4.3.1

## Streamlit Cloud 고객DB 자동복원

- Streamlit Cloud 재배포 후 로컬 고객DB가 비어 있으면 Supabase 고객자료 자동조회
- 기존 고객DB 템플릿 구조를 유지해 로컬 고객DB 자동복원
- 고객관리·정책자금 매칭·AI 컨설팅 리포트에서 기존 고객목록 재사용
- 홈 화면에 자동복원 완료 메시지 표시
- 기존 로컬 고객이 있으면 절대 덮어쓰지 않음
- Supabase 자료는 읽기 전용으로 조회
