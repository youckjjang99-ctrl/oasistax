# v5.2.0 적용방법

이번 버전이 요청하신 녹음파일 상담일지 자동작성 기능입니다.

## 적용

1. 압축 안의 모든 파일을 기존 정책자금자동화 폴더에 풉니다.
2. `RUN_V520_UPDATE.bat`를 더블클릭합니다.
3. GitHub에 반영합니다.

```powershell
git add .
git commit -m "v5.2.0 녹음 상담일지 자동작성"
git push
```

## OpenAI API 키 설정

Streamlit Cloud:

```text
앱 목록 → 점 세 개 → Settings → Secrets
```

아래 한 줄을 추가합니다.

```toml
OPENAI_API_KEY = "sk-본인의_API_KEY"
```

선택 설정:

```toml
OPENAI_SUMMARY_MODEL = "gpt-5-mini"
```

API 키는 GitHub 코드나 채팅에 올리지 마세요.

## Streamlit 재부팅

`packages.txt`에 ffmpeg가 추가되므로 GitHub 반영 후 Streamlit Cloud를 Reboot합니다.

## 사용

```text
기업 컨설팅
→ 고객 선택
→ CRM
→ 녹음파일 상담일지 자동작성
```

1. m4a 또는 다른 녹음파일 업로드
2. `녹취 및 상담일지 생성`
3. 생성된 초안 검토·수정
4. `상담일지 및 CRM 저장`

저장되는 내용:

- 상담 요약
- 고객 니즈
- 주요 논의사항
- 추천 검토사항
- 필요서류
- 위험·확인사항
- 다음 액션
- 다음 연락 예정일
- 전체 녹취록
- CRM 메모와 타임라인

25MB를 초과하는 파일은 ffmpeg로 자동 분할합니다.
