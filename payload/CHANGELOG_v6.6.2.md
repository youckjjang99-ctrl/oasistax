# CHANGELOG v6.6.2

## Streamlit XML 처리 및 추천결과 분류

- `pandas.read_xml()` 사용을 제거해 Streamlit Cloud의 lxml 미설치 오류 해결
- 파이썬 기본 `xml.etree.ElementTree`로 XML API 응답 처리
- JSON 응답 우선, XML 응답 자동 대체 처리
- 추천 결과를 전체·정책자금/보증·고용지원금·공고형 지원사업 탭으로 분리
- 내부DB `source_type`을 우선 사용하여 상시정책자금과 고용지원금 정확히 분류
- 외부 공고는 제목·내용·기관·키워드를 이용해 자동 분류
- 각 결과 표에 분류 컬럼 추가
- 기존 내부 정책DB·기업마당 데이터·매칭 결과 보존
- Supabase SQL 추가 실행 불필요
