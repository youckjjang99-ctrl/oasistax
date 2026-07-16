from __future__ import annotations
import py_compile, shutil, sys
from pathlib import Path
VERSION='v7.4.3'; TARGET_FILES=['system_precheck.py','update_safety.py','maintenance.py','VERSION.txt']
def fail(message): print('UPDATE_FAILED'); print(message); input('Press Enter to close...'); raise SystemExit(1)
def patch_maintenance(text):
    anchor='from collector import sync_bizinfo_cache\n'
    if 'from system_precheck import run_precheck' not in text:
        if anchor not in text: raise RuntimeError('maintenance.py import 위치를 찾지 못했습니다.')
        text=text.replace(anchor,anchor+'from system_precheck import run_precheck\n',1)
    section_anchor='    st.markdown("#### 프로젝트 상태")\n'
    section=(
        '    st.markdown("#### 배포 사전점검")\n'
        '    precheck_report = run_precheck(project_root, save_report=True)\n'
        '    p1, p2, p3 = st.columns(3)\n'
        '    p1.metric("배포 가능 여부", "가능" if precheck_report.get("status") == "PASS" else "차단")\n'
        '    p2.metric("오류", f"{precheck_report.get(\"error_count\", 0)}개")\n'
        '    p3.metric("경고", f"{precheck_report.get(\"warning_count\", 0)}개")\n'
        '    failed_checks = [row for row in precheck_report.get("checks", []) if not row.get("ok")]\n'
        '    if failed_checks:\n'
        '        st.dataframe(failed_checks, hide_index=True, use_container_width=True)\n'
        '    else:\n'
        '        st.success("문법·Import·필수함수·Streamlit 옵션 검사 결과 배포 가능합니다.")\n'
        '    if st.button("사전점검 다시 실행", key="system_run_precheck_v743", use_container_width=True):\n'
        '        st.rerun()\n\n'
    )
    if '#### 배포 사전점검' not in text:
        if section_anchor not in text: raise RuntimeError('maintenance.py 시스템 상태 위치를 찾지 못했습니다.')
        text=text.replace(section_anchor,section+section_anchor,1)
    text=text.replace('git commit -m "v3.5.0 기업마당 자동 동기화"','git commit -m "v7.4.3 시스템 사전점검 자동롤백 배포안정화"')
    return text
def main():
    root=Path.cwd()
    if not (root/'app.py').exists(): fail('app.py가 있는 프로젝트 루트에서 실행해주세요.')
    version_path=root/'VERSION.txt'; current=version_path.read_text(encoding='utf-8-sig').strip() if version_path.exists() else ''
    if current and current not in {'v7.4.2','7.4.2','v7.4.3','7.4.3'}: fail(f'Expected v7.4.2 but found {current}.')
    sys.path.insert(0,str(root/'payload')); from update_safety import create_update_backup, rollback_update
    backup=create_update_backup(root,VERSION,TARGET_FILES)
    try:
        for rel in ['system_precheck.py','update_safety.py']:
            src=root/'payload'/rel
            if not src.exists(): raise FileNotFoundError(f'payload/{rel} 누락')
            shutil.copy2(src,root/rel)
        m=root/'maintenance.py'; m.write_text(patch_maintenance(m.read_text(encoding='utf-8')),encoding='utf-8',newline='\n')
        version_path.write_text(VERSION+'\n',encoding='utf-8')
        c=root/'payload'/'CHANGELOG_v7.4.3.md'
        if c.exists(): shutil.copy2(c,root/'CHANGELOG_v7.4.3.md')
        for name in ['system_precheck.py','update_safety.py','maintenance.py','app.py','enterprise_center.py','enterprise_customer_management.py','employee_status.py','multi_source_policy.py']:
            p=root/name
            if p.exists(): py_compile.compile(str(p),doraise=True)
        sys.path.insert(0,str(root)); from system_precheck import run_precheck
        report=run_precheck(root,save_report=True)
        if report.get('status')!='PASS':
            failed=[r for r in report.get('checks',[]) if not r.get('ok') and r.get('level')=='error']
            raise RuntimeError('사전점검 실패: '+'; '.join(f"{r.get('item')}: {r.get('message')}" for r in failed[:8]))
    except Exception as exc:
        restored=rollback_update(root,backup,TARGET_FILES); print('UPDATE_ROLLED_BACK'); print(f'BACKUP={backup}'); print('RESTORED='+','.join(restored)); fail(f'{type(exc).__name__}: {exc}')
    print('UPDATE_OK'); print(f'VERSION={VERSION}'); print(f'BACKUP={backup}'); print('PRECHECK=PASS'); print('AUTO_ROLLBACK=ENABLED'); print('SQL_REQUIRED=NO'); input('Press Enter to close...')
if __name__=='__main__': main()
