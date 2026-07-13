# v4.5.0 적용방법

## 수정 파일
- app.py
- VERSION.txt

## 신규 파일
- cloud_crm_restore.py
- enterprise_center.py

## 적용

1. 압축 안의 모든 파일을 기존 `정책자금자동화` 폴더에 풉니다.
2. `RUN_V450_UPDATE.bat`를 더블클릭합니다.
3. `[SUCCESS]`가 나오면 실행합니다.

```powershell
streamlit run app.py
```

## 주요 확인사항

### CRM 자동복원

Streamlit Cloud 재배포 후 로그인하면 Supabase에 저장된 CRM 내용이 자동 복원됩니다.

복원 대상:
- 고객 상태
- 다음 액션
- 다음 예정일
- 상담 메모
- 상담 진행단계
- 중요도
- 담당자
- 상담 타임라인

### 기업관리센터

왼쪽 메뉴의 `기업관리센터`에서 고객을 선택하면 다음 정보를 한 화면에서 확인합니다.

- 기업정보
- CRM
- 정책자금 매칭설정
- 주가평가·등기
- 기업 히스토리
- AI 빠른진단

기존 고객관리·정책자금·주가평가·AI 리포트 메뉴는 그대로 유지됩니다.

## GitHub 반영

```powershell
git add .
git commit -m "v4.5.0 기업관리센터 CRM자동복원"
git push
```
