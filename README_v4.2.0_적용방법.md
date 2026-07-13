# v4.2.0 적용방법

1. 압축 안의 모든 파일을 기존 정책자금자동화 폴더에 풉니다.
2. RUN_V420_UPDATE.bat를 더블클릭합니다.
3. Supabase SQL Editor에서 supabase_v420_upgrade.sql을 실행합니다.
4. 아래 명령으로 실행합니다.

```powershell
streamlit run app.py
```

관리자 로그인 후:

```text
클라우드 DB 관리
→ 실시간 이중저장 상태
```

정상 상태:

```text
Supabase 설정: 연결됨
동기화 대기: 0건
```

새 고객·CRM·재무·등기·주가평가·매칭설정은
기존 파일과 Supabase에 동시에 저장됩니다.

Supabase 장애가 발생해도 기존 로컬 저장은 완료되며,
실패 자료는 cloud_sync_queue.json에 보관됩니다.

```powershell
git add .
git commit -m "v4.2.0 파일 Supabase 실시간 이중저장"
git push
```
