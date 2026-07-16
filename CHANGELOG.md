# CHANGELOG

## 적용 버전
v8.2.1

## 공고형 정책자금 로딩 복구
- 기업마당 공고명 필드 `pblancNm`, `pblanc_nm` 인식
- Supabase 정규화 필드 `title`, `agency`를 raw_data에 재주입
- 제목 누락으로 공고형 자료가 매칭단계에서 탈락하던 문제 수정
- 내부 정책DB 총건수와 실제 변환건수 차이를 상태표에 표시
- 기존 상시정책자금·고용지원금 매칭 유지
- Supabase SQL 실행 불필요
