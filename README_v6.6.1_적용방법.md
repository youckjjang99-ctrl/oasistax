# OASIS v6.6.1 적용방법

1. ZIP을 기존 정책자금자동화 폴더에 압축 해제합니다.
2. `RUN_V6.6.1_UPDATE.cmd`를 더블클릭합니다.
3. 아래 문구가 표시되면 성공입니다.

```text
UPDATE_OK
VERSION=v6.6.1
SQL_REQUIRED=NO
```

4. 이번 업데이트는 Supabase SQL 실행이 필요하지 않습니다.
5. GitHub에 반영한 뒤 Streamlit 재배포를 기다립니다.
6. `다중소스 AI 매칭 실행`을 다시 눌러 확인합니다.

## GitHub 업로드 명령어

```powershell
git status
git add .
git commit -m "v6.6.1 다중소스 출처형식 안정화 및 KeyError 수정"
git push origin main
```
