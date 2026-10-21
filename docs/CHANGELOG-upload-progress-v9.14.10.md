# OASIS 업로드 진행 안내 및 등기자료 등록 개선

버전: `v9.14.10-upload-progress`

작업일: 2026-10-21 Asia/Seoul

기준 소스: GitHub main `497d126`

반영 상태: 로컬 검증 완료, GitHub 업로드 및 운영 배포 전

## 원인과 수정

등기자료는 파일 저장 후 분석기에만 스피너가 표시되고 이후 기업 연결과 저장 중에는 안내가 없었습니다. 완료 안내는 즉시 화면을 다시 그리면서 사라졌고, 첨부자료 연결 실패는 숨겨졌습니다. 텍스트를 읽지 못한 빈 결과도 성공으로 취급되어 기존 등기값을 공란으로 바꿀 수 있었습니다.

- 파일 확인 전에 진행카드를 표시하고, 분석과 저장까지 현재 단계를 안내합니다. 모르는 소요시간을 임의의 백분율로 표시하지 않습니다.
- 완료, 일부 확인 필요, 실패를 구분합니다. 화면 재실행이 필요한 흐름은 사용자와 기업별 결과 안내를 다음 화면에서도 표시합니다.
- 빈 등기 결과와 다른 회사 자료는 반영하지 않습니다. 사업자번호, 법인번호 또는 정규화한 법인명의 실제 일치가 필요합니다.
- 부분 추출 시 기존 자본금, 주식정보와 재무 입력을 유지합니다. 손상된 기존 등기 캐시는 덮어쓰지 않습니다.
- 클라우드 저장과 첨부자료 연결을 각각 확인합니다. 로컬 보존만 확인된 경우 클라우드 성공으로 표시하지 않습니다.
- Excel 저장 오류를 정상적인 중복 제외 0건과 구분합니다. 같은 사용자, 담당자, 파일 내용의 Excel은 화면 재실행 시 다시 추가하거나 동기화하지 않습니다.
- 종합소득세 PDF가 바뀌면 이전 파일의 분석 결과를 해제합니다. 등록 실패 건을 기존자료 갱신 성공 건수에 포함하지 않습니다.
- 모든 진행카드는 HTML 이스케이프, 상태 읽기용 접근성 속성, 모바일 줄바꿈 및 동작 줄이기 설정을 지원합니다.

## 적용된 업로드 화면

| 자료 | 표시 흐름 |
| --- | --- |
| 법인 등기사항증명서 | 파일 확인, 등기 분석, 기업 연결 및 저장, 결과 |
| 크레탑 PDF | 파일 확인, 문서 분석, 기본정보 검토, 별도 저장 |
| 종합소득세 신고서 | 파일 확인, 사업장 분석, 사업장 선택, 등록 결과 |
| 고객DB Excel | 파일 확인, 양식 검증, 고객 저장, 보관 확인 |
| 정관 | 파일 확인, 문자 인식, 조항 분석, 결과 저장 |
| 4대보험 가입자명부 | 파일별 읽기, 분석, 검수표, 최종 저장 |
| 상담 녹음 | 기존 6단계 및 중복 실행 차단 유지, 초안과 녹음 보관 결과, 별도 최종 저장 |
| 세무조정계산서 및 연구개발 인정서 | 파일 확인, 문서 분석, 기업 연결, 결과 |

## 변경 파일

```text
app.py
articles_review.py
consultation_journal.py
employee_status.py
enterprise_documents.py
enterprise_registration_ui.py
registry_runner.py
stock_valuation.py
ui.py
upload_progress.py
utils.py
tests/test_upload_progress.py
tests/test_registry_upload_progress.py
tests/test_upload_workflow_feedback.py
VERSION.txt
RUN_v9.14.10-upload-progress.bat
docs/CHANGELOG-upload-progress-v9.14.10.md
```

## 데이터와 기존 기능

