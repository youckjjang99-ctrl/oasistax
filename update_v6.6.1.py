from __future__ import annotations

import py_compile
import shutil
from datetime import datetime
from pathlib import Path

VERSION = "v6.6.1"
TARGETS = ["multi_source_policy.py", "VERSION.txt"]


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
    target = root / "multi_source_policy.py"
    if not target.exists():
        fail("Run this patch from the OASIS project root folder.")

    version_path = root / "VERSION.txt"
    current = (
        version_path.read_text(encoding="utf-8-sig").strip()
        if version_path.exists()
        else ""
    )
    if current and current not in {
        "v6.6.0", "6.6.0", "v6.6.1", "6.6.1"
    }:
        fail(f"Expected v6.6.0 but found {current}.")

    backup = root / "_oasis_backups" / (
        "before_v6.6.1_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    backup.mkdir(parents=True, exist_ok=True)
    for name in TARGETS:
        src = root / name
        if src.exists():
            shutil.copy2(src, backup / name)

    text = target.read_text(encoding="utf-8")

    old_local = '''        normalized = normalize_record(enriched, f"{source_name}:{source_type}")
        if normalized:
            normalized["repository_id"] = row.get("record_id")
            records.append(normalized)
'''
    new_local = '''        normalized = normalize_record(enriched, f"{source_name}:{source_type}")
        if normalized:
            normalized["repository_id"] = row.get("record_id")
            normalized["source"] = (
                normalized.get("source")
                or source_name
                or source_type
                or "내부 통합 정책DB"
            )
            normalized["source_name"] = source_name
            normalized["source_type"] = source_type
            records.append(normalized)
'''
    text = replace_once(text, old_local, new_local, "internal source normalization")

    adapter = '''

def load_bizinfo_source() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    # 기업마당 저장소 형식을 다중소스 매칭 공통 형식으로 변환
    repository_rows, status = fetch_bizinfo_records()
    records: list[dict[str, Any]] = []

    for row in repository_rows:
        if not isinstance(row, dict):
            continue

        raw = row.get("raw_data", {})
        if not isinstance(raw, dict):
            raw = {}

        enriched = dict(raw)
        title = _clean(row.get("title", ""))
        agency = _clean(row.get("agency", ""))

        if title:
            enriched.setdefault("사업명", title)
            enriched.setdefault("공고명", title)
        if agency:
            enriched.setdefault("기관명", agency)

        normalized = normalize_record(enriched, "기업마당 API")
        if not normalized:
            continue

        normalized["repository_id"] = row.get("record_id")
        normalized["source"] = "기업마당 API"
        normalized["source_name"] = "기업마당 API"
        normalized["source_type"] = "bizinfo"
        records.append(normalized)

    result_status = dict(status or {})
    result_status["source"] = "기업마당 API"
    result_status["count"] = len(records)
    if result_status.get("status") == "정상" and repository_rows and not records:
        result_status["status"] = "형식오류"
        result_status["message"] = (
            "기업마당 응답은 받았지만 매칭 공고 형식으로 변환하지 못했습니다."
        )
    return records, result_status
'''

    marker = "\ndef _deduplicate(records: list[dict[str, Any]]) -> list[dict[str, Any]]:\n"
    if "def load_bizinfo_source(" not in text:
        if marker not in text:
            fail("Patch point not found: Bizinfo adapter insertion")
        text = text.replace(marker, adapter + marker, 1)

    old_dedupe = '''        existing = groups.get(key)
        if existing is None:
            record["source_list"] = [record["source"]]
            groups[key] = record
            continue

        if record["source"] not in existing["source_list"]:
            existing["source_list"].append(record["source"])
'''
    new_dedupe = '''        source = _clean(
            record.get("source")
            or record.get("source_name")
            or record.get("source_type")
            or "출처 미확인"
        )
        record["source"] = source

        existing = groups.get(key)
        if existing is None:
            record["source_list"] = [source]
            groups[key] = record
            continue

        existing_sources = existing.get("source_list", [])
        if not isinstance(existing_sources, list):
            existing_sources = _list_value(existing_sources)
            existing["source_list"] = existing_sources

        if source not in existing_sources:
            existing_sources.append(source)
'''
    text = replace_once(text, old_dedupe, new_dedupe, "safe source deduplication")

    old_loader = '''        load_local_sources,
        fetch_bizinfo_records,
        fetch_kstartup,
'''
    new_loader = '''        load_local_sources,
        load_bizinfo_source,
        fetch_kstartup,
'''
    text = replace_once(text, old_loader, new_loader, "Bizinfo adapter loader")

    target.write_text(text, encoding="utf-8", newline="\n")
    version_path.write_text(VERSION + "\n", encoding="utf-8")

    changelog_src = root / "payload" / "CHANGELOG_v6.6.1.md"
    if changelog_src.exists():
        shutil.copy2(changelog_src, root / "CHANGELOG_v6.6.1.md")

    py_compile.compile(str(target), doraise=True)

    print("UPDATE_OK")
    print(f"VERSION={VERSION}")
    print(f"BACKUP={backup}")
    print("SQL_REQUIRED=NO")
    print("RESULT=Source normalization and safe deduplication enabled.")
    input("Press Enter to close...")


if __name__ == "__main__":
    main()
