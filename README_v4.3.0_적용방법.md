# v4.3.0 적용방법

## 수정 파일
- app.py
- VERSION.txt

## 신규 파일
- consulting_report.py

## 적용

1. 압축 안의 모든 파일을 기존 `정책자금자동화` 폴더에 풉니다.
2. `RUN_V430_UPDATE.bat`를 더블클릭합니다.
3. `[SUCCESS]`가 나오면 실행합니다.

```powershell
streamlit run app.py
```

## 사용방법

1. 왼쪽 메뉴에서 `AI 컨설팅 리포트`를 선택합니다.
2. 기존 등록 고객을 선택합니다.
3. 기업 기본정보·재무진단·강점·확인사항·추천전략을 확인합니다.
4. 대표 상담 질문을 활용합니다.
5. `AI 컨설팅 리포트 엑셀 다운로드`를 누릅니다.

기존 고객DB와 고객리스트는 수정하지 않습니다.

## GitHub 반영

```powershell
git add .
git commit -m "v4.3.0 AI 컨설팅 리포트"
git push
```
