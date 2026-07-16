# OASIS v7.4.3 적용방법

1. ZIP을 기존 프로젝트 폴더에 압축 해제합니다.
2. RUN_V7.4.3_UPDATE.cmd를 실행합니다.
3. UPDATE_OK / PRECHECK=PASS / AUTO_ROLLBACK=ENABLED를 확인합니다.
4. GitHub 업로드 전 RUN_PRECHECK.cmd를 실행해 STATUS=PASS / DEPLOY_READY를 확인합니다.
5. Supabase SQL은 실행하지 않습니다.

```powershell
git status
git add .
git commit -m "v7.4.3 시스템 사전점검 자동롤백 배포안정화"
git push origin main
```
