# v6.2.2 적용방법

이번 패치는 다음 두 문제를 함께 수정합니다.

1. `Invalid format specifier ' true, "confidence"...'` 상담일지 생성 오류
2. 대용량 녹음의 Supabase 업로드 때문에 녹취 시작이 늦어지는 문제

## 적용

1. 압축 안의 파일을 기존 정책자금자동화 폴더에 풉니다.
2. `RUN_V622_FIX.bat`를 실행합니다.
3. GitHub에 반영합니다.

```powershell
git add .
git commit -m "v6.2.2 상담일지 JSON오류 대용량속도 개선"
git push
```

4. Streamlit Cloud를 Reboot합니다.

## 변경된 처리순서

```text
파일 업로드 완료
→ 녹취
→ 상담일지 분석
→ 압축본 Supabase 저장
→ 결과 표시
```

기본 `빠른 클라우드 보관`을 사용하면 78MB 원본을
모노·16kHz·48kbps m4a로 압축한 뒤 Supabase에 저장합니다.

정확한 원본 파일을 그대로 보관해야 한다면
`빠른 클라우드 보관` 체크를 해제하면 됩니다.

PC에서 Streamlit로 파일이 처음 올라가는 시간은 인터넷 업로드 속도에 따라 달라지지만,
그 이후의 Supabase 2차 업로드와 처리 대기시간은 크게 줄어듭니다.
