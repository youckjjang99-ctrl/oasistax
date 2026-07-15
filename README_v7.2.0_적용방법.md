# OASIS v7.2.0 적용방법

1. ZIP을 기존 정책자금자동화 폴더에 압축 해제합니다.
2. `RUN_V7.2.0_UPDATE.cmd`를 실행합니다.
3. `UPDATE_OK`, `VERSION=v7.2.0`을 확인합니다.
4. Supabase SQL Editor에서 `supabase_v720_upgrade.sql`을 한 번 실행합니다.
5. GitHub에 반영하고 Streamlit 재배포 후 다시 로그인합니다.

## GitHub 업로드 명령어

```powershell
git status
git add .
git commit -m "v7.2.0 기업컨설팅 화면단순화 고객삭제 휴지통 중복로그인방지"
git push origin main
```
