# v3.7.0 적용방법

1. 압축 안의 모든 내용을 기존 정책자금자동화 폴더에 풉니다.
2. `RUN_OASIS_V370_UPDATE.cmd`를 더블클릭합니다.
3. `[SUCCESS]`가 나오면 실행합니다.

```powershell
streamlit run app.py
```

## 예담건설 확인

PDF를 신규 분석한 결과에서 아래 값이 표시되는지 확인합니다.

- 사업장 소재지
- 설립일
- 매출액
- 영업이익
- 당기순이익

기존에 등록된 예담건설 행은 자동으로 다시 분석하지 않으므로,
고객관리의 `고객 기본정보 직접 수정`에서 값을 수정할 수 있습니다.

## GitHub 반영

```powershell
git add .
git commit -m "v3.7.0 CRM 고도화 및 크레탑 추출 개선"
git push
```
