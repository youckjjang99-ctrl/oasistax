# 정책자금자동화 v6.3.3 적용방법

## 적용
1. ZIP 안의 파일을 기존 정책자금자동화 폴더에 압축 해제합니다.
2. `RUN_V6.3.3_UPDATE.cmd`를 더블클릭합니다.
3. `UPDATE COMPLETE - v6.3.3`이 표시되면 코드 적용이 완료된 것입니다.
4. Supabase SQL Editor에서 `supabase_v633_upgrade.sql`을 한 번 실행합니다.
5. 아래 Git 명령어를 프로젝트 폴더의 PowerShell에서 실행합니다.

```powershell
git status
git add .
git commit -m "v6.3.3 기존 녹음파일 복구 및 사업자번호 조회 안정화"
git push origin main
```

## 확인 방법
- 앱 버전이 `v6.3.3`인지 확인합니다.
- 대한산업 기업상담 화면에서 기존 클라우드 녹음 히스토리가 다시 보이는지 확인합니다.
- 같은 원본 파일을 다시 올렸을 때 기존 파일을 재사용한다는 안내가 표시되는지 확인합니다.

## 데이터 보호
기존 녹음파일은 삭제하거나 이동하지 않습니다. 기존 고객DB, CRM, 상담일지, 정책자금 키워드와 Supabase Storage도 유지됩니다.
