# v9.14.17-stock-capital-history

기준: GitHub main `4f741b7de5a8b3a517e8a857c2fc111cc2cf15d4`, 작업일 2026-10-24 (Asia/Seoul).

## 범위

주가평가에 필요한 등기 인식·자본변동·평가기준일별 주식수 검토만 보완합니다. 저가증자 증여이익·증여세 계산은 구현하지 않습니다. 고객정보·기존 평가·원본을 삭제하거나 운영 데이터를 일괄 재분석하지 않습니다.

## 원인과 변경

- 말소사항 포함 등기에서 자본금·발행주식수의 첫 매칭을 현재 값으로 선택하던 오류를 수정합니다. 변경일·등기일·행별 주식수와 자본금 후보를 보존하고, 최신 값을 확정할 수 없는 경우 미확인으로 표시합니다. 발행가능 총수와 실제 발행수를 분리합니다.
- 등기의 현재 숫자와 별도로 `capital_history`, `capital_events`, 검토 플래그·경고와 파서 버전을 추가합니다. 실제 발행가액이나 납입금액은 액면가에서 추정하지 않습니다.
- 평가기준일의 현재 주식수, 각 사업연도 종료일 주식수, 법정 환산 주식수를 구분합니다. 확인한 당해연도 유상증자는 과거 연도 주식수를 일괄 변경하지 않습니다.
- 과거 단일 확정 증감자의 주식수 비율은 정확한 분수로 검토합니다. 소수 주식수·복수 사건·무상증감자 등 자동 지원 범위 밖의 경우에는 확정값과 근거를 요구합니다. 과거 유상증감자의 순손익 가감·월할·연환산은 자동 추정하지 않으며 전문가가 검토한 최종값을 별도로 입력합니다.
- 과거 주식수 공란을 현재 주식수로 채우던 동작을 제거했습니다. 각 사업연도의 주식수를 확인해야 계산됩니다.
- 자본변동 검토는 기업·사용자·등기 원천·평가기준일·순손익액·주식수에 연결합니다. 입력이나 원천 변경 시 재검토를 요구하며, 새 계산에 검토 근거를 함께 보존합니다.
- 명시적으로 선택한 등록 등기 원본을 재분석할 수 있습니다. 로컬 소유자 경계, SHA, 원본 ID, 기업 식별정보를 검증하고, 비공개 원본은 동일 Supabase HTTPS 출처·리다이렉트 차단·크기 상한을 확인합니다. 원본이 없으면 재등록을 안내합니다.
- 재분석은 등기의 새 분석 버전을 보관하는 동작입니다. 기존 재무정보·평가 입력값·계산 결과는 그대로 두고 사용자가 명시적으로 반영합니다. 클라우드 보관이 확인되지 않으면 완료로 안내하지 않습니다.
- 새 분석의 미확인 자본·주식·액면가가 로컬/클라우드 병합 때 과거 오인식값으로 되살아나지 않도록 동일 원본의 자본 필드를 일관되게 처리합니다. 이전 스냅샷과 양쪽 `_versions`는 합집합으로 보존합니다.

## 데이터·권한 영향

- Supabase 테이블·컬럼·인덱스·RLS 변경 및 마이그레이션 없음. 기존 `oasis_registry.registry_data`와 주가평가 JSON 안에 추가 메타데이터를 보관하는 호환 변경입니다.
- 기존 고객 ID, company_uid, 원본 SHA/자료 ID, 메모와 과거 계산을 유지합니다.
- 운영 DB/Storage 접근·데이터 변경·자동 배치 재계산·GitHub 공개 업로드·Railway 배포는 이 개발 작업에서 수행하지 않습니다.
- 회원·권한·DB발굴·전화번호 수집·Tilko/경정청구·고객 원천 테이블은 수정하지 않습니다.

## 사용법

