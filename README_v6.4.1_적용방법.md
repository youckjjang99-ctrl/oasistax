# OASIS v6.4.1 적용방법

1. ZIP을 기존 정책자금자동화 폴더에 압축 해제합니다.
2. `RUN_V6.4.1_UPDATE.cmd`를 더블클릭합니다.
3. `UPDATE_OK`, `VERSION=v6.4.1`이 나오면 성공입니다.
4. 이번 버전은 Supabase SQL 실행이 필요하지 않습니다.
5. 앱에서 기업컨설팅 → CRM → 녹음파일 상담일지 보기로 이동합니다.
6. `기존 상담일지 전체 재연동` 버튼을 한 번 누릅니다.
7. 기업컨설팅 → 정책자금, 정책자금매칭, 기업히스토리, AI 종합진단을 확인합니다.

GitHub 업로드 명령어:

```powershell
git status
git add .
git commit -m "v6.4.1 기존 상담일지 정책자금 재연동 및 히스토리 복구"
git push origin main
```
