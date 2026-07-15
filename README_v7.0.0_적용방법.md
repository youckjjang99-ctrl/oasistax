# OASIS v7.0.0 적용방법

1. ZIP을 기존 정책자금자동화 폴더에 압축 해제합니다.
2. `RUN_V7.0.0_UPDATE.cmd`를 실행합니다.
3. 아래 문구가 표시되면 성공입니다.

```text
UPDATE_OK
VERSION=v7.0.0
SQL_REQUIRED=NO
```

4. 이번 업데이트는 Supabase SQL 실행이 필요하지 않습니다.
5. GitHub에 반영합니다.
6. Streamlit 재배포 시 OpenCV 설치 때문에 첫 배포가 평소보다 오래 걸릴 수 있습니다.
7. 기업컨설팅 → 정관검토에서 뒤집힌 PDF를 다시 업로드해 분석합니다.
8. 분석 결과 아래에서 페이지별 적용 회전각과 OCR 품질을 확인할 수 있습니다.

## GitHub 업로드 명령어

```powershell
git status
git add .
git commit -m "v7.0.0 AI문서전처리 자동회전 기울기보정 OCR품질검사"
git push origin main
```
