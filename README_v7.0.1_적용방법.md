# OASIS v7.0.1 적용방법

1. ZIP을 기존 정책자금자동화 폴더에 압축 해제합니다.
2. `RUN_V7.0.1_UPDATE.cmd`를 실행합니다.
3. 아래 문구가 표시되면 성공입니다.

```text
UPDATE_OK
VERSION=v7.0.1
SQL_REQUIRED=NO
```

4. Supabase SQL 실행은 필요하지 않습니다.
5. GitHub에 반영합니다.
6. Streamlit 재배포 후 기존 정관을 다시 업로드하고 분석합니다.
7. 페이지별 OCR 품질표에서 OCR언어·문단모드·영문비율·재검토 여부를 확인합니다.

기존에 저장된 깨진 OCR 결과는 자동으로 바뀌지 않습니다.
동일한 정관을 다시 분석해야 개선된 OCR 결과가 저장됩니다.

## GitHub 업로드 명령어

```powershell
git status
git add .
git commit -m "v7.0.1 한글우선 OCR 및 영문오인식 제거"
git push origin main
```
