# CHANGELOG v6.8.0

## 스캔 PDF 자동 OCR 및 정관 상세검토

- PDF 내장 텍스트를 먼저 확인하고 문자가 부족한 경우에만 자동 OCR 실행
- Streamlit Cloud에 Tesseract 한글·영문 OCR 엔진 자동 설치
- PyMuPDF로 스캔 PDF 페이지를 이미지화하여 한글 OCR 처리
- 페이지별 OCR 진행률 표시
- 최대 120페이지까지 자동 처리
- OCR 방식·처리페이지·인식페이지 수 표시
- 조항별 실제 정관 문구 발췌 표시
- 조항별 대표 설명 스크립트 자동 생성
- 기존 정관 점수·기업히스토리·AI 종합진단 연동 유지
- 기존 PDF/HWP/DOCX/TXT 분석 기능 유지
- Supabase SQL 추가 실행 불필요
