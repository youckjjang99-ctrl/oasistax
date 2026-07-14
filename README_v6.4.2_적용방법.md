# OASIS v6.4.2 적용방법

1. ZIP을 기존 정책자금자동화 폴더에 압축 해제합니다.
2. `RUN_V6.4.2_UPDATE.cmd`를 더블클릭합니다.
3. `UPDATE_OK`, `VERSION=v6.4.2`가 표시되면 성공입니다.
4. Supabase SQL 실행은 필요하지 않습니다.
5. 기업컨설팅 → CRM → 녹음파일 상담일지 보기에서 `기존 상담일지 전체 재연동`을 한 번 누릅니다.
6. 정책자금, 기업히스토리, AI 종합진단을 다시 확인합니다.

```powershell
git status
git add .
git commit -m "v6.4.2 상담일지 정책자금 기업히스토리 실제 연결 수정"
git push origin main
```
