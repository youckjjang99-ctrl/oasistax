# OASIS v6.8.0 적용방법

1. ZIP을 기존 정책자금자동화 폴더에 압축 해제합니다.
2. `RUN_V6.8.0_UPDATE.cmd`를 실행합니다.
3. 다음 문구가 나오면 성공입니다.

```text
UPDATE_OK
VERSION=v6.8.0
SQL_REQUIRED=NO
```

4. GitHub에 반영합니다.
5. Streamlit 재배포 시 한글 OCR 엔진 설치로 첫 배포가 평소보다 오래 걸릴 수 있습니다.
6. 기업컨설팅 → 정관검토에서 스캔 PDF를 다시 업로드하고 분석합니다.

일반 텍스트 PDF는 OCR을 사용하지 않아 기존 속도를 유지합니다.
문자가 없는 스캔 PDF만 OCR이 자동 실행됩니다.

## GitHub 업로드 명령어

```powershell
git status
git add .
git commit -m "v6.8.0 스캔정관 한글OCR 및 정관상세검토 기능"
git push origin main
```
