# v3.6.0 적용방법

1. 압축 안의 모든 내용을 기존 `정책자금자동화` 폴더에 풉니다.
2. `RUN_OASIS_V360_UPDATE.cmd`를 더블클릭합니다.
3. `[SUCCESS]`가 나오면 실행합니다.

```powershell
streamlit run app.py
```

## 변경된 분석 구조

- Streamlit 앱 프로세스와 PDF 분석 프로세스를 분리
- 모든 페이지를 순차 탐색
- 고정 페이지 번호 사용 안 함
- 사업자번호를 찾은 뒤 중복 여부 우선 확인
- 신규 고객만 전체 재무정보 분석
- 분석 프로세스가 segmentation fault로 종료돼도 앱 본체 유지

정상 확인 후 GitHub에 반영합니다.

```powershell
git add .
git commit -m "v3.6.0 크레탑 분석 엔진 분리"
git push
```
