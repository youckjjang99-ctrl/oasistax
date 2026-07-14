# v6.0.0 적용방법

## 적용

1. 압축 안의 파일을 기존 정책자금자동화 폴더에 풉니다.
2. `RUN_V600_UPDATE.bat`를 실행합니다.
3. GitHub에 반영합니다.

```powershell
git add .
git commit -m "v6.0.0 다중소스 증거기반 정책자금 매칭"
git push
```

4. Streamlit Cloud를 Reboot합니다.

## 사용

```text
정책자금 매칭
→ 고객 선택
→ 고객별 매칭설정
→ 다중소스 AI 매칭 실행
```

기존 기업마당·상시형 정책자금·고용지원금 DB는 별도 설정 없이 사용합니다.

## K-Startup API 설정

K-Startup에서 인증키와 호출 URL을 발급받은 뒤 Streamlit Secrets에 입력합니다.

```toml
KSTARTUP_API_URL = "K-Startup API 호출 URL"
KSTARTUP_API_KEY = "인증키"
KSTARTUP_API_PARAMS_JSON = '{"pageNo":1,"numOfRows":1000,"type":"json"}'
```

API 문서에 따라 키 파라미터명이 다르면:

```toml
KSTARTUP_API_PARAMS_JSON = '{"_key_parameter":"serviceKey","pageNo":1,"numOfRows":1000}'
```

처럼 설정합니다.

## 중진공 OpenAPI 설정

중진공 OpenAPI에서 이용할 데이터셋의 호출 URL과 인증키를 입력합니다.

```toml
KOSMES_API_URL = "중진공 OpenAPI 호출 URL"
KOSMES_API_KEY = "인증키"
KOSMES_API_PARAMS_JSON = '{"pageNo":1,"numOfRows":1000}'
```

## 추천점수

점수는 다음 근거를 조합합니다.

- 상담녹취 추천키워드
- 직접 입력한 매칭키워드
- 관심지원분야와 자금사용목적
- 업종과 지역
- 설립연도와 창업업력
- 제외키워드
- 공고 신청기간

추천 근거와 감점사유가 함께 표시됩니다.

외부 API가 설정되지 않아도 기존 내부DB만으로 작동합니다.