Supabase 스키마, RLS, 인증, Tilko, 전화번호 수집 및 API 호출 방식은 변경하지 않습니다. 고객정보, 원본 파일, 기존 ID, Git 이력은 삭제하지 않습니다. 기존 정관 분석, 직원 검수, 상담 녹취 캐시와 최종 저장 절차도 유지합니다.

이번 변경은 기존 동기 처리를 백그라운드 작업으로 전환하지 않습니다. 처리 중 페이지를 닫으면 작업의 지속 실행을 보장하지 않으므로 진행카드에서 화면 유지 안내를 제공합니다. 등기 분석기의 90초 제한과 텍스트 PDF 처리 범위는 유지합니다. 스캔본을 읽지 못하면 성공 대신 원본 PDF 재업로드 안내를 표시합니다.

## 검증

- Streamlit 1.59.1 기반 업로드, 기업등록, 저장, 주가평가, 권한, 메뉴 회귀 검증.
- 최종 결과: 30개 모듈에서 **635개 테스트와 하위 검증 3개 통과**. 외부 라이브러리 사용 중단 예정 경고 5개이며 실패는 없습니다.
- 합성 파일과 모의 저장소만 사용하며 실제 고객자료, 유료 AI, 통화 및 운영 DB 호출은 사용하지 않습니다.
- 실제 Streamlit 화면에서 등기 등록 후 재실행, 종합소득세 오류 표시, 정관 및 일반자료 완료 안내, 직원 저장 실패 후 검수표 유지, 녹음 보관 경고 유지 등을 검증합니다.
- 로컬 브라우저 PC 화면 및 390px 모바일 화면에서 진행카드, 단계 표시, 가로 넘침과 줄바꿈을 확인했습니다.
- 최종 테스트 결과는 함께 제공하는 검증 실행 파일로 재확인할 수 있습니다. 운영 배포 후에는 실제 권한 계정으로 스모크 테스트가 추가로 필요합니다.

## 적용과 롤백

1. 현재 프로젝트의 변경 파일과 `VERSION.txt`를 별도로 백업합니다. 고객자료와 `.git` 폴더는 유지합니다.
2. 패치 ZIP의 파일만 기존 프로젝트 루트에 덮어씁니다. ZIP에는 상위 폴더와 고객자료가 없습니다.
3. 기존 프로젝트 Python 환경에서 `RUN_v9.14.10-upload-progress.bat`를 실행합니다. 설치, DB 변경, Git push, 운영 배포는 실행하지 않습니다.
4. 테스트와 변경 내용을 검토한 뒤 별도 승인으로 GitHub와 Railway에 반영합니다.

롤백은 백업한 변경 소스와 버전 파일을 복원해 재배포하는 방식입니다. 신규 공통 모듈과 테스트는 남아 있어도 이전 소스에서 호출하지 않습니다. DB 롤백이나 고객자료 삭제는 필요하지 않습니다. 원래 작업폴더의 미완료 변경사항을 덮어쓰지 않도록 적용 전 파일별 차이를 확인하세요.

## GitHub 반영 명령 예시

아래 명령은 배포 승인 후 최신 main 작업폴더에서 실행합니다. 관계없는 변경 파일을 포함하지 않도록 명시한 파일만 추가합니다.

```bash
git status
git add -- app.py articles_review.py consultation_journal.py employee_status.py enterprise_documents.py enterprise_registration_ui.py registry_runner.py stock_valuation.py ui.py upload_progress.py utils.py tests/test_upload_progress.py tests/test_registry_upload_progress.py tests/test_upload_workflow_feedback.py VERSION.txt RUN_v9.14.10-upload-progress.bat docs/CHANGELOG-upload-progress-v9.14.10.md
git commit -m "fix: show upload progress and protect registry registration"
git push origin main
```

연결 서비스가 main 변경으로 자동 재배포될 수 있으므로 사용자 승인 없이 명령을 실행하지 않습니다.
