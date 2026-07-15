# OASIS v6.6.2 적용방법

1. ZIP을 기존 정책자금자동화 폴더에 압축 해제합니다.
2. `RUN_V6.6.2_UPDATE.cmd`를 더블클릭합니다.
3. 아래 문구가 표시되면 성공입니다.

```text
UPDATE_OK
VERSION=v6.6.2
SQL_REQUIRED=NO
```

4. 이번 버전은 `lxml` 설치와 Supabase SQL 실행이 필요하지 않습니다.
5. GitHub 반영 후 Streamlit 재배포를 기다립니다.
6. 다중소스 AI 매칭을 실행합니다.
7. 결과 화면에서 다음 탭을 확인합니다.

- 전체 추천
- 정책자금·보증
- 고용지원금
- 공고형 지원사업

## GitHub 업로드 명령어

```powershell
git status
git add .
git commit -m "v6.6.2 lxml 오류 수정 및 정책자금 고용지원금 결과분류"
git push origin main
```