1. 이전 방식으로 분석한 등기는 주가평가의 **등기 원본 다시 분석**을 열고 현재 기업의 원본을 선택합니다. 원본이 없으면 기업정보등록에서 같은 기업에 PDF를 다시 등록합니다.
2. 새 분석 결과를 대조한 뒤 **등록 등기값을 현재 평가에 반영**을 누릅니다. 기존 평가 버전은 보존됩니다.
3. **증자·감자 및 평가기준일별 주식수 검토**에서 변동 종류·실제 효력일과 3개 사업연도 기간을 확인합니다. 기본 1월~12월 표시는 원문 결산기간의 자동 확정이 아닙니다.
4. 자동 지원 범위이면 **평가기준일별 주식수 확인·반영**으로 현재/과거 주식수를 구분해 적용합니다. 지원 범위 밖은 전문가 확정 경로에서 최종 주식수·순손익액·검토 근거를 입력합니다.
5. 평가기준일의 자산·부채에 증자 영향이 이미 반영됐는지 확인합니다. 이 기능은 증자대금을 자산에 임의 가산하지 않습니다.
6. 계산·저장하면 새 평가 버전에 검토 이력이 포함됩니다. 등기나 금액을 바꾸면 재검토합니다.

## 현행 법령 검토

- [시행령 제55조: 평가기준일의 순자산가액](https://www.law.go.kr/법령/상속세및증여세법시행령/제55조)
- [시행령 제56조: 각 연도 말 주식수·증감자 순손익 조정](https://www.law.go.kr/법령/상속세및증여세법시행령/제56조)
- [시행규칙 제17조의3: 연환산·주식수 환산](https://www.law.go.kr/법령/상속세및증여세법시행규칙/제17조의3)

법령 검토일 2026-10-24. 평가기준일에 해당하는 법령과 예외·종류주식·무상변동·합병·분할은 별도 전문가 확인이 필요합니다. 상담용 보충적 평가 기능이며 신고용 확정 평가를 대신하지 않습니다.

## 테스트·롤백

`RUN_v9.14.17-stock-capital-history.bat` 실행 결과: 기존 회귀 667개 + 신규 합성·모킹 검증 106개, 총 **773개 통과**(실패 0, 기존 PDF SWIG 경고 5개). Streamlit 1.59.1 AppTest의 위젯 렌더·메뉴 왕복·이전 평가 불러오기·저장·원본 보존을 포함합니다. 실제 고객 원본은 테스트·저장소·패치 ZIP에 포함하지 않습니다. 별도 로컬 원문 교차검증은 읽기 전용으로 수행했으며 운영 저장값은 변경하지 않았습니다.

롤백은 직전 코드로 전환하되 JSON의 원본·분석버전·검토이력·평가결과를 삭제하지 않습니다. 구 버전에서는 새 메타데이터를 무시할 수 있으므로 새 코드에서 확인한 결과를 임의로 재계산하지 않습니다.

## 변경 파일

- VERSION.txt
- registry_worker.py
- payload/registry_worker.py
- registry_reanalysis.py
- stock_capital.py
- stock_capital_ui.py
- stock_valuation.py
- cloud_sync.py
- tests/test_registry_capital_history.py
- tests/test_registry_reanalysis.py
- tests/test_stock_capital.py
- tests/test_stock_capital_integration.py
- tests/test_stock_company_state.py
- tests/test_stock_valuation_goodwill_ui.py
- RUN_v9.14.17-stock-capital-history.bat
- docs/CHANGELOG-stock-capital-v9.14.17.md

## 승인 후 업로드 예시

현재 작업은 로컬 수정과 검증까지만 완료했습니다. 아래 명령은 공개 업로드 승인 후 작업 브랜치에서 실행하며, main 병합과 Railway 배포는 별도로 확인합니다. 관련 없는 변경과 고객자료가 섞이지 않도록 `git add .`는 사용하지 않습니다.

```bash
git status
git add VERSION.txt registry_worker.py payload/registry_worker.py registry_reanalysis.py stock_capital.py stock_capital_ui.py stock_valuation.py cloud_sync.py
git add tests/test_registry_capital_history.py tests/test_registry_reanalysis.py tests/test_stock_capital.py tests/test_stock_capital_integration.py tests/test_stock_company_state.py tests/test_stock_valuation_goodwill_ui.py
git add RUN_v9.14.17-stock-capital-history.bat docs/CHANGELOG-stock-capital-v9.14.17.md
git commit -m "fix: preserve registry capital history and validate valuation share counts"
git push origin codex/stock-capital-history-20261024
```
