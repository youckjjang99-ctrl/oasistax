from __future__ import annotations

import py_compile
import shutil
import sys
from datetime import datetime
from pathlib import Path

TARGET_VERSION = "v8.2.1"
SUPPORTED_VERSIONS = {
    "v8.2.0", "8.2.0",
    "v8.2.1", "8.2.1",
}

TARGET_FILES = [
    "multi_source_policy.py",
    "VERSION.txt",
]

ALIAS_OLD = '    "title": [\n        "공고명", "사업명", "지원사업명", "사업공고명", "통합공고사업명",\n        "제도명", "상품명", "사업 제목", "title", "biz_pbanc_nm",\n    ],\n'
ALIAS_NEW = '    "title": [\n        "공고명", "사업명", "지원사업명", "사업공고명", "통합공고사업명",\n        "제도명", "상품명", "사업 제목", "title", "biz_pbanc_nm",\n        "pblancNm", "pblanc_nm",\n    ],\n'
LOADING_OLD = '        enriched = dict(raw)\n        if not enriched.get("기관명") and enriched.get("기관"):\n            enriched["기관명"] = enriched.get("기관")\n        if not enriched.get("사업명") and enriched.get("상품명"):\n            enriched["사업명"] = enriched.get("상품명")\n        if not enriched.get("사업명") and enriched.get("제도명"):\n            enriched["사업명"] = enriched.get("제도명")\n'
LOADING_NEW = '        enriched = dict(raw)\n\n        # Supabase 저장소의 정규화 필드를 raw_data에 다시 주입한다.\n        # 기업마당 원본 필드명이 pblancNm 등으로 달라도\n        # 제목·기관 누락으로 공고가 탈락하지 않도록 한다.\n        enriched.setdefault(\n            "사업명",\n            str(row.get("title", "") or ""),\n        )\n        enriched.setdefault(\n            "공고명",\n            str(row.get("title", "") or ""),\n        )\n        enriched.setdefault(\n            "기관명",\n            str(row.get("agency", "") or ""),\n        )\n\n        if not enriched.get("기관명") and enriched.get("기관"):\n            enriched["기관명"] = enriched.get("기관")\n        if not enriched.get("사업명") and enriched.get("상품명"):\n            enriched["사업명"] = enriched.get("상품명")\n        if not enriched.get("사업명") and enriched.get("제도명"):\n            enriched["사업명"] = enriched.get("제도명")\n'
STATUS_OLD = '    return records, {\n        "source": "내부 통합 정책DB",\n        "status": "정상" if records else "자료없음",\n        "message": status.get("message", ""),\n        "count": len(records),\n    }\n'
STATUS_NEW = '    repository_count = len(repository_rows)\n    converted_count = len(records)\n    dropped_count = max(\n        repository_count - converted_count,\n        0,\n    )\n\n    return records, {\n        "source": "내부 통합 정책DB",\n        "status": "정상" if records else "자료없음",\n        "message": (\n            f"{status.get(\'message\', \'\')} · "\n            f"변환 {converted_count:,}건"\n            + (\n                f" · 제외 {dropped_count:,}건"\n                if dropped_count\n                else ""\n            )\n        ),\n        "count": converted_count,\n    }\n'


def fail(message: str) -> None:
    print("UPDATE_FAILED")
    print(message)
    input("Press Enter to close...")
    raise SystemExit(1)


def replace_once(
    text: str,
    old: str,
    new: str,
    label: str,
) -> str:
    if old not in text:
        raise RuntimeError(
            f"수정 위치를 찾지 못했습니다: {label}"
        )
    return text.replace(old, new, 1)


def create_backup(root: Path) -> Path:
    backup = (
        root
        / "_oasis_backups"
        / (
            "before_v8.2.1_"
            + datetime.now().strftime("%Y%m%d_%H%M%S")
        )
    )
    backup.mkdir(
        parents=True,
        exist_ok=False,
    )

    for relative in TARGET_FILES:
        source = root / relative
        if not source.exists():
            continue

        destination = backup / relative
        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        shutil.copy2(source, destination)

    return backup


def rollback(root: Path, backup: Path) -> None:
    for relative in TARGET_FILES:
        source = backup / relative
        destination = root / relative

        if source.exists():
            destination.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            shutil.copy2(source, destination)


def patch_multi_source_policy(text: str) -> str:
    if '"pblancNm", "pblanc_nm"' not in text:
        text = replace_once(
            text,
            ALIAS_OLD,
            ALIAS_NEW,
            "기업마당 공고명 별칭",
        )

    if (
        "Supabase 저장소의 정규화 필드를 raw_data에 다시 주입한다."
        not in text
    ):
        text = replace_once(
            text,
            LOADING_OLD,
            LOADING_NEW,
            "공고형 정책자금 상위필드 주입",
        )

    if "repository_count = len(repository_rows)" not in text:
        text = replace_once(
            text,
            STATUS_OLD,
            STATUS_NEW,
            "정책DB 변환건수 진단표시",
        )

    return text


def main() -> None:
    root = Path.cwd()

    if not (root / "app.py").exists():
        fail(
            "app.py가 있는 OASIS 프로젝트 폴더에서 실행해주세요."
        )

    target = root / "multi_source_policy.py"
    if not target.exists():
        fail(
            "multi_source_policy.py를 찾지 못했습니다."
        )

    version_path = root / "VERSION.txt"
    current_version = (
        version_path.read_text(
            encoding="utf-8-sig"
        ).strip()
        if version_path.exists()
        else ""
    )

    if (
        current_version
        and current_version not in SUPPORTED_VERSIONS
    ):
        fail(
            f"현재 버전 {current_version}에서는 "
            f"{TARGET_VERSION} 업데이트를 적용할 수 없습니다."
        )

    backup = create_backup(root)

    try:
        source = target.read_text(encoding="utf-8")
        updated = patch_multi_source_policy(source)

        target.write_text(
            updated,
            encoding="utf-8",
            newline="\n",
        )

        version_path.write_text(
            TARGET_VERSION + "\n",
            encoding="utf-8",
        )

        changelog = root / "payload" / "CHANGELOG.md"
        if changelog.exists():
            shutil.copy2(
                changelog,
                root / "CHANGELOG.md",
            )

        py_compile.compile(
            str(target),
            doraise=True,
        )

        if (root / "system_precheck.py").exists():
            sys.path.insert(0, str(root))
            from system_precheck import run_precheck

            report = run_precheck(
                root,
                save_report=True,
            )

            if report.get("status") != "PASS":
                errors = [
                    item
                    for item in report.get("checks", [])
                    if not item.get("ok")
                    and item.get("level") == "error"
                ]
                summary = "; ".join(
                    f"{item.get('item')}: {item.get('message')}"
                    for item in errors[:8]
                )
                raise RuntimeError(
                    "사전점검 실패: " + summary
                )

    except Exception as exc:
        rollback(root, backup)
        print("UPDATE_ROLLED_BACK")
        print(f"BACKUP={backup}")
        fail(
            f"{type(exc).__name__}: {exc}"
        )

    print("UPDATE_OK")
    print(f"VERSION={TARGET_VERSION}")
    print(f"BACKUP={backup}")
    print("BIZINFO_TITLE_ALIASES=FIXED")
    print("REPOSITORY_FIELDS_INJECTED=YES")
    print("CONVERSION_DIAGNOSTICS=ENABLED")
    print("PRECHECK=PASS")
    print("SQL_REQUIRED=NO")
    input("Press Enter to close...")


if __name__ == "__main__":
    main()
