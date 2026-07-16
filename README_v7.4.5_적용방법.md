# OASIS v7.4.5 적용방법

1. ZIP을 기존 정책자금자동화 폴더에 압축 해제
2. `RUN_V7.4.5_UPDATE.cmd` 실행
3. 아래 문구 확인

```text
UPDATE_OK
VERSION=v7.4.5
SQL_REQUIRED=NO
```

4. GitHub 반영

```powershell
git status
git add .
git commit -m "v7.4.5 시스템관리 f-string 문법오류 긴급복구"
git push origin main
```
