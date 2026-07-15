# OASIS v7.1.0 적용방법

## 적용 순서

1. ZIP을 기존 정책자금자동화 폴더에 압축 해제합니다.
2. `RUN_V7.1.0_UPDATE.cmd`를 실행합니다.
3. 아래 문구가 표시되면 성공입니다.

```text
UPDATE_OK
VERSION=v7.1.0
SQL_REQUIRED=supabase_v710_upgrade.sql
```

4. Supabase SQL Editor에서 `supabase_v710_upgrade.sql`을 한 번 실행합니다.
5. GitHub에 반영합니다.
6. Streamlit 재배포 후 기업컨설팅 메뉴를 확인합니다.

고객 삭제는 영구삭제가 아니라 휴지통 이동입니다.
원본 고객DB와 CRM·상담일지·정관·정책자금·기업히스토리는 보존됩니다.

## GitHub 업로드 명령어

```powershell
git status
git add .
git commit -m "v7.1.0 기업고객 검색 필터 휴지통 삭제복원"
git push origin main
```
