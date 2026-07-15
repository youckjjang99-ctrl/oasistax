# CHANGELOG v7.4.2

## 기업컨설팅 화면 렌더링 오류 긴급복구

### 원인
- `st.columns()`의 `vertical_alignment` 값으로 `"end"` 사용
- 현재 Streamlit은 `"top"`, `"center"`, `"bottom"`만 허용
- 기업컨설팅 화면 진입 시 `StreamlitInvalidVerticalAlignmentError` 발생

### 수정
- `vertical_alignment="end"`를 `vertical_alignment="bottom"`으로 변경
- 프로젝트 루트의 다른 Python 파일에도 동일 값이 있는지 자동 검사
- 동일한 잘못된 값이 발견되면 함께 안전하게 교체
- 기업컨설팅 검색창과 관리할 기업 제목의 하단 정렬 유지

### 유지 기능
- 직원현황 및 4대보험 가입자명부 업로드
- 고용지원금 매칭 연동
- 비밀번호 변경
- 기업검색·삭제·휴지통·복원
- 기존 고객DB·CRM·상담일지·정관·정책자금 데이터

### 데이터 영향
- 없음
- Supabase SQL 추가 실행 없음
