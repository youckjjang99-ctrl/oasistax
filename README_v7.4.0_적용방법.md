# OASIS v7.4.0 적용방법

1. ZIP을 기존 정책자금자동화 폴더에 압축 해제합니다.
2. `RUN_V7.4.0_UPDATE.cmd`를 실행합니다.
3. 아래 문구가 표시되면 성공입니다.

```text
UPDATE_OK
VERSION=v7.4.0
SQL_REQUIRED=supabase_v740_upgrade.sql
```

4. Supabase SQL Editor에서 `supabase_v740_upgrade.sql`을 한 번 실행합니다.
5. GitHub에 반영합니다.
6. Streamlit 재배포 후 다시 로그인합니다.
7. 기업컨설팅 → 직원현황에서 4대보험 가입자명부를 업로드합니다.
8. 이후 정책자금 → 다중소스 AI 매칭을 실행하면 직원현황이 자동 반영됩니다.

## GitHub 업로드 명령어

```powershell
git status
git add .
git commit -m "v7.4.0 직원현황 4대보험명부 고용지원금연동 비밀번호변경"
git push origin main
```
