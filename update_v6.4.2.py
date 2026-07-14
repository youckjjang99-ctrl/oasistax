from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

VERSION = "v6.4.2"
TARGETS = [
    "matching_preferences.py",
    "customer_history.py",
    "consultation_journal.py",
    "VERSION.txt",
]


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
    if not (root / "consultation_journal.py").exists():
        fail("Run this patch from the OASIS project root folder.")

    version_path = root / "VERSION.txt"
    current = version_path.read_text(encoding="utf-8-sig").strip() if version_path.exists() else ""
    if current and current not in {"v6.4.1", "6.4.1", "v6.4.2", "6.4.2"}:
        fail(f"Expected v6.4.1 but found {current}.")

    backup = root / "_oasis_backups" / (
        "before_v6.4.2_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    backup.mkdir(parents=True, exist_ok=True)
    for name in TARGETS:
        src = root / name
        if src.exists():
            shutil.copy2(src, backup / name)

    path = root / "matching_preferences.py"
    text = path.read_text(encoding="utf-8")
    old = """def get_matching_preferences(
    user_id: str,
    business_no: Any,
) -> dict[str, Any]:
    key = normalize_business_no(business_no)
    data = _load_all(user_id)
    value = data.get(key, {}) if key else {}
    return value if isinstance(value, dict) else {}
"""
    new = """def get_matching_preferences(
    user_id: str,
    business_no: Any,
) -> dict[str, Any]:
    key = normalize_business_no(business_no)
    data = _load_all(user_id)
    value = data.get(key, {}) if key else {}
    if isinstance(value, dict) and value:
        return value

    if key:
        try:
            from cloud_db import CloudDatabase, TABLE_MATCHING_PREFERENCES, cloud_is_configured
            if cloud_is_configured():
                rows = CloudDatabase().select(
                    TABLE_MATCHING_PREFERENCES,
                    filters={"owner_user_id": user_id, "business_no": key},
                    limit=1,
                )
                if rows and isinstance(rows[0], dict):
                    cloud_value = rows[0].get("preference_data", {})
                    if isinstance(cloud_value, str):
                        cloud_value = json.loads(cloud_value)
                    if isinstance(cloud_value, dict) and cloud_value:
                        data[key] = cloud_value
                        _save_all(user_id, data)
                        return cloud_value
        except Exception:
            pass
    return {}
"""
    text = replace_once(text, old, new, "cloud preference restore")
    path.write_text(text, encoding="utf-8", newline="\n")

    path = root / "customer_history.py"
    text = path.read_text(encoding="utf-8")
    insertion = """

def save_customer_event(
    user_id: str,
    business_no: str,
    company_name: str,
    event_id: str,
    event_title: str,
    event_detail: str,
    occurred_at: str = "",
    source: str = "consultation",
) -> dict[str, Any]:
    normalized = _normalize_business_no(business_no)
    if not normalized:
        return {}
    all_history = _load_all(user_id)
    items = all_history.get(normalized, [])
    if not isinstance(items, list):
        items = []
    event_id = str(event_id or "").strip()
    for item in items:
        data = item.get("data", {}) if isinstance(item, dict) else {}
        if isinstance(data, dict) and str(data.get("이벤트ID", "")) == event_id:
            return item
    captured_at = occurred_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    event_data = {
        "히스토리유형": "상담",
        "이벤트ID": event_id,
        "상담제목": event_title,
        "상담내용": event_detail,
    }
    snapshot = {
        "captured_at": captured_at,
        "source": source,
        "company_name": company_name,
        "business_no": normalized,
        "data": event_data,
    }
    items.insert(0, snapshot)
    all_history[normalized] = items[:200]
    _save_all(user_id, all_history)
    if cloud_is_configured():
        try:
            CloudDatabase().insert(
                TABLE_HISTORY,
                [{
                    "owner_user_id": user_id,
                    "business_no": normalized,
                    "company_name": company_name,
                    "source": source,
                    "snapshot_data": event_data,
                    "captured_at": captured_at,
                }],
            )
        except Exception:
            pass
    return snapshot
"""
    marker = "\ndef get_customer_history(\n"
    if "def save_customer_event(" not in text:
        if marker not in text:
            fail("Patch point not found: customer event insertion")
        text = text.replace(marker, insertion + marker, 1)

    old = """def get_customer_history(
    user_id: str,
    business_no: str,
) -> list[dict[str, Any]]:
    all_history = _load_all(user_id)
    return all_history.get(
        _normalize_business_no(business_no),
        [],
    ) or []
"""
    new = """def get_customer_history(
    user_id: str,
    business_no: str,
) -> list[dict[str, Any]]:
    normalized = _normalize_business_no(business_no)
    all_history = _load_all(user_id)
    local_items = all_history.get(normalized, []) or []
    if not isinstance(local_items, list):
        local_items = []
    cloud_items = []
    if normalized and cloud_is_configured():
        try:
            rows = CloudDatabase().select(
                TABLE_HISTORY,
                filters={"owner_user_id": user_id, "business_no": normalized},
                order="captured_at.desc",
                limit=200,
            )
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                data = row.get("snapshot_data", {})
                if isinstance(data, str):
                    data = json.loads(data)
                cloud_items.append({
                    "captured_at": row.get("captured_at", ""),
                    "source": row.get("source", ""),
                    "company_name": row.get("company_name", ""),
                    "business_no": row.get("business_no", normalized),
                    "data": data if isinstance(data, dict) else {},
                })
        except Exception:
            cloud_items = []
    merged = []
    seen = set()
    for item in cloud_items + local_items:
        if not isinstance(item, dict):
            continue
        data = item.get("data", {}) if isinstance(item.get("data"), dict) else {}
        unique_key = str(data.get("이벤트ID") or (
            str(item.get("captured_at", "")) + "|" + str(item.get("source", "")) + "|" +
            json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
        ))
        if unique_key in seen:
            continue
        seen.add(unique_key)
        merged.append(item)
    merged.sort(key=lambda item: str(item.get("captured_at", "")), reverse=True)
    if merged:
        all_history[normalized] = merged[:200]
        _save_all(user_id, all_history)
    return merged[:200]
"""
    text = replace_once(text, old, new, "cloud customer history restore")
    path.write_text(text, encoding="utf-8", newline="\n")

    path = root / "consultation_journal.py"
    text = path.read_text(encoding="utf-8")
    old = """from matching_preferences import (
    INTEREST_OPTIONS,
    get_matching_preferences,
    save_matching_preferences,
)
"""
    new = """from matching_preferences import (
    INTEREST_OPTIONS,
    get_matching_preferences,
    save_matching_preferences,
)
from customer_history import save_customer_event
"""
    text = replace_once(text, old, new, "customer history import")

    old = """    append_timeline_event(
        user_id,
        customer_key,
        history_title,
        _journal_to_timeline_detail(record),
    )
"""
    new = """    history_detail = _journal_to_timeline_detail(record)
    append_timeline_event(user_id, customer_key, history_title, history_detail)
    save_customer_event(
        user_id=user_id,
        business_no=business_no,
        company_name=company_name,
        event_id=str(record.get("journal_id", "")),
        event_title=history_title,
        event_detail=history_detail,
        occurred_at=str(record.get("saved_at", "")),
        source="consultation",
    )
"""
    text = replace_once(text, old, new, "new journal enterprise history")

    old = """                append_timeline_event(
                    user_id,
                    customer_key,
                    f"{saved_at} 상담 · {title}",
                    _journal_to_timeline_detail(record),
                )
                processed.add(journal_id)
                history_added += 1
"""
    new = """                history_title = f"{saved_at} 상담 · {title}"
                history_detail = _journal_to_timeline_detail(record)
                append_timeline_event(user_id, customer_key, history_title, history_detail)
                save_customer_event(
                    user_id=user_id,
                    business_no=business_no,
                    company_name=company_name,
                    event_id=journal_id,
                    event_title=history_title,
                    event_detail=history_detail,
                    occurred_at=str(record.get("saved_at", "")),
                    source="consultation",
                )
                processed.add(journal_id)
                history_added += 1
"""
    text = replace_once(text, old, new, "relinked enterprise history")

    old = """    if business_no:
        journals = [
            item for item in journals
            if str(item.get("business_no", "")) == str(business_no)
        ]
"""
    new = """    if business_no:
        target_business_no = normalize_business_no(business_no)
        journals = [
            item for item in journals
            if normalize_business_no(item.get("business_no", "")) == target_business_no
        ]
"""
    text = replace_once(text, old, new, "normalized journal filter")
    path.write_text(text, encoding="utf-8", newline="\n")

    version_path.write_text(VERSION + "\n", encoding="utf-8")
    changelog_src = root / "payload" / "CHANGELOG_v6.4.2.md"
    if changelog_src.exists():
        shutil.copy2(changelog_src, root / "CHANGELOG_v6.4.2.md")

    import py_compile
    for name in ["matching_preferences.py", "customer_history.py", "consultation_journal.py"]:
        py_compile.compile(str(root / name), doraise=True)

    print("UPDATE_OK")
    print(f"VERSION={VERSION}")
    print(f"BACKUP={backup}")
    print("CHECK=Open CRM journal view and run full relink once.")
    input("Press Enter to close...")


if __name__ == "__main__":
    main()
