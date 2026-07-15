# CHANGELOG v7.4.1

## 앱 시작 ImportError 긴급복구

### 원인
- v7.2.0 고객관리 화면이 사용하는 함수명과
  v7.4.0에 남아 있던 고객관리 모듈 함수명이 서로 불일치
- `enterprise_center.py`가 다음 함수를 불러오지 못해 앱 시작 실패
  - `confirm_delete_dialog`
  - `filter_active_customers`
- `app.py`가 `render_customer_trash_page`를 불러오지 못할 가능성도 함께 복구

### 수정
- 기존 `enterprise_customer_management.py`에 호환 함수 추가
- 기존 기업 검색창 유지
- 작은 × 삭제버튼과 확인창 유지
- 좌측 휴지통 메뉴와 복원 기능 유지
- v7.4.0 직원현황·4대보험 명부·고용지원금 연동 유지
- 비밀번호 변경 기능 유지

### 데이터 영향
- 고객DB 삭제·초기화 없음
- CRM·상담일지·정관·정책자금·기업히스토리 영향 없음
- 직원현황 데이터 영향 없음
- Supabase SQL 추가 실행 없음
