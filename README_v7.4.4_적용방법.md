# OASIS v7.4.4 적용방법

1. ZIP을 기존 정책자금자동화 폴더에 압축 해제합니다.
2. `RUN_V7.4.4_UPDATE.cmd`를 실행합니다.
3. 다음 문구가 나오면 성공입니다.

```text
UPDATE_OK
VERSION=v7.4.4
PRECHECK=PASS
AUTO_ROLLBACK=ENABLED
SQL_REQUIRED=NO
```

4. `RUN_PRECHECK.cmd`를 실행해 `STATUS=PASS`, `DEPLOY_READY`를 확인합니다.
5. GitHub에 반영합니다.

```powershell
git status
git add .
git commit -m "v7.4.4 사전점검 오탐수정 자동롤백 안정화"
git push origin main
```

Supabase SQL은 실행하지 않습니다.
