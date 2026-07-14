# v6.2.1 적용방법

이번 패치는 상담녹음 실행이 멈춘 뒤 실제 오류문구가 사라지는 문제를 수정합니다.

1. 압축을 기존 정책자금자동화 폴더에 풉니다.
2. `RUN_V621_FIX.bat`를 실행합니다.
3. GitHub에 반영합니다.

```powershell
git add .
git commit -m "v6.2.1 상담녹음 오류표시 대용량업로드 안정화"
git push
```

4. Streamlit Cloud를 Reboot합니다.

변경사항:
- 오류 상세내용을 화면에 계속 표시
- 78MB 이상 Storage 업로드 제한시간 확대
- Supabase 저장 실패 시에도 녹취와 상담일지 생성 계속
- 원본 저장 실패 원인은 별도 경고로 표시
