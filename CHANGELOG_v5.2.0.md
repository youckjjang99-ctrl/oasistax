# CHANGELOG v5.2.0

## 녹음파일 상담일지 자동작성

### 신규 기능
- 기업 컨설팅 → CRM 탭에서 녹음파일 업로드
- mp3, mp4, mpeg, mpga, m4a, wav, webm 지원
- OpenAI 음성인식으로 한국어 녹취 생성
- 25MB 초과 녹음파일 자동 분할
- 상담요약·고객 니즈·주요 논의·추천사항 자동작성
- 필요서류·위험요소·다음 액션 자동정리
- 생성 결과를 사용자가 직접 수정 후 저장
- 상담일지 별도 보관
- CRM 상담메모·상태·다음 액션·예정일 자동반영
- 상담 타임라인 자동추가
- CRM 로컬 + Supabase 동시저장

### 설정
Streamlit Secrets에 아래 값이 필요합니다.

OPENAI_API_KEY = "sk-..."

선택 설정:
OPENAI_SUMMARY_MODEL = "gpt-5-mini"

### 대용량 파일
- OpenAI 음성 업로드 제한에 맞춰 25MB 초과 파일 자동분할
- Streamlit Cloud에서 ffmpeg 설치를 위해 packages.txt에 ffmpeg 추가

### 데이터 보호
- 기존 고객DB 수정 없음
- 상담일지는 회원별 consultation_journals.json에 별도 저장
- 저장 전 초안을 직접 수정 가능
