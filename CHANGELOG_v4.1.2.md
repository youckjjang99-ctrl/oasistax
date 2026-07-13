# CHANGELOG v4.1.2

## 고객별 정책자금 매칭키워드 관리

### 신규 파일
- matching_preferences.py

### 수정 파일
- app.py
- registered_policy_match.py
- main.py
- VERSION.txt

### 기능
- 크레탑 자동등록 화면에 매칭키워드 입력
- 관심지원분야 다중선택
- 제외키워드 입력
- 자금사용목적·투자예정금액·투자예정시기 입력
- 정책자금 매칭 화면에서 고객별 설정 조회·수정
- 고객별 설정은 사업자등록번호 기준 별도 JSON 저장
- 선택 고객용 임시 매칭파일에만 키워드 반영
- 사용자 제외키워드가 포함된 공고는 추천 결과에서 제외

### 데이터 보호
- 기존 고객DB와 고객리스트를 수정하지 않음
- 저장 파일: 회원별 customer_matching_preferences.json
- 기존 정책자금 매칭 및 엑셀 업로드 방식 유지
