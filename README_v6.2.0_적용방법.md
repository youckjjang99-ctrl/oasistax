# v6.2.0 적용방법

## 1. 패치 적용

1. 압축 안의 파일을 기존 정책자금자동화 폴더에 풉니다.
2. `RUN_V620_UPDATE.bat`를 실행합니다.

## 2. Supabase Storage와 테이블 생성

프로젝트 폴더의 `supabase_v620_upgrade.sql` 내용을 전체 복사합니다.

```text
Supabase
→ SQL Editor
→ New query
→ 붙여넣기
→ Run
```

실행 후 Supabase 왼쪽 `Storage` 메뉴에
`oasis-consultation-audio` 버킷이 생겼는지 확인합니다.

## 3. Streamlit Secrets 확인

기존 Supabase 설정에 Service Role Key가 있어야 합니다.

```toml
SUPABASE_URL = "https://프로젝트주소.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = "service_role 키"
```

프로젝트에서 기존 이름을 사용 중이면 아래 이름도 인식합니다.

```toml
SUPABASE_SERVICE_KEY = "service_role 키"
```

`anon public` 키가 아니라 Supabase Project Settings → API Keys의
`service_role` 비밀키를 입력해야 합니다. GitHub에는 절대 올리지 않습니다.

## 4. GitHub 반영

```powershell
git add .
git commit -m "v6.2.0 상담녹음 Supabase 영구보관"
git push
```

Streamlit Cloud를 Reboot합니다.

## 5. 사용

```text
기업 컨설팅
→ 고객 선택
→ CRM
→ 녹음파일 상담일지 자동작성
```

녹음파일 업로드 후 생성하면 먼저 Supabase Storage에 원본을 보관한 뒤
녹취와 상담일지를 만듭니다.

같은 CRM 화면의 `클라우드 녹음 히스토리`에서:
- 재생
- 상담요약 확인
- 파일 삭제

를 할 수 있습니다.

버전 업데이트와 Streamlit 재부팅 후에도 저장된 원본 음성은 유지됩니다.
