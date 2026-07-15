# OASIS v7.4.2 긴급복구 적용방법

현재 오류:

```text
StreamlitInvalidVerticalAlignmentError
vertical_alignment="end"
```

`end`는 Streamlit에서 허용되지 않으므로 `bottom`으로 변경하는 패치입니다.

## 적용 순서

1. ZIP을 현재 정책자금자동화 폴더에 압축 해제합니다.
2. `RUN_V7.4.2_UPDATE.cmd`를 실행합니다.
3. 아래 문구가 표시되면 성공입니다.

```text
UPDATE_OK
VERSION=v7.4.2
SQL_REQUIRED=NO
```

4. Supabase SQL은 실행하지 않습니다.
5. GitHub에 반영합니다.
6. Streamlit 재배포 후 기업컨설팅 메뉴를 다시 엽니다.

## GitHub 업로드 명령어

```powershell
git status
git add .
git commit -m "v7.4.2 기업컨설팅 세로정렬 오류 긴급복구"
git push origin main
```
