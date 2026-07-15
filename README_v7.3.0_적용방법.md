# OASIS v7.3.0 적용방법

1. ZIP을 기존 정책자금자동화 폴더에 압축 해제합니다.
2. `RUN_V7.3.0_UPDATE.cmd`를 실행합니다.
3. 아래 문구가 나오면 성공입니다.

```text
UPDATE_OK
VERSION=v7.3.0
SQL_REQUIRED=NO
GITHUB_ACTIONS=DAILY_03_KST
```

4. Supabase SQL은 실행하지 않습니다.
5. GitHub에 반영합니다.
6. GitHub 저장소의 Actions에서 `Update Internal Policy DB`를 한 번 수동 실행합니다.
7. 이후 매일 한국시간 새벽 3시에 자동 실행됩니다.

기업마당용 `BIZINFO_API_KEY` GitHub Actions Secret은 기존 값을 사용합니다.

K-Startup·중진공도 새벽 자동 수집하려면 GitHub 저장소의
Settings → Secrets and variables → Actions에 다음 값을 등록합니다.

- `KSTARTUP_API_URL`
- `KSTARTUP_API_KEY`
- `KSTARTUP_API_PARAMS_JSON`
- `KOSMES_API_URL`
- `KOSMES_API_KEY`
- `KOSMES_API_PARAMS_JSON`

등록하지 않은 소스는 건너뛰며, 정책자금 매칭 자체에는 영향을 주지 않습니다.

## GitHub 업로드 명령어

```powershell
git status
git add .
git commit -m "v7.3.0 새벽3시 정책DB동기화 및 실시간API호출제거"
git push origin main
```
