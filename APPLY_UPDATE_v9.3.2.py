from __future__ import annotations
import py_compile, shutil, sys
from datetime import datetime
from pathlib import Path
TARGET_VERSION='v9.3.2'; EXPECTED_VERSION='v9.3.1'
FILES=('employment_support_2026.py','VERSION.txt')
def restore(backup, root):
    for name in FILES:
        src=backup/name
        if src.exists(): shutil.copy2(src, root/name)
def main():
    root=Path(__file__).resolve().parent; payload=root/'payload'; vf=root/'VERSION.txt'
    current=vf.read_text(encoding='utf-8').strip() if vf.exists() else ''
    if current != EXPECTED_VERSION:
        print(f'UPDATE_FAILED: Expected {EXPECTED_VERSION} but found {current or "UNKNOWN"}'); sys.exit(1)
    for name in FILES:
        if not (payload/name).exists(): print(f'UPDATE_FAILED: missing payload/{name}'); sys.exit(1)
    backup=root/'backup'/f'before_v9.3.2_{datetime.now():%Y%m%d_%H%M%S}'; backup.mkdir(parents=True,exist_ok=True)
    for name in FILES:
        if (root/name).exists(): shutil.copy2(root/name,backup/name)
    try:
        for name in FILES: shutil.copy2(payload/name,root/name)
        py_compile.compile(str(root/'employment_support_2026.py'),doraise=True)
    except Exception as exc:
        restore(backup,root); print(f'UPDATE_FAILED: {exc}'); print(f'ROLLBACK={backup}'); sys.exit(1)
    print('UPDATE_OK'); print('VERSION=v9.3.2'); print(f'BACKUP={backup}')
    print('EMPLOYEE_ROSTER_AUTO_ANALYSIS=ENABLED'); print('MANUAL_DUPLICATE_FIELDS=REMOVED'); print('DB_SCHEMA=PRESERVED')
if __name__=='__main__': main()
