from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl import load_workbook


def normalize_business_no(value: Any) -> str:
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"
    return str(value or "").strip()


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).lower()


def load_registered_customers(cumulative_path: Path) -> pd.DataFrame:
    if not cumulative_path.exists():
        return pd.DataFrame()

    try:
        df = pd.read_excel(cumulative_path, sheet_name="고객DB")
    except Exception:
        return pd.DataFrame()

    if df.empty:
        return df

    df = df.dropna(how="all").copy()
    df.columns = [str(column).strip() for column in df.columns]
    return df


def build_customer_labels(df: pd.DataFrame) -> tuple[list[str], dict[str, int]]:
    labels: list[str] = []
    row_map: dict[str, int] = {}
    used: dict[str, int] = {}

    for index, row in df.iterrows():
        company = str(row.get("업체명", "") or "").strip()
        representative = str(row.get("대표자명", "") or "").strip()
        business_no = normalize_business_no(
            row.get("사업자등록번호", "")
        )

        if not company:
            continue

        label_parts = [company]
        if business_no:
            label_parts.append(business_no)
        if representative:
            label_parts.append(representative)

        base_label = " · ".join(label_parts)
        used[base_label] = used.get(base_label, 0) + 1
        label = (
            base_label
            if used[base_label] == 1
            else f"{base_label} · {used[base_label]}"
        )

        labels.append(label)
        row_map[label] = int(index)

    return labels, row_map


def customer_preview(row: pd.Series) -> pd.DataFrame:
    fields = [
        "업체명",
        "대표자명",
        "사업자등록번호",
        "업종명",
        "사업장 소재지",
        "설립일",
        "종업원수",
        "상시근로자수",
        "매출액",
        "연매출",
        "영업이익",
        "당기순이익",
        "벤처",
        "이노비즈",
        "메인비즈",
        "기업부설연구소",
        "연구개발전담부서",
        "특허보유",
    ]

    rows = []
    for field in fields:
        if field not in row.index:
            continue

        value = row.get(field)
        if value is None or str(value).strip().lower() in {
            "",
            "nan",
            "none",
            "nat",
        }:
            continue

        if field in {
            "매출액",
            "연매출",
            "영업이익",
            "당기순이익",
        }:
            try:
                value = f"{int(float(str(value).replace(',', ''))):,}"
            except Exception:
                pass

        rows.append({"항목": field, "값": value})

    return pd.DataFrame(rows)


def _find_header_row_and_columns(worksheet):
    for row_number in range(1, min(worksheet.max_row, 40) + 1):
        values = {
            str(worksheet.cell(row_number, column).value or "").strip(): column
            for column in range(1, worksheet.max_column + 1)
        }
        if "업체명" in values:
            return row_number, values
    raise ValueError("고객DB 시트에서 '업체명' 헤더를 찾지 못했습니다.")


def _append_text(current: Any, addition: str) -> str:
    current_text = str(current or "").strip()
    addition = str(addition or "").strip()

    if not addition:
        return current_text
    if not current_text:
        return addition
    if addition in current_text:
        return current_text
    return f"{current_text} / {addition}"


def _ensure_column(worksheet, header_row: int, columns: dict[str, int], name: str) -> int:
    if name in columns:
        return columns[name]

    column_number = worksheet.max_column + 1
    worksheet.cell(header_row, column_number).value = name
    columns[name] = column_number
    return column_number


