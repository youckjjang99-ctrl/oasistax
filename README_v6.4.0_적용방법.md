# OASIS v6.4.0 적용방법

1. ZIP을 기존 정책자금자동화 폴더에 압축 해제합니다.
2. `RUN_V6.4.0_UPDATE.cmd`를 더블클릭합니다.
3. `UPDATE_OK`, `VERSION=v6.4.0`이 나오면 성공입니다.
4. 이번 업데이트는 Supabase SQL 실행이 필요하지 않습니다.
5. PowerShell에서 아래 명령어를 실행합니다.

```powershell
git status
git add .
git commit -m "v6.4.0 상담일지 정책자금 기업히스토리 AI종합진단 연동"
git push origin main
```

확인 경로:
- 기업컨설팅 → 정책자금: 자동 매칭키워드
- 정책자금매칭: 상담 기반 추천
- 기업컨설팅 → 기업히스토리: 상담일자
- 기업컨설팅 → AI 종합진단: 상담·등기·주가평가 통합 반영
