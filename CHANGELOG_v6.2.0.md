# CHANGELOG v6.2.0

## 상담 녹음파일 Supabase Storage 영구보관

### 영구보관
- 원본 mp3·m4a·wav 등 상담 녹음을 Supabase Storage에 저장
- Streamlit 재부팅·GitHub 배포·버전 업데이트 후에도 유지
- 회원/사업자번호/연월별 폴더 구조
- 동일 파일 SHA-256 중복 업로드 방지

### 상담일지 연결
- 저장된 음성파일과 상담일지 journal_id 연결
- 상담 제목과 요약을 녹음 메타데이터에 반영
- 기존 상담일지·CRM 저장 기능 유지

### 기업별 녹음 히스토리
- CRM 화면에서 해당 기업의 클라우드 녹음 목록 확인
- 임시 서명 URL로 브라우저 재생
- 파일명·용량·상담제목·요약 표시
- 필요 시 음성파일 삭제 가능

### 안정성
- Storage 미설정 시 기존 방식으로 녹취·상담일지 생성
- 저장 실패 시 원인을 표시
- 기존 고객DB 수정 없음
- Supabase Service Role Key는 Streamlit Secrets에서만 사용
