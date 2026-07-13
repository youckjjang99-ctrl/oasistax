# v3.6.2 적용방법

1. 압축 안의 모든 내용을 기존 정책자금자동화 폴더에 풉니다.
2. RUN_OASIS_V362_UPDATE.cmd를 더블클릭합니다.
3. SUCCESS가 나오면 실행합니다.

```powershell
streamlit run app.py
```

예담건설 PDF를 다시 분석하면 새 행이 추가되지 않아야 합니다.
기존에 생긴 2건은 자동 삭제하지 않습니다.

```powershell
git add .
git commit -m "v3.6.2 사업자번호 중복방지 복구"
git push
```
