# CHANGELOG v3.5.0

## 기업마당 공고DB 새벽 3시 자동 동기화

### 수정 파일
- `main.py`
- `maintenance.py`
- `VERSION.txt`
- `.gitignore`

### 신규 파일
- `collector.py`
- `bizinfo_cache.py`
- `.github/workflows/update_bizinfo_db.yml`
- `CHANGELOG_v3.5.0.md`

### 주요 변경
1. GitHub Actions가 매일 한국시간 오전 3시에 기업마당 공고를 자동 수집합니다.
2. 신규 공고를 중복 제거한 뒤 `data/bizinfo_programs.json`과 엑셀 DB로 저장합니다.
3. 정책자금 매칭은 기업마당을 매번 실시간 호출하지 않고 내부 DB를 사용합니다.
4. 기업마당 서버가 일시적으로 느리거나 실패해도 기존 내부 DB는 보존됩니다.
5. 관리자 `시스템 관리` 화면에서 공고 수, 마지막 갱신일, 상태를 확인할 수 있습니다.
6. 관리자가 `기업마당 DB 지금 동기화` 버튼으로 즉시 수동 갱신할 수 있습니다.
7. API 요청은 재시도와 타임아웃 예외처리를 적용합니다.

### 기존 기능 영향
- 고객관리, 크레탑 자동등록, 상시정책자금DB, 고용지원금DB, 매칭 점수 로직은 유지합니다.
- 공고형 지원사업 데이터의 공급 방식만 내부 DB 우선으로 변경합니다.

### GitHub 필수 설정
저장소의 `Settings → Secrets and variables → Actions`에서:
- 이름: `BIZINFO_API_KEY`
- 값: 기업마당 API 인증키

`Settings → Actions → General → Workflow permissions`에서:
- `Read and write permissions` 선택

### Streamlit 수동 동기화 설정
Streamlit 앱 설정의 Secrets에:
```toml
BIZINFO_API_KEY = "기업마당_API_인증키"
```
