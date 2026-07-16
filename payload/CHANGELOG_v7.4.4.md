# CHANGELOG v7.4.4

## 사전점검 오탐 수정
- 함수·클래스뿐 아니라 상수·변수 선언도 Import 심볼로 인식
- Assign, AnnAssign, import 별칭 검사 추가
- ACTION_OPTIONS, STATUS_OPTIONS, PIPELINE_OPTIONS, PRIORITY_OPTIONS,
  INTEREST_OPTIONS, ROOT_DIR 오탐 해결
- 금지 문자열 단순검색 제거
- 실제 st.columns(vertical_alignment=...) 호출만 AST로 검사
- 검사기 자체 문구를 오류로 잡던 문제 해결
- Widget key 중복은 경고로만 표시
- 자동 롤백 유지
- 관리자 시스템 관리 화면에 사전점검 결과 추가

## 데이터 영향
- 고객DB·CRM·상담일지·직원현황·정관·정책자금 영향 없음
- Supabase SQL 실행 불필요
