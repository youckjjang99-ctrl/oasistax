# v3.7.3 적용방법

1. 압축 안의 모든 파일을 기존 `정책자금자동화` 폴더에 풉니다.
2. `RUN_V373_FIX.bat`를 더블클릭합니다.
3. `[SUCCESS]`가 나오면 실행합니다.

```powershell
streamlit run app.py
```

정상 확인 후 GitHub 반영:

```powershell
git add .
git commit -m "v3.7.3 CRM NameError 수정"
git push
```
