# OASIS UPDATE

1. ZIP을 정책자금자동화 폴더에 압축 해제합니다.
2. `RUN_UPDATE.cmd`를 실행합니다.
3. `UPDATE_OK`, `VERSION=v8.2.0`, `PRECHECK=PASS`를 확인합니다.

GitHub 반영:

```powershell
git status
git add .
git commit -m "v8.2.0 장시간녹음 자동분할 구간캐시 누적업데이트"
git push origin main
```
