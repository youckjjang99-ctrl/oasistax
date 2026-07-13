# v4.3.1 적용방법

1. 압축 안의 모든 파일을 기존 정책자금자동화 폴더에 풉니다.
2. RUN_V431_FIX.bat를 더블클릭합니다.
3. 로컬 확인:

```powershell
streamlit run app.py
```

4. 정상 확인 후 GitHub 반영:

```powershell
git add .
git commit -m "v4.3.1 Streamlit 고객DB 자동복원"
git push
```

5. Streamlit Cloud 재배포 후 관리자 계정으로 로그인합니다.

Supabase에 고객 백업이 있으면 홈 화면에:

```text
Supabase에서 고객 N건을 자동 복원했습니다.
```

라고 표시됩니다.

그 후 고객관리, 정책자금 매칭, AI 컨설팅 리포트에서
기존 고객목록을 확인할 수 있습니다.

기존 로컬 고객DB가 있으면 절대 덮어쓰지 않습니다.
