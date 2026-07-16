# OASIS UPDATE

## 적용방법

1. ZIP을 현재 정책자금자동화 폴더에 압축 해제합니다.
2. 기존 `RUN_UPDATE.cmd`, `update.py`가 있으면 새 파일로 덮어씁니다.
3. `RUN_UPDATE.cmd`를 실행합니다.
4. 아래 결과를 확인합니다.

```text
UPDATE_OK
VERSION=v8.2.1
BIZINFO_TITLE_ALIASES=FIXED
REPOSITORY_FIELDS_INJECTED=YES
CONVERSION_DIAGNOSTICS=ENABLED
PRECHECK=PASS
SQL_REQUIRED=NO
```

## 적용 후 확인

기업컨설팅 → 정책자금 → 다중소스 AI 매칭 실행 후:

- 공고소스 상태 count가 34건이 아니라 전체 내부DB에 가까운 수치인지 확인
- `공고형 지원사업` 탭에 결과가 표시되는지 확인
- 상태 메시지에 `변환 OOO건`, 필요 시 `제외 OOO건`이 표시되는지 확인

## GitHub 반영

```powershell
git status
git add .
git commit -m "v8.2.1 공고형 정책자금 로딩 누락 복구"
git push origin main
```

Supabase SQL은 실행하지 않습니다.
