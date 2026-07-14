# v5.5.0 적용방법

1. 압축을 기존 정책자금자동화 폴더에 풉니다.
2. RUN_V550_UPDATE.bat를 실행합니다.
3. Supabase SQL Editor에서 supabase_v550_upgrade.sql을 실행합니다.
4. GitHub에 반영합니다.

```powershell
git add .
git commit -m "v5.5.0 AI 사용량 비용 대시보드"
git push
```

5. Streamlit Cloud를 Reboot합니다.

권장 Secrets:

```toml
AI_USAGE_USD_KRW_RATE = "1400"
OPENAI_TRANSCRIPTION_USD_PER_MINUTE = "0.003"
OPENAI_SUMMARY_INPUT_USD_PER_1M = "0.25"
OPENAI_SUMMARY_OUTPUT_USD_PER_1M = "2.00"
```

관리자 로그인 후 `AI 사용량` 메뉴에서 확인합니다.
비용은 예상치이며 실제 OpenAI 청구액과 차이가 날 수 있습니다.
