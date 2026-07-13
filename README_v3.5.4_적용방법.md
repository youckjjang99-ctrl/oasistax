# v3.5.4 적용방법

1. 압축 안의 모든 내용을 기존 `정책자금자동화` 폴더에 풉니다.
2. `RUN_OASIS_V354_UPDATE.cmd`를 더블클릭합니다.
3. `[SUCCESS]`가 나오면 실행합니다.

```powershell
streamlit run app.py
```

중복된 예담건설 PDF를 분석하면 전체 재무분석 전에 중복 안내가 표시되고 저장되지 않아야 합니다.

정상 확인 후:

```powershell
git add .
git commit -m "v3.5.4 크레탑 업로드 안정화"
git push
```
