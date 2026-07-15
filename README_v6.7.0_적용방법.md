# OASIS v6.7.0 적용방법

1. ZIP을 기존 정책자금자동화 폴더에 압축 해제합니다.
2. `RUN_V6.7.0_UPDATE.cmd`를 실행합니다.
3. 아래 문구가 표시되면 성공입니다.

```text
UPDATE_OK
VERSION=v6.7.0
SQL_REQUIRED=NO
```

4. GitHub에 반영하면 Streamlit이 `python-docx`, `olefile`을 자동 설치합니다.
5. 기업컨설팅 → 정관검토에서 PDF/HWP/DOCX/TXT 정관을 업로드합니다.
6. 분석 후 기업히스토리와 AI 진단에서 결과를 확인합니다.

스캔 이미지만 포함된 PDF는 텍스트 추출이 되지 않으므로 텍스트 PDF로 변환 후 사용합니다.

## GitHub 업로드 명령어

```powershell
git status
git add .
git commit -m "v6.7.0 정관검토 메뉴 및 AI 기업진단 연동"
git push origin main
```
