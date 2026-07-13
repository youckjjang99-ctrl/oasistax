# CHANGELOG v3.5.1

## 기업마당 모듈 누락 긴급 수정

### 원인
v3.5.0 적용 과정에서 `maintenance.py`는 반영되었지만,
프로젝트 최상위 폴더에 `bizinfo_cache.py`와 `collector.py`가 복사되지 않은 환경이 있어
Streamlit 실행 시 `ModuleNotFoundError`가 발생했습니다.

### 수정
- `bizinfo_cache.py`를 프로젝트 최상위 폴더에 강제 배치
- `collector.py`를 프로젝트 최상위 폴더에 강제 배치
- `main.py`, `maintenance.py`를 v3.5 자동동기화 기준으로 재동기화
- GitHub Actions workflow 재배치
- 버전 `v3.5.1` 반영

### 기존 기능 영향
기존 고객DB, CRM, 크레탑, 회원관리 및 매칭 기능은 유지됩니다.
