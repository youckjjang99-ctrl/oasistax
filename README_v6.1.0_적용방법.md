# v6.1.0 적용방법

## 적용

1. 압축 안의 파일을 기존 정책자금자동화 폴더에 풉니다.
2. `RUN_V610_UPDATE.bat`를 실행합니다.
3. GitHub에 반영합니다.

```powershell
git add .
git commit -m "v6.1.0 내부직원 AI 컨설팅 코파일럿"
git push
```

4. Streamlit Cloud를 Reboot합니다.

## 사용

왼쪽 메뉴의 `AI 코파일럿`을 선택합니다.

- 고객 선택
- 이번 상담 우선순위 확인
- 필수질문·요청서류 체크
- 기업별 메모리 기록
- 실제 성공사례 등록
- 유사 성공사례 확인
- 미팅 종료 후 누락사항 점검

## 데이터 저장

- consulting_copilot_memory.json
- consulting_success_cases.json
- consulting_checklists.json

기존 고객DB는 수정하지 않습니다.
추가 OpenAI API 호출도 없습니다.
