from __future__ import annotations

import py_compile
import shutil
from datetime import datetime
from pathlib import Path

VERSION = "v6.6.2"
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
        "v6.6.1", "6.6.1", "v6.6.2", "6.6.2"
    }:
        fail(f"Expected v6.6.1 but found {current}.")

    backup = root / "_oasis_backups" / (
        "before_v6.6.2_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    backup.mkdir(parents=True, exist_ok=True)
    for name in TARGETS:
        src = root / name
        if src.exists():
            shutil.copy2(src, backup / name)

    text = target.read_text(encoding="utf-8")

    # Add standard-library XML parser.
    if "import xml.etree.ElementTree as ET" not in text:
        text = replace_once(
            text,
            "import requests\nimport streamlit as st\n",
            "import requests\nimport streamlit as st\nimport xml.etree.ElementTree as ET\n",
            "ElementTree import",
        )

    old_xml = '''            except Exception:
                raw_items = pd.read_xml(response.text).to_dict("records")
'''
    new_xml = '''            except Exception:
                # v6.6.2: Streamlit Cloud에서 lxml 없이 XML 응답 처리
                root = ET.fromstring(response.content)
                raw_items = []
                candidate_nodes = list(root.findall(".//item"))
                if not candidate_nodes:
                    candidate_nodes = [
                        node
                        for node in root.iter()
                        if list(node)
                        and any(
                            child.text and str(child.text).strip()
                            for child in list(node)
                        )
                    ]
                for node in candidate_nodes:
                    record = {}
                    for child in list(node):
                        tag = str(child.tag).split("}")[-1]
                        value = "".join(child.itertext()).strip()
                        if tag and value:
                            record[tag] = value
                    if record:
                        raw_items.append(record)
'''
    text = replace_once(text, old_xml, new_xml, "XML parser replacement")

    helpers = '''

_EMPLOYMENT_TERMS = {
    "고용", "채용", "장려금", "일자리", "근로자", "청년일자리",
    "고용유지", "고용촉진", "육아휴직", "출산육아", "중장년",
    "고령자", "청년채용", "인건비", "인턴", "근무환경",
}

_POLICY_FUND_TERMS = {
    "정책자금", "융자", "운전자금", "시설자금", "보증", "기술보증",
    "신용보증", "대출", "자금", "이차보전", "특례보증", "보증료",
}


def classify_support_result(result: dict[str, Any]) -> str:
    source_type = _clean(result.get("source_type", "")).lower()
    source_text = " ".join(result.get("source_list", []) or [])
    combined = _normalize_text(
        " ".join(
            _clean(result.get(field))
            for field in [
                "title", "summary", "target", "keywords",
                "agency", "source", "source_type", "source_name",
            ]
        )
        + " "
        + source_text
    )

    if source_type == "employment_support":
        return "고용지원금"
    if source_type == "permanent_fund":
        return "정책자금·보증"

    employment_hits = sum(
        1 for term in _EMPLOYMENT_TERMS if term in combined
    )
    policy_hits = sum(
        1 for term in _POLICY_FUND_TERMS if term in combined
    )

    if employment_hits >= 2 and employment_hits > policy_hits:
        return "고용지원금"
    if policy_hits >= 1 and policy_hits >= employment_hits:
        return "정책자금·보증"
    return "공고형 지원사업"


def _result_table_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for result in results:
        rows.append(
            {
                "점수": result["score"],
                "등급": result["grade"],
                "분류": classify_support_result(result),
                "공고명": result["title"],
                "기관": result["agency"],
                "소스": ", ".join(result.get("source_list", [])),
                "지원지역": result["region"],
                "신청종료": result["end_date"],
                "추천근거": " / ".join(result["evidence"][:3]),
                "감점사유": " / ".join(result["penalties"][:2]),
            }
        )
    return rows


def _render_result_group(
    results: list[dict[str, Any]],
    empty_message: str,
    key_prefix: str,
) -> None:
    if not results:
        st.info(empty_message)
        return

    st.dataframe(
        pd.DataFrame(_result_table_rows(results)),
        hide_index=True,
        use_container_width=True,
    )

    for index, result in enumerate(results[:10], start=1):
        with st.expander(
            f"{index}. {result['title']} · {result['score']}점",
            expanded=index <= 3,
        ):
            st.write(
                f"**분류:** {classify_support_result(result)}"
            )
            st.write(f"**기관:** {result['agency'] or '-'}")
            st.write(
                "**소스:** "
                + ", ".join(result.get("source_list", []))
            )
            st.write(f"**지원대상:** {result['target'] or '-'}")
            st.write(f"**지원지역:** {result['region'] or '-'}")
            st.write(
                f"**신청기간:** {result['start_date'] or '-'} "
                f"~ {result['end_date'] or '-'}"
            )
            if result["summary"]:
                st.write(result["summary"])
            st.markdown("**추천 근거**")
            for item in result["evidence"]:
                st.write(f"- {item}")
            if result["penalties"]:
                st.markdown("**확인 필요**")
                for item in result["penalties"]:
                    st.write(f"- {item}")
            if result["url"]:
                st.link_button(
                    "공고 원문 열기",
                    result["url"],
                    use_container_width=True,
                )
'''

    marker = "\ndef render_multi_source_match(\n"
    if "def classify_support_result(" not in text:
        if marker not in text:
            fail("Patch point not found: result helpers insertion")
        text = text.replace(marker, helpers + marker, 1)

    old_results = '''    table_rows = []
    for result in visible:
        table_rows.append(
            {
                "점수": result["score"],
                "등급": result["grade"],
                "공고명": result["title"],
                "기관": result["agency"],
                "소스": ", ".join(result.get("source_list", [])),
                "지원지역": result["region"],
                "신청종료": result["end_date"],
                "추천근거": " / ".join(result["evidence"][:3]),
                "감점사유": " / ".join(result["penalties"][:2]),
            }
        )

    st.markdown("##### 추천 결과")
    st.dataframe(
        pd.DataFrame(table_rows),
        hide_index=True,
        use_container_width=True,
    )

    for index, result in enumerate(visible[:10], start=1):
        with st.expander(
            f"{index}. {result['title']} · {result['score']}점",
            expanded=index <= 3,
        ):
            st.write(f"**기관:** {result['agency'] or '-'}")
            st.write(
                "**소스:** "
                + ", ".join(result.get("source_list", []))
            )
            st.write(f"**지원대상:** {result['target'] or '-'}")
            st.write(f"**지원지역:** {result['region'] or '-'}")
            st.write(f"**신청기간:** {result['start_date'] or '-'} ~ {result['end_date'] or '-'}")
            if result["summary"]:
                st.write(result["summary"])
            st.markdown("**추천 근거**")
            for item in result["evidence"]:
                st.write(f"- {item}")
            if result["penalties"]:
                st.markdown("**확인 필요**")
                for item in result["penalties"]:
                    st.write(f"- {item}")
            if result["url"]:
                st.link_button(
                    "공고 원문 열기",
                    result["url"],
                    use_container_width=True,
                )
'''
    new_results = '''    categorized = {
        "정책자금·보증": [],
        "고용지원금": [],
        "공고형 지원사업": [],
    }
    for result in visible:
        categorized[classify_support_result(result)].append(result)

    tab_all, tab_fund, tab_employment, tab_notice = st.tabs(
        [
            f"전체 추천 {len(visible)}건",
            f"정책자금·보증 {len(categorized['정책자금·보증'])}건",
            f"고용지원금 {len(categorized['고용지원금'])}건",
            f"공고형 지원사업 {len(categorized['공고형 지원사업'])}건",
        ]
    )

    with tab_all:
        _render_result_group(
            visible,
            "설정한 점수 이상의 추천 결과가 없습니다.",
            "all",
        )
    with tab_fund:
        _render_result_group(
            categorized["정책자금·보증"],
            "설정한 점수 이상의 정책자금·보증 추천이 없습니다.",
            "fund",
        )
    with tab_employment:
        _render_result_group(
            categorized["고용지원금"],
            "설정한 점수 이상의 고용지원금 추천이 없습니다.",
            "employment",
        )
    with tab_notice:
        _render_result_group(
            categorized["공고형 지원사업"],
            "설정한 점수 이상의 공고형 지원사업 추천이 없습니다.",
            "notice",
        )
'''
    text = replace_once(text, old_results, new_results, "result category tabs")

    target.write_text(text, encoding="utf-8", newline="\n")
    version_path.write_text(VERSION + "\n", encoding="utf-8")

    changelog_src = root / "payload" / "CHANGELOG_v6.6.2.md"
    if changelog_src.exists():
        shutil.copy2(changelog_src, root / "CHANGELOG_v6.6.2.md")

    py_compile.compile(str(target), doraise=True)

    print("UPDATE_OK")
    print(f"VERSION={VERSION}")
    print(f"BACKUP={backup}")
    print("SQL_REQUIRED=NO")
    print("RESULT=XML parser and categorized result tabs enabled.")
    input("Press Enter to close...")


if __name__ == "__main__":
    main()
