# v3.8.0 적용방법

## 수정 파일
- app.py
- cretop_worker.py
- VERSION.txt

## 신규 파일
- stock_valuation.py

## 적용
1. 압축 안의 모든 파일을 기존 `정책자금자동화` 폴더에 풉니다.
2. `RUN_V380_UPDATE.bat`를 더블클릭합니다.
3. `[SUCCESS]`가 나오면 실행합니다.

```powershell
streamlit run app.py
```

## 메뉴
`홈 → 크레탑 자동등록 → 고객관리 → 정책자금 매칭 → 주가평가`

## 주가평가 사용
1. 기존 고객을 선택하거나 직접 입력합니다.
2. 크레탑 PDF가 있으면 업로드 후 분석합니다.
3. 발행주식수와 세법상 조정 후 순손익액을 확인·수정합니다.
4. 순자산 조정사항을 입력합니다.
5. 주가평가 계산 후 별도 저장합니다.

기존 고객DB는 읽기만 하며 수정하지 않습니다.

## GitHub 반영
```powershell
git add .
git commit -m "v3.8.0 주가평가 모듈 추가"
git push
```
