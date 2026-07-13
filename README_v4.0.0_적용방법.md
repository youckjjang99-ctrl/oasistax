# OASIS v4.0.0 적용방법

1. 압축 안의 모든 파일을 기존 정책자금자동화 폴더에 풉니다.
2. RUN_V400_UPDATE.bat를 더블클릭합니다.
3. 로컬 실행:
   streamlit run app.py

4. Supabase > SQL Editor에서 supabase_schema.sql 전체를 실행합니다.

5. Streamlit Cloud > Settings > Secrets에 입력:
   SUPABASE_URL = "https://프로젝트ID.supabase.co"
   SUPABASE_SECRET_KEY = "sb_secret_..."

6. 앱 재부팅 후 관리자 로그인 > 클라우드 DB 관리
7. Supabase 연결 테스트
8. 미리보기 확인 후 기존 자료 복사

기존 고객DB는 수정하거나 삭제하지 않습니다.

GitHub 반영:
git add .
git commit -m "v4.0.0 Supabase 클라우드 DB 기반 구축"
git push
