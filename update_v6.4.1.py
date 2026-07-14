
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

VERSION = "v6.4.1"
TARGETS = ["consultation_journal.py", "enterprise_center.py", "VERSION.txt"]


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
    if current and current not in {"v6.4.0", "6.4.0", "v6.4.1", "6.4.1"}:
        fail(f"Expected v6.4.0 but found {current}.")

    backup = root / "_oasis_backups" / (
        "before_v6.4.1_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    backup.mkdir(parents=True, exist_ok=True)
    for name in TARGETS:
        src = root / name
        if src.exists():
            shutil.copy2(src, backup / name)

    path = root / "consultation_journal.py"
    text = path.read_text(encoding="utf-8")

    import_old = '''from crm import (
    append_timeline_event,
    get_customer_record,
    upsert_customer_record,
)
'''
    import_new = '''from crm import (
    append_timeline_event,
    get_customer_record,
    make_customer_key,
    upsert_customer_record,
)
'''
    text = replace_once(text, import_old, import_new, "make_customer_key import")

    insertion = '''
def _journal_relink_state_path(user_id: str) -> Path:
    return get_user_dirs(user_id)["base"] / "consultation_relink_state.json"


def _load_journal_relink_state(user_id: str) -> set[str]:
    path = _journal_relink_state_path(user_id)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(item) for item in data if str(item).strip()} if isinstance(data, list) else set()
    except Exception:
        return set()


def _save_journal_relink_state(user_id: str, journal_ids: set[str]) -> None:
    path = _journal_relink_state_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(sorted(journal_ids), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def relink_saved_consultation_journals(
    user_id: str,
    customer_key: str,
    business_no: str,
    company_name: str,
) -> dict[str, Any]:
    journals = _load_journals(user_id)
    normalized = normalize_business_no(business_no)
    matched: list[dict[str, Any]] = []

    for item in journals:
        if not isinstance(item, dict):
            continue
        item_no = normalize_business_no(item.get("business_no", ""))
        item_company = str(item.get("company_name", "") or "").strip()
        if normalized and item_no == normalized:
            matched.append(item)
        elif not normalized and company_name and item_company == company_name.strip():
            matched.append(item)

    matched = sorted(matched, key=lambda row: str(row.get("saved_at", "")))
    processed = _load_journal_relink_state(user_id)
    keyword_added: list[str] = []
    interest_added: list[str] = []
    history_added = 0
    errors: list[str] = []

    for record in matched:
        journal_id = str(record.get("journal_id", "") or "").strip()
        if not journal_id:
            journal_id = hashlib.sha256(
                json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()

        try:
            result = merge_policy_matching_preferences(
                user_id,
                business_no,
                company_name,
                record,
            )
            keyword_added.extend(result.get("added_keywords", []) or [])
            interest_added.extend(result.get("added_interests", []) or [])
        except Exception as exc:
            errors.append(f"키워드 반영 실패({journal_id}): {exc}")

        if journal_id not in processed:
            try:
                title = str(record.get("consultation_title", "") or "녹음 상담일지")
                saved_at = str(record.get("saved_at", "") or "")[:10]
                append_timeline_event(
                    user_id,
                    customer_key,
                    f"{saved_at} 상담 · {title}",
                    _journal_to_timeline_detail(record),
                )
                processed.add(journal_id)
                history_added += 1
            except Exception as exc:
                errors.append(f"히스토리 반영 실패({journal_id}): {exc}")

    _save_journal_relink_state(user_id, processed)

    return {
        "journal_count": len(matched),
        "keyword_count": len({str(item) for item in keyword_added}),
        "interest_count": len({str(item) for item in interest_added}),
        "history_count": history_added,
        "errors": errors,
    }


'''
    marker = "def get_company_consultation_context(\n"
    if "def relink_saved_consultation_journals(" not in text:
        if marker not in text:
            fail("Patch point not found: relink insertion")
        text = text.replace(marker, insertion + marker, 1)

    old = '    st.caption(f"조회 결과 {len(journals)}건")\n    for index, item in enumerate(journals[:100]):\n'
    new = '''    st.caption(f"조회 결과 {len(journals)}건")

    if business_no or company_name:
        if st.button(
            "기존 상담일지 전체 재연동",
            use_container_width=True,
            key=f"relink_saved_journals_{business_no or company_name}",
        ):
            customer_key = make_customer_key(company_name, business_no)
            result = relink_saved_consultation_journals(
                user_id=user_id,
                customer_key=customer_key,
                business_no=business_no,
                company_name=company_name,
            )
            message = (
                f"상담일지 {result['journal_count']}건 확인 · "
                f"매칭키워드 {result['keyword_count']}개 추가 · "
                f"관심분야 {result['interest_count']}개 추가 · "
                f"기업히스토리 {result['history_count']}건 복구"
            )
            st.session_state[
                f"journal_relink_notice_{business_no or company_name}"
            ] = message
            st.session_state[
                f"journal_relink_errors_{business_no or company_name}"
            ] = result.get("errors", [])
            st.rerun()

    notice_key = f"journal_relink_notice_{business_no or company_name}"
    error_key = f"journal_relink_errors_{business_no or company_name}"
    if st.session_state.get(notice_key):
        st.success(st.session_state.pop(notice_key))
        for error in st.session_state.pop(error_key, [])[:5]:
            st.caption(error)

    for index, item in enumerate(journals[:100]):
'''
    text = replace_once(text, old, new, "relink button")

    path.write_text(text, encoding="utf-8", newline="\n")

    path = root / "enterprise_center.py"
    text = path.read_text(encoding="utf-8")
    old = '''            render_saved_consultation_journals(
                user_id=user_id,
                business_no=business_no,
                company_name=company_name,
            )
'''
    new = '''            render_saved_consultation_journals(
                user_id=user_id,
                business_no=business_no,
                company_name=company_name,
            )
            st.caption(
                "재연동 후 정책자금 탭과 정책자금매칭 메뉴를 다시 열면 "
                "최신 키워드와 추천 결과가 반영됩니다."
            )
'''
    text = replace_once(text, old, new, "enterprise caption")
    path.write_text(text, encoding="utf-8", newline="\n")

    version_path.write_text(VERSION + "\n", encoding="utf-8")
    changelog_src = root / "payload" / "CHANGELOG_v6.4.1.md"
    if changelog_src.exists():
        shutil.copy2(changelog_src, root / "CHANGELOG_v6.4.1.md")

    import py_compile
    for name in ["consultation_journal.py", "enterprise_center.py"]:
        py_compile.compile(str(root / name), doraise=True)

    print("UPDATE_OK")
    print(f"VERSION={VERSION}")
    print(f"BACKUP={backup}")
    input("Press Enter to close...")


if __name__ == "__main__":
    main()
