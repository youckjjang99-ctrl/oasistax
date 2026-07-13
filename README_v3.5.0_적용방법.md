# OASIS v3.5.0 적용방법

## 1. 업데이트 적용

1. 압축 안의 모든 내용을 기존 `정책자금자동화` 폴더에 풉니다.
2. `RUN_OASIS_V350_UPDATE.cmd`를 더블클릭합니다.
3. `[SUCCESS]`가 나오면 업데이트 완료입니다.

## 2. GitHub 설정

저장소에서:

`Settings → Secrets and variables → Actions → New repository secret`

- Name: `BIZINFO_API_KEY`
- Secret: 기업마당 API 인증키

그리고:

`Settings → Actions → General → Workflow permissions`

- `Read and write permissions` 선택 후 저장

## 3. GitHub 업로드

```powershell
git add .
git commit -m "v3.5.0 기업마당 자동 동기화"
git push
```

## 4. 자동 동기화 첫 테스트

GitHub 저장소의 `Actions` 메뉴에서:

`Update Bizinfo Funding DB → Run workflow`

초록색 체크가 나오면 정상입니다.

## 5. 자동 실행 시간

매일 한국시간 오전 3시에 자동 실행됩니다.

## 6. Streamlit 관리자 수동 동기화

Streamlit 앱 설정의 Secrets에 추가:

```toml
BIZINFO_API_KEY = "기업마당_API_인증키"
```

그 후 관리자 계정으로 로그인해:

`시스템 관리 → 기업마당 DB 지금 동기화`

버튼을 사용할 수 있습니다.

## 7. 변경된 매칭 구조

기존:
`매칭 클릭 → 기업마당 실시간 접속 → 기다림`

변경:
`새벽 자동 수집 → 내부 DB 저장 → 매칭 클릭 시 내부 DB 즉시 사용`
