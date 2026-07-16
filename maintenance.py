from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable

import streamlit as st

from bizinfo_cache import get_bizinfo_cache_status
from collector import sync_bizinfo_cache
from system_precheck import run_precheck


CORE_FILES = (
    "app.py",
    "auth.py",
    "crm.py",
    "history.py",
    "main.py",
    "ui.py",
    "utils.py",
    "requirements.txt",
    "VERSION.txt",
)

DATA_PATHS = (
    "data",
    "templates",
)

BACKUP_ROOT_NAME = "_oasis_backups"
UPDATE_LOG_NAME = "update_history.json"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def read_version(project_root: Path) -> str:
    path = project_root / "VERSION.txt"
    if not path.exists():
        return "확인 불가"
    try:
        value = path.read_text(encoding="utf-8").strip()
        return value or "확인 불가"
    except OSError:
        return "확인 불가"


def get_project_health(project_root: Path) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    for name in CORE_FILES:
        path = project_root / name
        checks.append({
            "항목": name,
            "상태": "정상" if path.exists() else "누락",
            "경로": str(path),
        })

    for name in ("user_data", "uploads", "results"):
        path = project_root / name
        checks.append({
            "항목": f"{name}/",
            "상태": "정상" if path.exists() else "미생성",
            "경로": str(path),
        })
    return checks


