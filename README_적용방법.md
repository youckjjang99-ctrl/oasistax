# OASIS v3.4.1 적용방법

## 가장 쉬운 방법

1. 압축파일 안의 모든 내용을 기존 `정책자금자동화` 폴더에 풉니다.
2. `RUN_OASIS_UPDATE.cmd`를 더블클릭합니다.
3. 화면에 `[SUCCESS]`가 표시되면 업데이트 완료입니다.
4. 기존처럼 `streamlit run app.py`로 실행합니다.

`OASIS_v3.4.1_업데이트.cmd`를 실행해도 동일합니다.

## 이번 버전에서 해결한 문제

Windows에서 `python` 명령을 찾지 못해 업데이트가 실행되지 않던 문제를 보완했습니다.
업데이트 파일이 `py -3`, `python`, 사용자 Python 설치 폴더 순서로 자동 탐색합니다.

## 그래도 Python을 찾지 못할 때

VS Code에서 프로젝트 폴더를 연 뒤 터미널에 아래 둘 중 하나를 입력합니다.

```powershell
py -3 update_v341.py
```

또는

```powershell
python update_v341.py
```

## 자동 처리 항목

- 기존 프로젝트 자동 검사
- `_oasis_backups`에 자동 백업
- v3.4.1 파일 적용
- VERSION.txt 자동 변경
- 업데이트 기록 저장
- 관리자 `시스템 관리` 메뉴 추가

## 복원

`OASIS_백업복원.cmd`를 더블클릭한 뒤 복원할 백업 번호를 선택합니다.
