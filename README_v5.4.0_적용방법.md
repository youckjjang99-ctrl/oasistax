# v5.4.0 적용방법

이번 버전은 녹음 상담일지의 API 비용과 처리시간을 줄이는 업데이트입니다.

## 적용

1. 압축 안의 파일을 기존 정책자금자동화 폴더에 풉니다.
2. `RUN_V540_UPDATE.bat`를 실행합니다.
3. GitHub에 반영합니다.

```powershell
git add .
git commit -m "v5.4.0 상담일지 API 비용 속도 최적화"
git push
```

4. Streamlit Cloud를 Reboot합니다.

## 자동 절감 기능

- 동일 녹음파일을 다시 올리면 기존 녹취를 재사용
- 동일 녹취로 상담일지를 다시 열면 기존 상담일지를 재사용
- 새 녹음은 모노·16kHz·48kbps로 압축 후 전송
- 기본 음성 모델은 `gpt-4o-mini-transcribe`
- 녹취는 유지하고 상담일지만 다시 생성 가능

## 권장 Secrets

```toml
OPENAI_API_KEY = "sk-..."
OPENAI_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"
OPENAI_SUMMARY_MODEL = "gpt-5-mini"
```

기존 API 키는 그대로 사용하면 됩니다.
