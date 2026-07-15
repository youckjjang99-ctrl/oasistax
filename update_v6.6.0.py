from __future__ import annotations

import py_compile
import shutil
from datetime import datetime
from pathlib import Path

VERSION = "v6.6.0"
TARGETS = ["app.py","multi_source_policy.py","integrated_policy_repository.py","data/internal_policy_seed.json","VERSION.txt"]


def fail(message: str) -> None:
    print("UPDATE_FAILED")
    print(message)
    input("Press Enter to close...")
    raise SystemExit(1)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        fail(f"Patch point not found: {label}")
    return text.replace(old, new, 1)


def main() -> None:
    root = Path.cwd()
    if not (root / "app.py").exists():
        fail("Run this patch from the OASIS project root folder.")

    version_path = root / "VERSION.txt"
    current = version_path.read_text(encoding="utf-8-sig").strip() if version_path.exists() else ""
    if current and current not in {"v6.5.0","6.5.0","v6.6.0","6.6.0"}:
        fail(f"Expected v6.5.0 but found {current}.")

    backup = root / "_oasis_backups" / ("before_v6.6.0_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    backup.mkdir(parents=True, exist_ok=True)
    for name in TARGETS:
        src = root / name
        if src.exists():
            dst = backup / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    for relative in ["integrated_policy_repository.py","data/internal_policy_seed.json"]:
        src = root / "payload" / relative
        if not src.exists():
            fail(f"payload/{relative} is missing.")
        dst = root / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    path = root / "multi_source_policy.py"
    text = path.read_text(encoding="utf-8")

    if "integrated_policy_repository" not in text:
        text = replace_once(
            text,
            "from utils import ROOT_DIR, get_user_dirs\n",
            "from utils import ROOT_DIR, get_user_dirs\nfrom integrated_policy_repository import (\n    fetch_bizinfo_records,\n    load_repository_records,\n    refresh_repository,\n    repository_status,\n)\n",
            "repository import",
        )

    old_local = '''def load_local_sources() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    files = _candidate_excel_files()
    loaded_sheets = 0

    for path in files:
        try:
            excel = pd.ExcelFile(path)
        except Exception:
            continue

        for sheet in excel.sheet_names:
            sheet_key = _normalize_text(sheet)
            if not any(
                keyword in sheet_key
                for keyword in [
                    "지원", "정책", "고용", "공고", "상시",
                ]
            ):
                continue
            try:
                frame = pd.read_excel(path, sheet_name=sheet)
            except Exception:
                continue

            loaded_sheets += 1
            source_name = f"내부DB:{sheet}"
            for raw in frame.dropna(how="all").to_dict("records"):
                normalized = normalize_record(raw, source_name)
                if normalized:
                    records.append(normalized)

    return records, {
        "source": "기존 내부DB",
        "status": "정상" if records else "자료없음",
        "message": f"파일 {len(files)}개 / 시트 {loaded_sheets}개 확인",
        "count": len(records),
    }
'''
    new_local = '''def load_local_sources() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    repository_rows, status = load_repository_records()
    records: list[dict[str, Any]] = []
    for row in repository_rows:
        raw = row.get("raw_data", {})
        if not isinstance(raw, dict):
            continue
        enriched = dict(raw)
        if not enriched.get("기관명") and enriched.get("기관"):
            enriched["기관명"] = enriched.get("기관")
        if not enriched.get("사업명") and enriched.get("상품명"):
            enriched["사업명"] = enriched.get("상품명")
        if not enriched.get("사업명") and enriched.get("제도명"):
            enriched["사업명"] = enriched.get("제도명")
        source_type = str(row.get("source_type", "internal"))
        source_name = str(row.get("source_name", "") or source_type)
        normalized = normalize_record(enriched, f"{source_name}:{source_type}")
        if normalized:
            normalized["repository_id"] = row.get("record_id")
            records.append(normalized)
    return records, {
        "source": "내부 통합 정책DB",
        "status": "정상" if records else "자료없음",
        "message": status.get("message", ""),
        "count": len(records),
    }
'''
    text = replace_once(text, old_local, new_local, "internal DB loader")

    text = replace_once(
        text,
        "    for loader in [\n        load_local_sources,\n        fetch_kstartup,\n        fetch_kosmes,\n    ]:\n",
        "    refresh_repository(force=False)\n\n    for loader in [\n        load_local_sources,\n        fetch_bizinfo_records,\n        fetch_kstartup,\n        fetch_kosmes,\n    ]:\n",
        "multi source list",
    )

    text = replace_once(
        text,
        '        "기존 내부DB에 K-Startup·중진공 OpenAPI를 결합하고, "\n        "고객정보·상담키워드·지역·업력·제외조건을 근거로 점수를 계산합니다."\n',
        '        "내부 상시정책자금·고용지원금 DB에 기업마당·K-Startup·중진공 API를 "\n        "결합하고, 고객정보·상담키워드·지역·업력·제외조건을 근거로 점수를 계산합니다."\n',
        "caption",
    )

    marker = '    if st.button(\n        "다중소스 AI 매칭 실행",\n'
    status_ui = '''    repository_info = repository_status()
    st.caption(
        f"내부 정책DB {repository_info.get('count', 0)}건 · "
        f"최근 자동확인 {repository_info.get('last_attempt_at') or '미실행'}"
    )
    refresh_col, _ = st.columns([1, 3])
    with refresh_col:
        if st.button(
            "정책DB 지금 최신화",
            key=f"policy_repository_refresh_{customer.name}",
            use_container_width=True,
        ):
            with st.spinner("기업마당 및 내부 정책DB를 최신화하고 있습니다..."):
                refresh_result = refresh_repository(force=True)
            st.info(
                f"{refresh_result.get('message', '')} "
                f"현재 {refresh_result.get('count', 0)}건"
            )
            st.rerun()

'''
    if "정책DB 지금 최신화" not in text:
        text = replace_once(text, marker, status_ui + marker, "refresh UI")

    path.write_text(text, encoding="utf-8", newline="\n")

    path = root / "app.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(text, '"정책자금 매칭": "고객DB 업로드/매칭",', '"정책자금 매칭": "통합 정책자금 매칭",', "menu label")
    text = replace_once(text, 'elif active_tab == "고객DB 업로드/매칭":', 'elif active_tab == "통합 정책자금 매칭":', "menu route")
    text = replace_once(
        text,
        '    st.markdown("### 등록 고객 정책자금 자동매칭")\n',
        '    st.markdown("### 등록 고객 통합 정책자금 AI 매칭")\n',
        "heading",
    )
    text = replace_once(
        text,
        '        if st.button(\n            "선택 고객 정책자금 매칭 실행",\n',
        '        if False and st.button(\n            "선택 고객 정책자금 매칭 실행",\n',
        "hide duplicate button",
    )
    text = replace_once(
        text,
        '    st.divider()\n    with st.expander(\n        "기존 방식: 고객DB 엑셀 파일 직접 업로드",\n',
        '''    st.divider()
    show_legacy_upload = st.checkbox(
        "관리자·레거시 고객DB 업로드 도구 보기",
        value=False,
        key="show_legacy_customer_upload_v660",
    )
    if not show_legacy_upload:
        st.info(
            "일반 매칭은 위 등록 고객 통합매칭을 사용합니다. "
            "고객DB 업로드는 기존 호환과 일괄등록 용도로만 유지됩니다."
        )
        st.stop()

    with st.expander(
        "기존 방식: 고객DB 엑셀 파일 직접 업로드",
''',
        "legacy guard",
    )
    path.write_text(text, encoding="utf-8", newline="\n")

    version_path.write_text(VERSION + "\n", encoding="utf-8")
    changelog_src = root / "payload" / "CHANGELOG_v6.6.0.md"
    if changelog_src.exists():
        shutil.copy2(changelog_src, root / "CHANGELOG_v6.6.0.md")

    for name in ["app.py","multi_source_policy.py","integrated_policy_repository.py"]:
        py_compile.compile(str(root / name), doraise=True)

    print("UPDATE_OK")
    print(f"VERSION={VERSION}")
    print(f"BACKUP={backup}")
    print("SQL_REQUIRED=supabase_v660_upgrade.sql")
    input("Press Enter to close...")


if __name__ == "__main__":
    main()
