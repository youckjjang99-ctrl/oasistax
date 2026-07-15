# OASIS v6.9.0 적용방법

## 적용 순서

1. ZIP을 기존 정책자금자동화 폴더에 압축 해제합니다.
2. `RUN_V6.9.0_UPDATE.cmd`를 실행합니다.
3. 아래 문구가 표시되면 성공입니다.

```text
UPDATE_OK
VERSION=v6.9.0
SQL_REQUIRED=supabase_v690_upgrade.sql
```

4. Supabase SQL Editor에서 `supabase_v690_upgrade.sql`을 한 번 실행합니다.
5. GitHub에 반영합니다.
6. Streamlit 재배포를 기다립니다.
7. 기업컨설팅 → 정관검토에서 정관을 다시 분석합니다.
8. 정관 자동개정·편집 영역에서 표준유형과 적용 조항을 선택합니다.
9. 전체 개정안을 최종 수정한 뒤 버전을 저장하고 PDF를 다운로드합니다.

기존 v6.8.0에서 이미 분석한 정관은 원문 텍스트가 저장되지 않았을 수 있습니다.
이 경우 동일한 정관을 한 번 다시 분석하면 자동개정 편집기가 활성화됩니다.

## GitHub 업로드 명령어

```powershell
git status
git add .
git commit -m "v6.9.0 정관 자동개정 편집기 및 PDF 버전관리"
git push origin main
```
