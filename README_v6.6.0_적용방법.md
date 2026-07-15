# OASIS v6.6.0 적용방법

1. ZIP을 기존 프로젝트 폴더에 압축 해제합니다.
2. `RUN_V6.6.0_UPDATE.cmd`를 실행합니다.
3. `UPDATE_OK`, `VERSION=v6.6.0`을 확인합니다.
4. Supabase SQL Editor에서 `supabase_v660_upgrade.sql`을 한 번 실행합니다.
5. Streamlit Secrets에 기업마당 API를 설정합니다.

```toml
BIZINFO_API_URL = "기업마당 API 호출 URL"
BIZINFO_API_KEY = "인증키"
BIZINFO_API_PARAMS_JSON = '{"pageNo":1,"numOfRows":1000,"type":"json"}'
```

기업마당 API 미설정 상태에서도 내부 정책DB 34건은 즉시 사용됩니다.

## GitHub 업로드 명령어

```powershell
git status
git add .
git commit -m "v6.6.0 내부 정책DB 자동최신화 및 기업마당 다중소스 통합"
git push origin main
```
