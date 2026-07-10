# 정책자금자동화 v2.1 Change Log

## 추가 기능
- 회원가입 기능 추가
- 회원별 로그인 세션 관리 추가
- 회원별 업로드 파일 저장 경로 분리
- 회원별 결과파일 저장 경로 분리
- 회원별 실행이력 저장 경로 분리
- 고객DB 업로드 시 `고객DB누적.xlsx` 자동 누적 저장
- 로그인한 회원의 누적 고객DB 다운로드 버튼 추가

## 기존 기능 유지
- 고객DB 양식 다운로드 유지
- 고객DB 업로드 검증 유지
- 정책자금 매칭 실행 방식 유지
- 업체별 TOP3 미리보기 유지
- 결과 엑셀 다운로드 유지
- 담당자별 실행횟수 통계 유지

## 수정 파일
- `app.py`
- `auth.py`
- `utils.py`
- `history.py`
- `VERSION.txt`
- `CHANGELOG_v2.1.md`

## 저장 구조
```text
user_data/
 └─ 회원ID/
     ├─ 고객DB누적.xlsx
     ├─ uploads/
     ├─ results/
     └─ history/
         └─ 실행이력.xlsx
```

## 주의사항
- 기존 `APP_LOGIN_ID`, `APP_LOGIN_PW` 값은 기본 관리자 계정으로 자동 생성됩니다.
- 운영 중인 Streamlit에 적용하려면 GitHub에 수정 파일 업로드 후 재배포가 필요합니다.
