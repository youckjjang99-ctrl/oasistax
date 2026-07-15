# CHANGELOG v6.6.1

- 내부 통합 정책DB 자료에 `source` 필드 명시
- 기업마당 저장소 형식을 다중소스 매칭 공통 형식으로 변환
- 기업마당 원본 응답의 사업명·기관명을 공통 필드로 보완
- 중복제거 시 `source` 누락으로 발생하던 KeyError 수정
- `source_name` 또는 `source_type`을 안전한 대체 출처로 사용
- 특정 소스의 출처 필드가 누락돼도 다른 정상 소스 매칭 계속 진행
- 기존 내부 정책DB 34건과 Supabase 데이터 유지
- 신규 Supabase SQL 불필요
