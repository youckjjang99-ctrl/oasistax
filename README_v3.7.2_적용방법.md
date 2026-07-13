# v3.7.2 적용방법

1. 압축 안의 모든 파일을 기존 `정책자금자동화` 폴더에 풉니다.
2. `RUN_V372_UPDATE.bat`를 더블클릭합니다.
3. `[SUCCESS]`가 나오면 실행합니다.

```powershell
streamlit run app.py
```

## 예담건설 갱신 방법

예담건설 PDF를 크레탑 자동등록에서 다시 분석합니다.

- 새 고객은 추가되지 않습니다.
- 기존 예담건설 행의 사업장 소재지, 설립일, 당기순이익 등이 갱신됩니다.
- 고객관리 화면에서 갱신된 정보를 확인합니다.

## GitHub 반영

```powershell
git add .
git commit -m "v3.7.2 기존고객 갱신 및 AI카드 개선"
git push
```
