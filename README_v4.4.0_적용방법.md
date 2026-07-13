# v4.4.0 적용방법

이번 버전은 요청하신 v4.3.2 주소 보완과 v4.4 CRM·기업 히스토리를 함께 반영합니다.

## 수정 파일
- app.py
- VERSION.txt

## 신규 파일
- address_tools.py
- crm_enhancements.py
- customer_history.py
- supabase_v440_upgrade.sql

## 적용

1. 압축 안의 모든 파일을 기존 정책자금자동화 폴더에 풉니다.
2. `RUN_V440_UPDATE.bat`를 더블클릭합니다.
3. Supabase SQL Editor에서 `supabase_v440_upgrade.sql` 내용을 실행합니다.
4. 프로그램을 실행합니다.

```powershell
streamlit run app.py
```

## 주소 보완 확인

`내 누적 고객DB` 메뉴에 들어가면 시도·시군구가 공란인 고객만 자동 보완됩니다.

예:
- 경기 용인시 기흥구 → 시도 경기 / 시군구 용인시
- 서울 광진구 → 시도 서울 / 시군구 광진구

## CRM 확인

고객관리에서 고객을 선택하면:
- 상담 진행단계
- 중요도
- 담당자
- 다음 예정일
- 상담 메모

를 관리할 수 있습니다.

## 기업 히스토리 확인

같은 업체의 크레탑 PDF를 다시 분석하면:
- 매출
- 영업이익
- 당기순이익
- 자산·부채·자본
- 종업원수
- 주소

변경 이력이 고객관리 화면에 표시됩니다.

## GitHub 반영

```powershell
git add .
git commit -m "v4.4.0 주소보완 CRM고도화 기업히스토리"
git push
```
