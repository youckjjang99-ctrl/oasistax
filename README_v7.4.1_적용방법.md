# OASIS v7.4.1 긴급복구 적용방법

현재 오류:

```text
ImportError
enterprise_center.py
from enterprise_customer_management import ...
```

이는 GitHub 업로드가 실패한 것이 아니라, 누적 패치의 함수명이 맞지 않아
Streamlit 앱이 시작 단계에서 멈춘 오류입니다.

## 적용 순서

1. ZIP을 현재 정책자금자동화 폴더에 압축 해제합니다.
2. `RUN_V7.4.1_UPDATE.cmd`를 실행합니다.
3. 아래 문구가 표시되면 성공입니다.

```text
UPDATE_OK
VERSION=v7.4.1
SQL_REQUIRED=NO
```

4. Supabase SQL은 추가 실행하지 않습니다.
5. GitHub에 반영합니다.
6. Streamlit 재배포가 완료되면 앱을 새로고침합니다.

## GitHub 업로드 명령어

```powershell
git status
git add .
git commit -m "v7.4.1 기업고객 ImportError 긴급복구"
git push origin main
```
