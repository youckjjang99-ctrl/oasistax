# v3.5.1 긴급 수정 적용방법

1. 압축 안의 모든 내용을 기존 `정책자금자동화` 폴더에 풉니다.
2. 파일 덮어쓰기 질문이 나오면 `모두 바꾸기`를 선택합니다.
3. `RUN_V351_HOTFIX.cmd`를 더블클릭합니다.
4. `[SUCCESS]`가 나오면:
   `streamlit run app.py`
5. 정상 실행 후 GitHub에 올립니다.

```powershell
git add .
git commit -m "v3.5.1 기업마당 모듈 누락 수정"
git push
```
