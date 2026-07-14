# OASIS v6.5.0 적용방법

1. ZIP을 기존 정책자금자동화 폴더에 압축 해제합니다.
2. `RUN_V6.5.0_UPDATE.cmd`를 더블클릭합니다.
3. 아래 문구가 나오면 성공입니다.

```text
UPDATE_OK
VERSION=v6.5.0
```

4. 이번 업데이트는 Supabase SQL 실행이 필요하지 않습니다.
5. GitHub에 반영한 뒤 Streamlit 재배포를 기다립니다.
6. 기업컨설팅에서 해당 기업을 선택하고 정책자금 탭을 열면 상담 기반 키워드가 자동 표시됩니다.
7. 기업히스토리와 AI진단도 같은 상담자료를 자동 사용합니다.

## GitHub 업로드 명령어

```powershell
git status
git add .
git commit -m "v6.5.0 상담일지 정책자금 기업히스토리 AI컨설팅엔진 통합"
git push origin main
```