def _copy_path(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True)
    elif source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def create_project_backup(
    project_root: Path,
    label: str = "manual",
    include_runtime_data: bool = True,
) -> Path:
    backup_root = project_root / BACKUP_ROOT_NAME
    backup_root.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_label = "".join(ch for ch in label if ch.isalnum() or ch in ("-", "_")) or "backup"
    backup_dir = backup_root / f"{timestamp}_{safe_label}"
    backup_dir.mkdir(parents=True, exist_ok=False)

    for name in CORE_FILES:
        source = project_root / name
        if source.exists():
            _copy_path(source, backup_dir / name)

    for name in DATA_PATHS:
        source = project_root / name
        if source.exists():
            _copy_path(source, backup_dir / name)

    if include_runtime_data:
        for name in ("user_data",):
            source = project_root / name
            if source.exists():
                _copy_path(source, backup_dir / name)

    manifest = {
        "created_at": _now(),
        "label": safe_label,
        "version": read_version(project_root),
        "python": sys.version,
        "included_runtime_data": include_runtime_data,
        "files": sorted(
            str(path.relative_to(backup_dir))
            for path in backup_dir.rglob("*")
            if path.is_file()
        ),
    }
    (backup_dir / "backup_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return backup_dir


def list_backups(project_root: Path) -> list[dict[str, str]]:
    backup_root = project_root / BACKUP_ROOT_NAME
    if not backup_root.exists():
        return []

    rows: list[dict[str, str]] = []
    for backup_dir in sorted(
        (path for path in backup_root.iterdir() if path.is_dir()),
        key=lambda path: path.name,
        reverse=True,
    ):
        manifest_path = backup_dir / "backup_manifest.json"
        manifest = {}
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                manifest = {}

        rows.append({
            "백업명": backup_dir.name,
            "생성일시": manifest.get("created_at", ""),
            "버전": manifest.get("version", ""),
            "데이터포함": "Y" if manifest.get("included_runtime_data") else "N",
            "경로": str(backup_dir),
        })
    return rows


def restore_backup(project_root: Path, backup_name: str) -> tuple[bool, str]:
    backup_dir = project_root / BACKUP_ROOT_NAME / backup_name
    if not backup_dir.exists() or not backup_dir.is_dir():
        return False, "선택한 백업 폴더를 찾지 못했습니다."

    # 복원 직전 안전백업
    create_project_backup(
        project_root,
        label="before_restore",
        include_runtime_data=True,
    )

    restored = 0
    for item in backup_dir.iterdir():
        if item.name == "backup_manifest.json":
            continue
        destination = project_root / item.name
        _copy_path(item, destination)
        restored += 1

    if restored == 0:
        return False, "복원할 파일이 없습니다."
    return True, f"{backup_name} 백업으로 복원했습니다. 앱을 재시작해주세요."


def read_update_history(project_root: Path) -> list[dict]:
    path = project_root / UPDATE_LOG_NAME
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def append_update_history(project_root: Path, entry: dict) -> None:
    path = project_root / UPDATE_LOG_NAME
    history = read_update_history(project_root)
    history.insert(0, entry)
    path.write_text(
        json.dumps(history[:100], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def render_system_management_page(
    project_root: Path,
    current_user_id: str,
) -> None:
    st.markdown("### 시스템 관리")
    st.caption("관리자 전용 버전·백업·복원·프로젝트 점검 화면입니다.")

    version = read_version(project_root)
    backups = list_backups(project_root)
    health = get_project_health(project_root)
    missing_count = sum(1 for row in health if row["상태"] == "누락")

    c1, c2, c3 = st.columns(3)
    c1.metric("현재 버전", version)
    c2.metric("저장된 백업", f"{len(backups)}개")
    c3.metric("필수파일 점검", "정상" if missing_count == 0 else f"{missing_count}개 누락")

    st.markdown("#### 배포 사전점검")
    precheck_report = run_precheck(project_root, save_report=True)
    p1, p2, p3 = st.columns(3)
    p1.metric("배포 가능 여부", "가능" if precheck_report.get("status") == "PASS" else "차단")
    p2.metric("오류", f"{precheck_report.get('error_count', 0)}개")
    p3.metric("경고", f"{precheck_report.get('warning_count', 0)}개")
    failed_checks = [row for row in precheck_report.get("checks", []) if not row.get("ok")]
    if failed_checks:
        st.dataframe(failed_checks, hide_index=True, use_container_width=True)
    else:
        st.success("문법·Import·필수함수·Streamlit 옵션 검사 결과 배포 가능합니다.")
    if st.button("사전점검 다시 실행", key="system_run_precheck_v743", use_container_width=True):
        st.rerun()

    st.markdown("#### 프로젝트 상태")
    st.dataframe(health, hide_index=True, use_container_width=True)

    st.markdown("#### 기업마당 공고DB 자동 동기화")
    bizinfo_status = get_bizinfo_cache_status(project_root / "data")
    b1, b2, b3 = st.columns(3)
    b1.metric("내부 공고 수", f"{bizinfo_status.get('record_count', 0)}건")
    b2.metric(
        "마지막 갱신",
        bizinfo_status.get("generated_at")
        or bizinfo_status.get("file_modified_at")
        or "미생성",
    )
    b3.metric(
        "동기화 상태",
        "정상" if bizinfo_status.get("status") == "success" else "확인 필요",
    )

    if bizinfo_status.get("message"):
        st.warning(bizinfo_status.get("message"))

    st.caption(
        "GitHub Actions가 매일 한국시간 오전 3시에 기업마당 공고DB를 자동 갱신합니다. "
        "매칭은 저장된 내부 DB를 사용하므로 기업마당이 일시적으로 느려도 계속 실행됩니다."
    )

    if st.button(
        "기업마당 DB 지금 동기화",
        key="system_sync_bizinfo",
        use_container_width=True,
    ):
        with st.spinner("기업마당 공고를 수집하고 내부 DB를 갱신하고 있습니다..."):
            try:
                result = sync_bizinfo_cache(
                    output_dir=project_root / "data",
                    api_key=os.getenv("BIZINFO_API_KEY", ""),
                    page_count=10,
                    page_size=100,
                    source=f"manual:{current_user_id}",
                    strict=True,
                )
                st.success(
                    f"동기화 완료: {result.get('record_count', 0)}건 / "
                    f"{result.get('generated_at', '')}"
                )
                st.rerun()
            except Exception as exc:
                st.error(f"동기화 실패: {type(exc).__name__}: {exc}")

    st.markdown("#### 수동 백업")
    col1, col2 = st.columns([1.4, 1])
    with col1:
        backup_label = st.text_input(
            "백업 이름",
            value="manual",
            key="system_backup_label",
        )
        include_data = st.checkbox(
            "회원별 고객DB와 CRM 데이터도 백업",
            value=True,
            key="system_backup_include_data",
        )
    with col2:
        st.info(
            "백업은 프로젝트의 `_oasis_backups` 폴더에 저장됩니다. "
            "Streamlit Cloud 파일은 재배포 시 초기화될 수 있으므로 중요한 고객DB는 별도 다운로드도 유지해주세요."
        )

    if st.button("지금 백업 만들기", key="system_create_backup", use_container_width=True):
        try:
            backup_dir = create_project_backup(
                project_root,
                label=backup_label,
                include_runtime_data=include_data,
            )
            st.success(f"백업 완료: {backup_dir.name}")
            st.rerun()
        except Exception as exc:
            st.error(f"백업 실패: {type(exc).__name__}: {exc}")

    st.markdown("#### 백업 및 복원")
    if not backups:
        st.info("아직 생성된 백업이 없습니다.")
    else:
        st.dataframe(backups, hide_index=True, use_container_width=True)
        backup_names = [row["백업명"] for row in backups]
        selected = st.selectbox(
            "복원할 백업",
            backup_names,
            key="system_restore_backup_name",
        )
        confirm = st.checkbox(
            "선택한 백업으로 복원하는 것에 동의합니다.",
            key="system_restore_confirm",
        )
        if st.button(
            "선택한 백업 복원",
            key="system_restore_backup",
            disabled=not confirm,
            use_container_width=True,
        ):
            try:
                ok, message = restore_backup(project_root, selected)
                if ok:
                    st.success(message)
                else:
                    st.error(message)
            except Exception as exc:
                st.error(f"복원 실패: {type(exc).__name__}: {exc}")

    st.markdown("#### 업데이트 기록")
    history = read_update_history(project_root)
    if history:
        st.dataframe(history, hide_index=True, use_container_width=True)
    else:
        st.caption("저장된 업데이트 기록이 없습니다.")

    st.markdown("#### Git 반영 안내")
    st.code(
        'git add .\n'
        'git commit -m "v7.4.3 시스템 사전점검 자동롤백 배포안정화"\n'
        'git push',
        language="powershell",
    )
