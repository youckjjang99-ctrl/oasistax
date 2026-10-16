# v9.14.4 변경 파일 전체 목록

기준 `bbd424a18be06e9829c9cd9e3a930b4f4ffd4e98` 이후 변경만 포함합니다. v9.14.3까지 적용된 프로젝트에 덮어쓰는 소스 패치이며 `.git`·고객 파일·비밀키·최상위 포장 폴더를 포함하지 않습니다.

- `VERSION.txt`
- `RUN_v9.14.4.bat`
- `voice_calling_ui.py`
- `voice_calling_dashboard.py`
- `voice_calling_repository.py`
- `voice_calling_launch.py`
- `supabase/migrations/20261007051303_ai_visit_calling_simple_campaigns.sql`
- `supabase/migrations/20261007051446_ai_visit_calling_manual_targets.sql`
- `tests/test_voice_calling_ui.py`
- `tests/test_voice_calling_ui_render.py`
- `tests/test_voice_calling_dashboard.py`
- `tests/test_voice_calling_launch.py`
- `tests/test_voice_calling_clawops_gateway.py`
- `tests/test_voice_calling_gateway.py`
- `tests/voice_calling_simple_campaigns.integration.mjs`
- `tests/voice_calling_manual.integration.mjs`
- `tests/voice_calling_simple_staging.verify.sql`
- `docs/ai-visit-calling-simple-v9.14.4.md`
- `docs/ai-visit-calling-simple-files-v9.14.4.md`
- `docs/CHANGELOG-ai-visit-calling-simple-v9.14.4.md`
- `tools/build_ai_calling_simple_patch.py`

RUN은 발신 OFF인 로컬 화면만 실행합니다. DB 마이그레이션·배포·발신 worker 시작은 자동 실행하지 않습니다.