def create_single_customer_workbook(
    cumulative_path: Path,
    destination_dir: Path,
    selected_row: pd.Series,
    manager_name: str = "",
    matching_preferences: dict[str, Any] | None = None,
) -> Path:
    """
    기존 누적 고객DB를 복사한 뒤 고객DB 시트만 선택 고객 1행으로 필터링한다.
    원본 파일과 기존 고객리스트는 수정하지 않는다.
    다른 정책자금·고용지원금 시트와 서식은 그대로 유지한다.
    """
    if not cumulative_path.exists():
        raise FileNotFoundError("누적 고객DB를 찾지 못했습니다.")

    destination_dir.mkdir(parents=True, exist_ok=True)

    company_name = str(selected_row.get("업체명", "고객") or "고객").strip()
    safe_company = re.sub(r'[\\/:*?"<>|]', "_", company_name)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = destination_dir / (
        f"등록고객매칭_{safe_company}_{timestamp}.xlsx"
    )

    shutil.copy2(cumulative_path, destination)

    workbook = load_workbook(destination)
    if "고객DB" not in workbook.sheetnames:
        raise ValueError("누적 고객DB에 '고객DB' 시트가 없습니다.")

    worksheet = workbook["고객DB"]
    header_row, columns = _find_header_row_and_columns(worksheet)

    target_business_no = normalize_business_no(
        selected_row.get("사업자등록번호", "")
    )
    target_company = normalize_text(selected_row.get("업체명", ""))
    target_representative = normalize_text(
        selected_row.get("대표자명", "")
    )

    company_column = columns["업체명"]
    business_column = columns.get("사업자등록번호")
    representative_column = columns.get("대표자명")
    manager_column = columns.get("담당자")

    matching_rows: list[int] = []

    for row_number in range(header_row + 1, worksheet.max_row + 1):
        company = normalize_text(
            worksheet.cell(row_number, company_column).value
        )
        business_no = (
            normalize_business_no(
                worksheet.cell(row_number, business_column).value
            )
            if business_column
            else ""
        )
        representative = (
            normalize_text(
                worksheet.cell(row_number, representative_column).value
            )
            if representative_column
            else ""
        )

        is_match = False
        if (
            target_business_no
            and len(target_business_no.replace("-", "")) == 10
            and business_no == target_business_no
        ):
            is_match = True
        elif company == target_company:
            if target_representative and representative_column:
                is_match = representative == target_representative
            else:
                is_match = True

        if is_match:
            matching_rows.append(row_number)

    if not matching_rows:
        raise ValueError("누적 고객DB에서 선택 고객 행을 찾지 못했습니다.")

    keep_row = matching_rows[0]

    # 선택 고객 외 데이터행 삭제. 제목·헤더·서식·다른 시트는 유지.
    for row_number in range(worksheet.max_row, header_row, -1):
        if row_number != keep_row:
            worksheet.delete_rows(row_number, 1)

    # 삭제 후 선택 고객은 header_row + 1 위치가 된다.
    if manager_column and manager_name.strip():
        worksheet.cell(header_row + 1, manager_column).value = (
            manager_name.strip()
        )

    preferences = dict(matching_preferences or {})
    if preferences:
        keyword_column = _ensure_column(
            worksheet,
            header_row,
            columns,
            "키워드메모",
        )
        memo_column = _ensure_column(
            worksheet,
            header_row,
            columns,
            "비고",
        )
        topic_columns = [
            _ensure_column(
                worksheet,
                header_row,
                columns,
                f"희망상담주제{index}",
            )
            for index in range(1, 4)
        ]
        purpose_columns = [
            _ensure_column(
                worksheet,
                header_row,
                columns,
                f"희망자금용도{index}",
            )
            for index in range(1, 4)
        ]

        matching_keywords = preferences.get("매칭키워드", []) or []
        interest_fields = preferences.get("관심지원분야", []) or []
        exclusion_keywords = preferences.get("제외키워드", []) or []
        fund_purpose = str(
            preferences.get("자금사용목적", "") or ""
        ).strip()

        keyword_text = ", ".join(
            [str(item).strip() for item in matching_keywords + interest_fields if str(item).strip()]
        )
        if keyword_text:
            current = worksheet.cell(
                header_row + 1,
                keyword_column,
            ).value
            worksheet.cell(
                header_row + 1,
                keyword_column,
            ).value = _append_text(current, keyword_text)

        for column_number, value in zip(
            topic_columns,
            list(interest_fields)[:3],
        ):
            worksheet.cell(header_row + 1, column_number).value = value

        purpose_values = []
        if fund_purpose:
            purpose_values.append(fund_purpose)
        purpose_values.extend(
            [
                str(item).strip()
                for item in interest_fields
                if str(item).strip()
            ]
        )

        for column_number, value in zip(
            purpose_columns,
            purpose_values[:3],
        ):
            worksheet.cell(header_row + 1, column_number).value = value

        memo_parts = []
        if exclusion_keywords:
            memo_parts.append(
                "제외키워드: "
                + ", ".join(
                    str(item).strip()
                    for item in exclusion_keywords
                    if str(item).strip()
                )
            )

        planned_amount = str(
            preferences.get("투자예정금액", "") or ""
        ).strip()
        planned_timing = str(
            preferences.get("투자예정시기", "") or ""
        ).strip()

        if planned_amount:
            memo_parts.append(f"투자예정금액: {planned_amount}")
        if planned_timing:
            memo_parts.append(f"투자예정시기: {planned_timing}")

        if memo_parts:
            current = worksheet.cell(
                header_row + 1,
                memo_column,
            ).value
            worksheet.cell(
                header_row + 1,
                memo_column,
            ).value = _append_text(
                current,
                " / ".join(memo_parts),
            )

    workbook.save(destination)
    return destination
