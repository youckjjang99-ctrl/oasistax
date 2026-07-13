# v4.0.1 적용방법

1. 압축 안의 모든 파일을 기존 `정책자금자동화` 폴더에 풉니다.
2. `RUN_V401_FIX.bat`를 더블클릭합니다.
3. `[SUCCESS]`가 나오면 실행합니다.

```powershell
streamlit run app.py
```

관리자 로그인 후 왼쪽 메뉴에 `클라우드 DB 관리`가 표시되어야 합니다.

정상 확인 후 GitHub 반영:

```powershell
git add .
git commit -m "v4.0.1 클라우드 DB 메뉴 누락 수정"
git push
```
