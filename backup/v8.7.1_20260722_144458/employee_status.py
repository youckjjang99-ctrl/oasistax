from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pandas as pd
import streamlit as st
import pytesseract
import requests
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from cloud_db import CloudDatabase, cloud_is_configured
from customer_history import save_customer_event
from document_preprocessor import preprocess_document
from employment_support_2026 import render_employment_support_analysis
from utils import get_user_dirs

TABLE_EMPLOYEE_ROSTERS = "oasis_employee_rosters"

DEFAULT_ROSTER_VISION_MODEL = "gpt-5-mini"
VISION_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def _read_secret(name: str, default: str = "") -> str:
    value = os.environ.get(name, "")
    if value:
        return value.strip()
    try:
        if name in st.secrets:
            return str(st.secrets[name]).strip()
    except Exception:
        pass
    return default


def _extract_response_text(response_data: dict[str, Any]) -> str:
    direct = response_data.get("output_text")
    if isinstance(direct, str):
        return direct.strip()

    values: list[str] = []
    for output in response_data.get("output", []) or []:
        if not isinstance(output, dict):
            continue
        for content in output.get("content", []) or []:
            if not isinstance(content, dict):
                continue
            value = content.get("text")
            if isinstance(value, str):
                values.append(value)
    return "\n".join(values).strip()


def _vision_image_data_url(filename: str, data: bytes) -> str:
    image = Image.open(io.BytesIO(data))
    image = ImageOps.exif_transpose(image).convert("RGB")

    maximum = max(image.size)
    if maximum > 2400:
        ratio = 2400 / maximum
        image = image.resize(
            (
                max(1, int(image.width * ratio)),
                max(1, int(image.height * ratio)),
            ),
            Image.Resampling.LANCZOS,
        )

    output = io.BytesIO()
    image.save(output, format="JPEG", quality=92, optimize=True)
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _roster_vision_schema() -> dict[str, Any]:
    employee_properties = {
        "sequence": {"type": "integer"},
        "name": {"type": "string"},
        "birth_six": {"type": "string"},
        "national_pension_date": {"type": "string"},
        "health_insurance_date": {"type": "string"},
        "industrial_accident_date": {"type": "string"},
        "employment_insurance_date": {"type": "string"},
    }
    workplace_properties = {
        "workplace_management_no": {"type": "string"},
        "workplace_name": {"type": "string"},
        "source_images": {
            "type": "array",
            "items": {"type": "string"},
        },
        "expected_employee_count": {"type": "integer"},
        "employees": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": employee_properties,
                "required": list(employee_properties),
            },
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "document_type": {
                "type": "string",
                "enum": [
                    "four_insurance_roster",
                    "non_roster",
                    "mixed",
                ],
            },
            "total_expected_employee_count": {"type": "integer"},
            "workplaces": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": workplace_properties,
                    "required": list(workplace_properties),
                },
            },
            "ignored_pages": {
                "type": "array",
                "items": {"type": "string"},
            },
            "notes": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [
            "document_type",
            "total_expected_employee_count",
            "workplaces",
            "ignored_pages",
            "notes",
        ],
    }


def _extract_roster_with_ai_vision(
    image_files: list[tuple[str, bytes]],
) -> dict[str, Any]:
    api_key = _read_secret("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY가 설정되지 않아 AI 비전 분석을 사용할 수 없습니다."
        )

    content: list[dict[str, Any]] = [
        {
            "type": "input_text",
            "text": (
                "첨부 이미지는 한국의 '4대 사회보험 사업장 가입자 명부'입니다. "
                "표의 각 직원 행을 정확히 읽으세요. 문서가 90도 또는 180도 "
                "회전되어 있어도 올바르게 해석하세요. 안내문이나 도장만 있는 "
                "페이지, '이하 여백' 페이지는 직원 행으로 만들지 마세요. "
                "연번이 있는 실제 가입내역 표에서만 직원을 추출하세요. "
                "성명은 반드시 '성명' 열의 한글 이름만 사용하세요. "
                "주민등록번호는 앞 6자리만 birth_six에 기록하고 뒷자리는 "
                "절대 출력하지 마세요. 날짜가 '-'이면 빈 문자열로 기록하세요. "
                "날짜는 YYYY-MM-DD 형식으로 통일하세요. expected_employee_count는 "
                "가입내역 표의 연번 범위와 실제 행 수를 근거로 정하세요. "
                "중요: 첨부 이미지에 사업장 관리번호나 사업장 명칭이 서로 다른 "
                "여러 사업장 명부가 포함될 수 있습니다. 서로 다른 사업장을 하나로 "
                "간주하거나 제외하지 말고 workplaces 배열에 각각 별도로 만드세요. "
                "각 사업장의 관리번호, 명칭, 예상 인원과 직원 목록을 모두 추출하세요. "
                "같은 사업장의 여러 페이지에서만 중복 직원을 한 번으로 합치세요. "
                "total_expected_employee_count는 모든 유효 사업장 예상 인원의 합계입니다."
            ),
        }
    ]

    for index, (filename, data) in enumerate(image_files, start=1):
        content.append(
            {
                "type": "input_text",
                "text": f"이미지 {index}: {filename}",
            }
        )
        content.append(
            {
                "type": "input_image",
                "image_url": _vision_image_data_url(filename, data),
                "detail": "high",
            }
        )

    payload = {
        "model": _read_secret(
            "OPENAI_ROSTER_VISION_MODEL",
            DEFAULT_ROSTER_VISION_MODEL,
        ),
        "input": [
            {
                "role": "user",
                "content": content,
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "four_insurance_roster",
                "description": (
                    "4대 사회보험 사업장 가입자 명부의 직원 행 추출 결과"
                ),
                "strict": True,
                "schema": _roster_vision_schema(),
            }
        },
        "max_output_tokens": 8000,
    }

    try:
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=240,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"AI 비전 연결 실패: {exc}") from exc

    if not response.ok:
        raise RuntimeError(
            f"AI 비전 분석 실패(HTTP {response.status_code}): "
            f"{response.text[:1000]}"
        )

    response_data = response.json()
    result_text = _extract_response_text(response_data)
    if not result_text:
        raise RuntimeError("AI 비전 분석 결과가 비어 있습니다.")

    try:
        result = json.loads(result_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "AI 비전 분석 결과 JSON을 해석하지 못했습니다."
        ) from exc

    if not isinstance(result, dict):
        raise RuntimeError("AI 비전 분석 결과 형식이 올바르지 않습니다.")
    return result


def _clean_review_name(value: Any) -> str:
    return re.sub(r"[^가-힣]", "", str(value or "").strip())


def _clean_birth_six(value: Any) -> str:
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    return digits[:6] if len(digits) >= 6 else ""


def _review_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text in {"-", "없음", "해당없음"}:
        return ""
    return _parse_date(text)


def _vision_result_to_review_rows(
    result: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    workplaces = result.get("workplaces", []) or []
    for workplace_index, workplace in enumerate(workplaces, start=1):
        if not isinstance(workplace, dict):
            continue

        workplace_no = re.sub(
            r"[^0-9]",
            "",
            str(workplace.get("workplace_management_no", "") or ""),
        )
        workplace_name = str(
            workplace.get("workplace_name", "") or ""
        ).strip()
        workplace_label = workplace_name or (
            f"사업장 {workplace_index}"
        )

        for index, item in enumerate(
            workplace.get("employees", []) or [],
            start=1,
        ):
            if not isinstance(item, dict):
                continue
            name = _clean_review_name(item.get("name", ""))
            birth_six = _clean_birth_six(item.get("birth_six", ""))
            identity = f"{workplace_no}|{workplace_label}|{name}|{birth_six}"
            if not name or identity in seen:
                continue
            seen.add(identity)

            sequence = item.get("sequence", index)
            try:
                sequence = int(sequence)
            except (TypeError, ValueError):
                sequence = index

            rows.append(
                {
                    "포함": True,
                    "사업장명": workplace_label,
                    "사업장관리번호": workplace_no,
                    "연번": sequence,
                    "성명": name,
                    "생년월일6자리": birth_six,
                    "국민연금": _review_date(
                        item.get("national_pension_date", "")
                    ),
                    "건강보험": _review_date(
                        item.get("health_insurance_date", "")
                    ),
                    "산재보험": _review_date(
                        item.get("industrial_accident_date", "")
                    ),
                    "고용보험": _review_date(
                        item.get("employment_insurance_date", "")
                    ),
                }
            )

    rows.sort(
        key=lambda row: (
            str(row.get("사업장명", "")),
            int(row.get("연번", 0) or 0),
        )
    )
    return rows


def _vision_workplace_summary(
    result: dict[str, Any],
) -> list[dict[str, Any]]:
    summaries = []
    for workplace in result.get("workplaces", []) or []:
        if not isinstance(workplace, dict):
            continue
        employees = [
            item
            for item in (workplace.get("employees", []) or [])
            if isinstance(item, dict)
        ]
        summaries.append(
            {
                "사업장명": str(
                    workplace.get("workplace_name", "") or ""
                ),
                "사업장관리번호": re.sub(
                    r"[^0-9]",
                    "",
                    str(
                        workplace.get(
                            "workplace_management_no",
                            "",
                        )
                        or ""
                    ),
                ),
                "예상인원": int(
                    workplace.get("expected_employee_count", 0)
                    or 0
                ),
                "AI추출인원": len(employees),
            }
        )
    return summaries


def _employees_to_review_rows(
    employees: list[dict[str, Any]],
    start_sequence: int = 1,
) -> list[dict[str, Any]]:
    rows = []
    for offset, employee in enumerate(employees):
        rows.append(
            {
                "포함": True,
                "사업장명": "",
                "사업장관리번호": "",
                "연번": start_sequence + offset,
                "성명": employee.get("name_masked", ""),
                "생년월일6자리": (
                    str(employee.get("birth_year", "")) + "0101"
                    if employee.get("birth_year")
                    else ""
                ),
                "국민연금": "",
                "건강보험": employee.get("acquisition_date", ""),
                "산재보험": "",
                "고용보험": "",
            }
        )
    return rows


def _review_rows_to_employees(
    review_df: pd.DataFrame,
) -> tuple[list[dict[str, Any]], list[str]]:
    employees: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[str] = set()

    if review_df is None or review_df.empty:
        return [], ["검수표에 직원이 없습니다."]

    for row_index, row in review_df.iterrows():
        if not bool(row.get("포함", True)):
            continue

        sequence = row.get("연번", row_index + 1)
        name = _clean_review_name(row.get("성명", ""))
        birth_six = _clean_birth_six(row.get("생년월일6자리", ""))
        dates = [
            _review_date(row.get(column, ""))
            for column in ["국민연금", "건강보험", "산재보험", "고용보험"]
        ]
        dates = [value for value in dates if value]

        if not (2 <= len(name) <= 5):
            errors.append(f"{sequence}번: 성명을 확인해주세요.")
            continue
        if len(birth_six) != 6:
            errors.append(f"{sequence}번 {name}: 생년월일 6자리를 확인해주세요.")
            continue
        if not dates:
            errors.append(f"{sequence}번 {name}: 자격취득일을 하나 이상 입력해주세요.")
            continue

        workplace_name = str(
            row.get("사업장명", "") or ""
        ).strip()
        workplace_no = re.sub(
            r"[^0-9]",
            "",
            str(row.get("사업장관리번호", "") or ""),
        )
        identity = (
            f"{workplace_no}|{workplace_name}|{name}|{birth_six}"
        )
        if identity in seen:
            continue
        seen.add(identity)

        employee = _normalize_employee(
            name,
            birth_six,
            min(dates),
            "",
            "가입중",
            ", ".join(
                column
                for column in ["국민연금", "건강보험", "산재보험", "고용보험"]
                if _review_date(row.get(column, ""))
            ),
        )
        if employee:
            employee["source_dates"] = sorted(dates)
            employee["workplace_name"] = workplace_name
            employee["workplace_management_no"] = workplace_no
            employees.append(employee)

    return employees, errors


COLUMN_ALIASES = {
    "name": [
        "성명", "가입자명", "근로자명", "직원명", "피보험자명",
        "이름", "가입자 성명",
    ],
    "birth": [
        "생년월일", "주민등록번호", "주민번호", "생년", "출생일",
    ],
    "acquisition": [
        "자격취득일", "취득일", "고용보험취득일", "가입일",
        "자격 취득일", "고용보험 자격취득일",
    ],
    "loss": [
        "자격상실일", "상실일", "퇴사일", "고용보험상실일",
    ],
    "status": [
        "자격상태", "가입상태", "상태", "처리상태",
    ],
    "insurance": [
        "보험구분", "가입보험", "보험종류", "사회보험",
    ],
}


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "nat"}:
        return ""
    return re.sub(r"\s+", " ", text)


def _normalize_business_no(value: Any) -> str:
    return re.sub(r"[^0-9]", "", str(value or ""))


def _company_key(business_no: str, company_name: str) -> str:
    return _normalize_business_no(business_no) or company_name


def _storage_path(user_id: str) -> Path:
    return get_user_dirs(user_id)["base"] / "employee_rosters.json"


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _normalized_columns(df: pd.DataFrame) -> dict[str, str]:
    mapping = {}
    for column in df.columns:
        key = re.sub(r"[^0-9a-z가-힣]", "", str(column).lower())
        mapping[key] = str(column)
    return mapping


def _find_column(df: pd.DataFrame, aliases: list[str]) -> str | None:
    normalized = _normalized_columns(df)
    for alias in aliases:
        key = re.sub(r"[^0-9a-z가-힣]", "", alias.lower())
        if key in normalized:
            return normalized[key]
    for key, original in normalized.items():
        if any(
            re.sub(r"[^0-9a-z가-힣]", "", alias.lower()) in key
            for alias in aliases
        ):
            return original
    return None


def _mask_name(name: str) -> str:
    name = _clean(name)
    if len(name) <= 1:
        return name
    if len(name) == 2:
        return name[0] + "*"
    return name[0] + "*" * (len(name) - 2) + name[-1]


def _parse_date(value: Any) -> str:
    text = _clean(value)
    if not text:
        return ""

    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.strftime("%Y-%m-%d")

    digits = re.sub(r"[^0-9]", "", text)
    candidates = []
    if len(digits) >= 8:
        candidates.append(digits[:8])
    elif len(digits) == 6:
        prefix = "19" if int(digits[:2]) > 30 else "20"
        candidates.append(prefix + digits)

    for candidate in candidates:
        try:
            return datetime.strptime(candidate, "%Y%m%d").strftime("%Y-%m-%d")
        except ValueError:
            continue

    try:
        parsed = pd.to_datetime(text, errors="coerce")
        if pd.notna(parsed):
            return parsed.strftime("%Y-%m-%d")
    except Exception:
        pass
    return ""


def _birth_info(value: Any) -> tuple[str, int | None]:
    text = _clean(value)
    digits = re.sub(r"[^0-9]", "", text)
    birth_date = ""

    if len(digits) >= 8:
        birth_date = _parse_date(digits[:8])
    elif len(digits) >= 6:
        yy = int(digits[:2])
        century = 1900 if yy > (date.today().year % 100) else 2000
        candidate = f"{century + yy:04d}{digits[2:6]}"
        birth_date = _parse_date(candidate)

    if not birth_date:
        return "", None

    born = datetime.strptime(birth_date, "%Y-%m-%d").date()
    today = date.today()
    age = today.year - born.year - (
        (today.month, today.day) < (born.month, born.day)
    )
    return birth_date, age


def _age_group(age: int | None) -> str:
    if age is None:
        return "확인필요"
    if 15 <= age <= 34:
        return "청년"
    if age >= 60:
        return "고령자"
    if age >= 50:
        return "중장년"
    return "일반"


def _tenure_months(acquisition_date: str) -> int | None:
    if not acquisition_date:
        return None
    try:
        acquired = datetime.strptime(
            acquisition_date,
            "%Y-%m-%d",
        ).date()
    except ValueError:
        return None

    today = date.today()
    return max(
        (today.year - acquired.year) * 12
        + today.month
        - acquired.month
        - (1 if today.day < acquired.day else 0),
        0,
    )


def _normalize_employee(
    name: Any,
    birth: Any,
    acquisition: Any,
    loss: Any = "",
    status: Any = "",
    insurance: Any = "",
) -> dict[str, Any] | None:
    raw_name = _clean(name)
    acquisition_date = _parse_date(acquisition)
    loss_date = _parse_date(loss)
    birth_date, age = _birth_info(birth)

    if not raw_name and not acquisition_date:
        return None

    status_text = _clean(status)
    active = not bool(loss_date)
    if any(word in status_text for word in ["상실", "퇴사", "종료"]):
        active = False

    months = _tenure_months(acquisition_date)
    return {
        "employee_id": hashlib.sha256(
            f"{raw_name}|{birth_date}|{acquisition_date}".encode("utf-8")
        ).hexdigest()[:18],
        "name_masked": _mask_name(raw_name),
        "birth_year": birth_date[:4] if birth_date else "",
        "age": age,
        "age_group": _age_group(age),
        "acquisition_date": acquisition_date,
        "loss_date": loss_date,
        "active": active,
        "status": "가입중" if active else "상실",
        "insurance": _clean(insurance),
        "tenure_months": months,
    }


def _parse_dataframe(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []

    df = df.dropna(how="all").copy()
    name_col = _find_column(df, COLUMN_ALIASES["name"])
    birth_col = _find_column(df, COLUMN_ALIASES["birth"])
    acquisition_col = _find_column(df, COLUMN_ALIASES["acquisition"])
    loss_col = _find_column(df, COLUMN_ALIASES["loss"])
    status_col = _find_column(df, COLUMN_ALIASES["status"])
    insurance_col = _find_column(df, COLUMN_ALIASES["insurance"])

    if not name_col and not acquisition_col:
        # 여러 줄 머리글 문서를 대비해 상단 10행을 다시 헤더 후보로 검사
        for header_row in range(min(10, len(df))):
            candidate = pd.read_excel(
                io.BytesIO(df.attrs.get("_raw_bytes", b"")),
                header=header_row,
            ) if df.attrs.get("_raw_bytes") else pd.DataFrame()
            if candidate.empty:
                continue
            parsed = _parse_dataframe(candidate)
            if parsed:
                return parsed
        return []

    employees = []
    for _, row in df.iterrows():
        employee = _normalize_employee(
            row.get(name_col, "") if name_col else "",
            row.get(birth_col, "") if birth_col else "",
            row.get(acquisition_col, "") if acquisition_col else "",
            row.get(loss_col, "") if loss_col else "",
            row.get(status_col, "") if status_col else "",
            row.get(insurance_col, "") if insurance_col else "",
        )
        if employee:
            employees.append(employee)

    deduped = {}
    for employee in employees:
        deduped[employee["employee_id"]] = employee
    return list(deduped.values())


def _parse_excel(data: bytes) -> list[dict[str, Any]]:
    workbook = pd.ExcelFile(io.BytesIO(data))
    best: list[dict[str, Any]] = []

    for sheet_name in workbook.sheet_names:
        for header_row in range(0, 10):
            try:
                df = pd.read_excel(
                    io.BytesIO(data),
                    sheet_name=sheet_name,
                    header=header_row,
                    dtype=object,
                )
            except Exception:
                continue
            parsed = _parse_dataframe(df)
            if len(parsed) > len(best):
                best = parsed
            if len(best) >= 2:
                break
    return best


ROSTER_MASKED_RRN_PATTERN = re.compile(
    r"(?<!\d)(\d{6})\s*[-–—]?\s*[1-8]?\s*[*xX•·]{4,7}(?!\d)"
)
ROSTER_DATE_PATTERN = re.compile(
    r"(19\d{2}|20\d{2})\s*[.\-/년]\s*(\d{1,2})"
    r"\s*[.\-/월]\s*(\d{1,2})"
)
ROSTER_NAME_PATTERN = re.compile(r"^[가-힣]{2,5}$")
ROSTER_EXCLUDED_NAMES = {
    "국민연금", "건강보험", "산재보험", "고용보험",
    "사업장", "가입자", "명부", "성명", "연번",
    "자격취득일", "취득일", "주민등록번호", "등록번호",
    "출력일시", "발급일시", "발급번호", "확인용",
    "구분", "보험", "이하여백", "주식회사",
}


def _prepare_roster_image(image: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(image).convert("RGB")
    maximum = max(image.size)
    if maximum < 2400:
        ratio = 2400 / max(maximum, 1)
        image = image.resize(
            (
                max(1, int(image.width * ratio)),
                max(1, int(image.height * ratio)),
            )
        )
    image = ImageOps.grayscale(image)
    image = ImageOps.autocontrast(image, cutoff=1)
    image = ImageEnhance.Contrast(image).enhance(1.55)
    image = ImageEnhance.Sharpness(image).enhance(1.7)
    return image.filter(ImageFilter.MedianFilter(size=3))


def _normalize_roster_date(value: str) -> str:
    match = ROSTER_DATE_PATTERN.search(str(value or ""))
    if not match:
        return ""
    try:
        return datetime(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        ).strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _valid_roster_name(value: str) -> bool:
    value = re.sub(r"\s+", "", str(value or ""))
    return bool(
        ROSTER_NAME_PATTERN.fullmatch(value)
        and value not in ROSTER_EXCLUDED_NAMES
    )


def _parse_roster_text_blocks(text: str) -> list[dict[str, Any]]:
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in str(text or "").splitlines()
        if re.sub(r"\s+", " ", line).strip()
    ]
    employees: list[dict[str, Any]] = []

    for index, line in enumerate(lines):
        compact = re.sub(r"\s+", "", line)
        rrn_match = ROSTER_MASKED_RRN_PATTERN.search(compact)
        if not rrn_match:
            continue

        start = max(0, index - 1)
        end = min(len(lines), index + 4)
        block_lines = lines[start:end]
        block = " ".join(block_lines)

        name_candidates: list[str] = []
        for token in re.findall(r"[가-힣]{2,5}", block):
            if _valid_roster_name(token):
                name_candidates.append(token)

        if not name_candidates:
            continue

        # 주민번호가 있는 줄에서 주민번호 뒤쪽 이름을 우선한다.
        same_line_after_rrn = compact[rrn_match.end():]
        same_line_names = [
            token
            for token in re.findall(r"[가-힣]{2,5}", same_line_after_rrn)
            if _valid_roster_name(token)
        ]
        name = (
            same_line_names[0]
            if same_line_names
            else name_candidates[0]
        )

        dates: list[str] = []
        for match in ROSTER_DATE_PATTERN.finditer(block):
            normalized = _normalize_roster_date(match.group(0))
            if normalized and normalized not in dates:
                dates.append(normalized)

        if not dates:
            continue

        acquisition = min(dates)
        employee = _normalize_employee(
            name,
            rrn_match.group(1),
            acquisition,
            "",
            "가입중",
            "",
        )
        if employee:
            employee["source_dates"] = sorted(dates)
            employees.append(employee)

    deduped: dict[str, dict[str, Any]] = {}
    for employee in employees:
        identity = "|".join(
            [
                str(employee.get("name_masked", "")),
                str(employee.get("birth_year", "")),
            ]
        )
        current = deduped.get(identity)
        if current is None:
            deduped[identity] = employee
            continue
        current_date = str(current.get("acquisition_date", ""))
        new_date = str(employee.get("acquisition_date", ""))
        if new_date and (not current_date or new_date < current_date):
            deduped[identity] = employee
    return list(deduped.values())


def _cluster_line_positions(
    values: list[int],
    tolerance: int = 8,
) -> list[int]:
    if not values:
        return []
    values = sorted(int(value) for value in values)
    groups: list[list[int]] = [[values[0]]]
    for value in values[1:]:
        if value - groups[-1][-1] <= tolerance:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [
        int(round(sum(group) / len(group)))
        for group in groups
    ]


def _ocr_cell_variants(
    image: np.ndarray,
    *,
    language: str,
    configs: list[str],
) -> list[str]:
    if image.size == 0:
        return []

    height, width = image.shape[:2]
    scale = max(2.0, min(4.0, 1200 / max(width, height, 1)))
    resized = cv2.resize(
        image,
        None,
        fx=scale,
        fy=scale,
        interpolation=cv2.INTER_CUBIC,
    )

    if len(resized.shape) == 3:
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    else:
        gray = resized

    variants = [
        gray,
        cv2.threshold(
            gray,
            0,
            255,
            cv2.THRESH_BINARY + cv2.THRESH_OTSU,
        )[1],
        cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            11,
        ),
    ]

    results: list[str] = []
    for variant in variants:
        bordered = cv2.copyMakeBorder(
            variant,
            24,
            24,
            24,
            24,
            cv2.BORDER_CONSTANT,
            value=255,
        )
        for config in configs:
            try:
                value = pytesseract.image_to_string(
                    bordered,
                    lang=language,
                    config=config,
                )
            except Exception:
                continue
            value = re.sub(r"\s+", "", str(value or ""))
            if value and value not in results:
                results.append(value)
    return results


def _read_roster_rrn(cell: np.ndarray) -> str:
    values = _ocr_cell_variants(
        cell,
        language="eng",
        configs=[
            "--oem 1 --psm 7 -c tessedit_char_whitelist=0123456789-*xX",
            "--oem 1 --psm 8 -c tessedit_char_whitelist=0123456789-*xX",
        ],
    )
    for value in values:
        digits = re.sub(r"[^0-9]", "", value)
        if len(digits) >= 6:
            return digits[:6]
    return ""


def _read_roster_name(cell: np.ndarray) -> str:
    values = _ocr_cell_variants(
        cell,
        language="kor",
        configs=[
            "--oem 1 --psm 7 -c preserve_interword_spaces=1",
            "--oem 1 --psm 8",
            "--oem 1 --psm 13",
        ],
    )
    candidates: list[str] = []
    for value in values:
        for token in re.findall(r"[가-힣]{2,5}", value):
            if _valid_roster_name(token):
                candidates.append(token)

    if not candidates:
        return ""

    # Prefer a consistently repeated result; ties favor a common 3-char name.
    counts: dict[str, int] = {}
    for candidate in candidates:
        counts[candidate] = counts.get(candidate, 0) + 1
    return max(
        counts,
        key=lambda value: (
            counts[value],
            int(len(value) == 3),
            -abs(len(value) - 3),
        ),
    )


def _read_roster_dates(cell: np.ndarray) -> list[str]:
    values = _ocr_cell_variants(
        cell,
        language="eng",
        configs=[
            "--oem 1 --psm 7 -c tessedit_char_whitelist=0123456789.-/",
            "--oem 1 --psm 6 -c tessedit_char_whitelist=0123456789.-/",
        ],
    )
    dates: list[str] = []
    for value in values:
        normalized_value = value.replace("/", ".").replace("-", ".")
        for match in re.finditer(
            r"(19\d{2}|20\d{2})[.]?(\d{1,2})[.]?(\d{1,2})",
            normalized_value,
        ):
            try:
                normalized = datetime(
                    int(match.group(1)),
                    int(match.group(2)),
                    int(match.group(3)),
                ).strftime("%Y-%m-%d")
            except ValueError:
                continue
            if normalized not in dates:
                dates.append(normalized)
    return dates


def _best_regular_line_band(
    positions: list[int],
    *,
    minimum_lines: int = 8,
    minimum_gap: int = 10,
    maximum_gap: int = 65,
) -> list[int]:
    positions = sorted(set(int(value) for value in positions))
    best: list[int] = []
    best_score = float("-inf")

    for start_index in range(len(positions)):
        current = [positions[start_index]]
        for next_index in range(start_index + 1, len(positions)):
            gap = positions[next_index] - current[-1]
            if gap < minimum_gap:
                continue
            if gap > maximum_gap:
                break
            current.append(positions[next_index])

            if len(current) < minimum_lines:
                continue

            gaps = np.diff(current).astype(float)
            median_gap = float(np.median(gaps))
            if median_gap <= 0:
                continue
            deviation = float(
                np.mean(np.abs(gaps - median_gap)) / median_gap
            )
            span = current[-1] - current[0]
            score = (
                len(current) * 120
                + span * 0.25
                - deviation * 500
            )
            if score > best_score:
                best = list(current)
                best_score = score

    return best


def _detect_roster_grid(
    image: np.ndarray,
) -> tuple[tuple[int, int, int, int] | None, list[int], list[int]]:
    gray = (
        cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        if len(image.shape) == 3
        else image
    )
    binary = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        31,
        12,
    )

    horizontal_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (max(gray.shape[1] // 18, 28), 1),
    )
    horizontal = cv2.morphologyEx(
        binary,
        cv2.MORPH_OPEN,
        horizontal_kernel,
        iterations=1,
    )

    horizontal_strength = np.sum(horizontal > 0, axis=1)
    raw_y = np.where(
        horizontal_strength >= max(gray.shape[1] * 0.30, 100)
    )[0].tolist()
    all_y_lines = _cluster_line_positions(raw_y, tolerance=5)

    # The employee table contains a long sequence of near-evenly spaced
    # horizontal lines. Select that band instead of the document border.
    y_lines = _best_regular_line_band(
        all_y_lines,
        minimum_lines=8,
        minimum_gap=max(10, gray.shape[0] // 90),
        maximum_gap=max(55, gray.shape[0] // 12),
    )
    if len(y_lines) < 8:
        return None, [], []

    top = max(y_lines[0] - 4, 0)
    bottom = min(y_lines[-1] + 5, gray.shape[0])
    band_height = bottom - top
    if band_height <= 0:
        return None, [], []

    vertical_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (1, max(band_height // 4, 24)),
    )
    vertical = cv2.morphologyEx(
        binary[top:bottom, :],
        cv2.MORPH_OPEN,
        vertical_kernel,
        iterations=1,
    )
    vertical_strength = np.sum(vertical > 0, axis=0)
    raw_x = np.where(
        vertical_strength >= max(band_height * 0.28, 25)
    )[0].tolist()

    # Merge doubled lines caused by shadows, thick printing or markup.
    x_lines = _cluster_line_positions(raw_x, tolerance=12)
    if len(x_lines) < 6:
        return None, [], []

    left = max(min(x_lines) - 3, 0)
    right = min(max(x_lines) + 4, gray.shape[1])
    width = right - left
    if width < gray.shape[1] * 0.50:
        return None, [], []

    local_x = [
        value - left
        for value in x_lines
        if left <= value <= right
    ]
    local_y = [
        value - top
        for value in y_lines
        if top <= value <= bottom
    ]
    return (
        left,
        top,
        width,
        band_height,
    ), local_x, local_y


def _select_roster_columns(
    x_lines: list[int],
    width: int,
) -> list[int]:
    values = sorted(
        value
        for value in x_lines
        if 0 <= value <= width
    )
    expected = [
        0.0,
        0.10,
        0.31,
        0.45,
        0.585,
        0.72,
        0.855,
        1.0,
    ]

    best: list[int] | None = None
    best_score = float("inf")

    if len(values) >= 8:
        from itertools import combinations

        # Limit candidate count while retaining the document edges.
        candidates = values
        if len(candidates) > 13:
            candidates = sorted(
                set(
                    [candidates[0], candidates[-1]]
                    + candidates[1:-1:2]
                    + candidates[2:-1:2]
                )
            )

        for selected in combinations(candidates, 8):
            span = selected[-1] - selected[0]
            if span < width * 0.70:
                continue
            ratios = [
                (value - selected[0]) / max(span, 1)
                for value in selected
            ]
            ratio_error = sum(
                abs(actual - target)
                for actual, target in zip(ratios, expected)
            )
            edge_error = (
                abs(selected[0]) / max(width, 1)
                + abs(width - selected[-1]) / max(width, 1)
            )
            score = ratio_error + edge_error * 2
            if score < best_score:
                best = list(selected)
                best_score = score

    if best is None:
        best = [
            int(round(width * ratio))
            for ratio in expected
        ]

    # Normalize the selected boundaries to the table crop.
    left = best[0]
    right = best[-1]
    span = max(right - left, 1)
    return [
        int(round((value - left) * width / span))
        for value in best
    ]


def _parse_roster_grid_image(
    image: np.ndarray,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    grid_box, x_lines, y_lines = _detect_roster_grid(image)
    if grid_box is None or len(y_lines) < 4:
        return [], {
            "method": "cell_grid_ocr",
            "grid_found": False,
            "reason": "table_grid_not_found",
        }

    x, y, width, height = grid_box
    table = image[y:y + height, x:x + width]
    columns = _select_roster_columns(x_lines, width)

    row_pairs: list[tuple[int, int]] = []
    for top, bottom in zip(y_lines, y_lines[1:]):
        row_height = bottom - top
        if 18 <= row_height <= max(int(height * 0.16), 120):
            row_pairs.append((top, bottom))

    employees: list[dict[str, Any]] = []
    row_debug: list[dict[str, Any]] = []
    margin = 3

    for row_index, (top, bottom) in enumerate(row_pairs):
        rrn_cell = table[
            top + margin:bottom - margin,
            columns[1] + margin:columns[2] - margin,
        ]
        name_cell = table[
            top + margin:bottom - margin,
            columns[2] + margin:columns[3] - margin,
        ]

        birth = _read_roster_rrn(rrn_cell)
        name = _read_roster_name(name_cell)

        dates: list[str] = []
        for column_index in range(3, 7):
            date_cell = table[
                top + margin:bottom - margin,
                columns[column_index] + margin:
                columns[column_index + 1] - margin,
            ]
            for value in _read_roster_dates(date_cell):
                if value not in dates:
                    dates.append(value)

        row_debug.append(
            {
                "row": row_index,
                "birth_detected": bool(birth),
                "name": name,
                "dates": dates,
            }
        )
        if not birth or not name or not dates:
            continue

        employee = _normalize_employee(
            name,
            birth,
            min(dates),
            "",
            "가입중",
            "",
        )
        if employee:
            employee["source_dates"] = sorted(dates)
            employees.append(employee)

    deduped: dict[str, dict[str, Any]] = {}
    for employee in employees:
        identity = "|".join(
            [
                str(employee.get("name_masked", "")),
                str(employee.get("birth_year", "")),
            ]
        )
        deduped[identity] = employee

    detected_employee_rows = sum(
        1
        for row in row_debug
        if row.get("birth_detected")
    )
    recognized_count = len(deduped)
    minimum_acceptable = (
        max(3, int(detected_employee_rows * 0.70))
        if detected_employee_rows
        else 0
    )
    if (
        detected_employee_rows >= 5
        and recognized_count < minimum_acceptable
    ):
        return [], {
            "method": "cell_grid_ocr",
            "grid_found": True,
            "validation_failed": True,
            "reason": "recognized_below_rrn_row_threshold",
            "detected_employee_rows": detected_employee_rows,
            "recognized_employee_count": recognized_count,
            "minimum_acceptable": minimum_acceptable,
            "rows": row_debug,
        }

    return list(deduped.values()), {
        "method": "cell_grid_ocr",
        "grid_found": True,
        "detected_employee_rows": detected_employee_rows,
        "grid_box": {
            "x": x,
            "y": y,
            "width": width,
            "height": height,
        },
        "vertical_line_count": len(x_lines),
        "horizontal_line_count": len(y_lines),
        "candidate_row_count": len(row_pairs),
        "recognized_employee_count": len(deduped),
        "rows": row_debug,
    }


def _parse_roster_image(
    data: bytes,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    pil_image = Image.open(io.BytesIO(data))
    pil_image = ImageOps.exif_transpose(pil_image).convert("RGB")
    base = np.array(pil_image)
    candidates: list[dict[str, Any]] = []

    for rotation in (0, 90, 180, 270):
        if rotation == 0:
            rotated = base
        elif rotation == 90:
            rotated = cv2.rotate(base, cv2.ROTATE_90_CLOCKWISE)
        elif rotation == 180:
            rotated = cv2.rotate(base, cv2.ROTATE_180)
        else:
            rotated = cv2.rotate(base, cv2.ROTATE_90_COUNTERCLOCKWISE)

        employees, detail = _parse_roster_grid_image(rotated)
        score = (
            len(employees) * 1000
            + int(detail.get("candidate_row_count", 0)) * 10
            + int(detail.get("horizontal_line_count", 0))
        )
        candidates.append(
            {
                "rotation": rotation,
                "employees": employees,
                "detail": detail,
                "score": score,
            }
        )

    best = max(
        candidates,
        key=lambda item: (
            item["score"],
            len(item["employees"]),
        ),
    )

    # Preserve a fallback for unusually photographed forms where grid
    # detection fails completely.
    if not best["employees"]:
        prepared = _prepare_roster_image(pil_image)
        fallback_candidates = []
        for rotation in (0, 90, 180, 270):
            rotated = prepared.rotate(
                rotation,
                expand=True,
                fillcolor="white",
            )
            text = pytesseract.image_to_string(
                rotated,
                lang="kor+eng",
                config="--oem 1 --psm 6",
            )
            fallback_employees = _parse_roster_text_blocks(text)
            fallback_candidates.append(
                {
                    "rotation": rotation,
                    "employees": fallback_employees,
                }
            )
        fallback_best = max(
            fallback_candidates,
            key=lambda item: len(item["employees"]),
        )
        if fallback_best["employees"]:
            return fallback_best["employees"], {
                "method": "text_fallback_after_grid",
                "selected_rotation": fallback_best["rotation"],
                "recognized_employee_count": len(
                    fallback_best["employees"]
                ),
                "grid_candidates": [
                    {
                        "rotation": item["rotation"],
                        "employee_count": len(item["employees"]),
                        "detail": item["detail"],
                    }
                    for item in candidates
                ],
            }

    return best["employees"], {
        "method": "four_insurance_cell_grid_ocr_v3",
        "selected_rotation": best["rotation"],
        "recognized_employee_count": len(best["employees"]),
        "selected_detail": best["detail"],
        "rotation_candidates": [
            {
                "rotation": item["rotation"],
                "employee_count": len(item["employees"]),
                "score": item["score"],
                "grid_found": item["detail"].get(
                    "grid_found",
                    False,
                ),
                "candidate_row_count": item["detail"].get(
                    "candidate_row_count",
                    0,
                ),
            }
            for item in candidates
        ],
    }


def _parse_pdf_text(text: str) -> list[dict[str, Any]]:
    employees = []
    date_pattern = re.compile(
        r"(19\d{2}|20\d{2})[-./년 ]?(\d{1,2})[-./월 ]?(\d{1,2})"
    )
    rrn_pattern = re.compile(r"\b(\d{6})[- ]?\d{7}\b")

    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if len(line) < 5:
            continue

        dates = [
            f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
            for match in date_pattern.finditer(line)
        ]
        if not dates:
            continue

        name_match = re.search(r"([가-힣]{2,5})", line)
        if not name_match:
            continue

        rrn_match = rrn_pattern.search(line)
        birth = rrn_match.group(1) if rrn_match else ""
        acquisition = dates[-1]
        employee = _normalize_employee(
            name_match.group(1),
            birth,
            acquisition,
            "",
            "가입중",
            "",
        )
        if employee:
            employees.append(employee)

    deduped = {}
    for employee in employees:
        deduped[employee["employee_id"]] = employee
    return list(deduped.values())


def parse_roster(
    filename: str,
    data: bytes,
    progress_callback=None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    suffix = Path(filename).suffix.lower()

    if suffix in {".xlsx", ".xls"}:
        employees = _parse_excel(data)
        return employees, {
            "method": "excel",
            "filename": filename,
        }

    if suffix == ".csv":
        for encoding in ["utf-8-sig", "cp949", "euc-kr"]:
            try:
                df = pd.read_csv(io.BytesIO(data), encoding=encoding)
                employees = _parse_dataframe(df)
                return employees, {
                    "method": "csv",
                    "filename": filename,
                    "encoding": encoding,
                }
            except Exception:
                continue
        raise ValueError("CSV 파일 인코딩을 확인할 수 없습니다.")

    if suffix in {".jpg", ".jpeg", ".png"}:
        if progress_callback:
            progress_callback(
                0,
                4,
                "가입자명부 표와 문서 방향을 분석하고 있습니다.",
            )
        employees, extraction = _parse_roster_image(data)
        if progress_callback:
            progress_callback(
                4,
                4,
                f"가입자명부 직원 {len(employees)}명 인식 완료",
            )
        return employees, {
            "method": "image_roster_table_ocr",
            "filename": filename,
            "extraction": extraction,
        }

    if suffix == ".pdf":
        text, extraction = preprocess_document(
            filename,
            data,
            progress_callback=progress_callback,
        )
        employees = _parse_pdf_text(text)
        return employees, {
            "method": "pdf_ocr",
            "filename": filename,
            "extraction": extraction,
        }

    raise ValueError(
        "지원 형식은 XLSX, XLS, CSV, PDF, JPG, JPEG, PNG입니다."
    )


def build_summary(employees: list[dict[str, Any]]) -> dict[str, Any]:
    active = [employee for employee in employees if employee.get("active")]
    today = date.today()

    recent_3m = 0
    recent_6m = 0
    long_tenure = 0
    for employee in active:
        acquisition = employee.get("acquisition_date", "")
        if acquisition:
            try:
                acquired = datetime.strptime(acquisition, "%Y-%m-%d").date()
                days = (today - acquired).days
                if 0 <= days <= 92:
                    recent_3m += 1
                if 0 <= days <= 184:
                    recent_6m += 1
            except ValueError:
                pass

        months = employee.get("tenure_months")
        if isinstance(months, int) and months >= 24:
            long_tenure += 1

    return {
        "total_count": len(employees),
        "active_count": len(active),
        "inactive_count": len(employees) - len(active),
        "youth_count": sum(
            employee.get("age_group") == "청년"
            for employee in active
        ),
        "middle_aged_count": sum(
            employee.get("age_group") == "중장년"
            for employee in active
        ),
        "senior_count": sum(
            employee.get("age_group") == "고령자"
            for employee in active
        ),
        "unknown_age_count": sum(
            employee.get("age_group") == "확인필요"
            for employee in active
        ),
        "recent_3m_count": recent_3m,
        "recent_6m_count": recent_6m,
        "long_tenure_count": long_tenure,
    }


def save_employee_roster(
    user_id: str,
    business_no: str,
    company_name: str,
    filename: str,
    employees: list[dict[str, Any]],
    parse_info: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    now = datetime.now().isoformat(timespec="seconds")
    summary = build_summary(employees)
    version_id = hashlib.sha256(
        f"{user_id}|{business_no}|{filename}|{now}".encode("utf-8")
    ).hexdigest()[:24]

    record = {
        "version_id": version_id,
        "owner_user_id": user_id,
        "business_no": business_no,
        "company_name": company_name,
        "filename": filename,
        "uploaded_at": now,
        "summary": summary,
        "employees": employees,
        "parse_info": parse_info,
    }

    path = _storage_path(user_id)
    data = _load_json(path, {})
    if not isinstance(data, dict):
        data = {}
    key = _company_key(business_no, company_name)
    versions = data.get(key, [])
    if not isinstance(versions, list):
        versions = []
    versions.insert(0, record)
    data[key] = versions[:20]
    _save_json(path, data)

    cloud_message = "로컬 저장"
    if cloud_is_configured():
        try:
            CloudDatabase().upsert(
                TABLE_EMPLOYEE_ROSTERS,
                [
                    {
                        "version_id": version_id,
                        "owner_user_id": user_id,
                        "business_no": business_no,
                        "company_name": company_name,
                        "filename": filename,
                        "uploaded_at": now,
                        "summary_data": summary,
                        "employee_data": employees,
                        "parse_info": parse_info,
                    }
                ],
                "version_id",
            )
            cloud_message = "로컬·Supabase 저장"
        except Exception:
            cloud_message = "로컬 저장"

    try:
        save_customer_event(
            user_id=user_id,
            business_no=business_no,
            company_name=company_name,
            event_id=f"employee-roster-{version_id}",
            event_title=f"{now[:10]} 4대보험 가입자명부 분석",
            event_detail=(
                f"가입중 {summary['active_count']}명 · "
                f"최근 6개월 취득 {summary['recent_6m_count']}명 · "
                f"청년 {summary['youth_count']}명 · "
                f"고령자 {summary['senior_count']}명"
            ),
            occurred_at=now,
            source="employee_roster",
        )
    except Exception:
        pass

    return record, cloud_message


def load_employee_versions(
    user_id: str,
    business_no: str,
    company_name: str = "",
) -> list[dict[str, Any]]:
    data = _load_json(_storage_path(user_id), {})
    key = _company_key(business_no, company_name)
    versions = data.get(key, []) if isinstance(data, dict) else []
    return versions if isinstance(versions, list) else []


def get_latest_employee_status(
    user_id: str,
    business_no: str,
    company_name: str = "",
) -> dict[str, Any]:
    versions = load_employee_versions(
        user_id,
        business_no,
        company_name,
    )
    return versions[0] if versions else {}


def employee_matching_context(
    user_id: str,
    business_no: str,
    company_name: str = "",
) -> dict[str, Any]:
    latest = get_latest_employee_status(
        user_id,
        business_no,
        company_name,
    )
    if not latest:
        return {}
    return {
        "uploaded_at": latest.get("uploaded_at", ""),
        "summary": latest.get("summary", {}) or {},
        "employees": latest.get("employees", []) or [],
    }


def _display_employee_rows(
    employees: list[dict[str, Any]],
) -> pd.DataFrame:
    rows = []
    for employee in employees:
        months = employee.get("tenure_months")
        tenure = (
            f"{months // 12}년 {months % 12}개월"
            if isinstance(months, int)
            else "확인필요"
        )
        rows.append(
            {
                "사업장": (
                    employee.get("workplace_name", "")
                    or employee.get("workplace_management_no", "")
                ),
                "직원": employee.get("name_masked", ""),
                "출생연도": employee.get("birth_year", ""),
                "연령구간": employee.get("age_group", ""),
                "자격취득일": employee.get("acquisition_date", ""),
                "근속기간": tenure,
                "가입상태": employee.get("status", ""),
            }
        )
    return pd.DataFrame(rows)


def render_employee_status(
    user_id: str,
    business_no: str,
    company_name: str,
) -> None:
    st.markdown("#### 직원현황")
    st.caption(
        "가입자명부 이미지는 AI 비전으로 표 전체를 분석한 뒤, "
        "편집 가능한 검수표에서 확인한 내용만 저장합니다. "
        "주민등록번호 뒷자리는 AI에 출력하도록 요청하지 않으며 저장하지 않습니다."
    )
    st.info(
        "JPG·JPEG·PNG 이미지는 OpenAI API로 전송되어 분석됩니다. "
        "검수 완료 버튼을 누르기 전에는 직원현황과 Supabase에 저장되지 않습니다."
    )

    state_key = (
        f"employee_roster_review_"
        f"{_company_key(business_no, company_name)}"
    )

    uploaded_files = st.file_uploader(
        "4대보험 가입자명부 업로드",
        type=["xlsx", "xls", "csv", "pdf", "jpg", "jpeg", "png"],
        accept_multiple_files=True,
        key=f"employee_roster_upload_{business_no or company_name}",
        help=(
            "가입내역 페이지와 안내 페이지를 함께 올려도 됩니다. "
            "AI가 직원 행이 없는 안내 페이지는 제외합니다."
        ),
    )

    if uploaded_files and st.button(
        "AI 비전 분석 후 검수표 만들기",
        type="primary",
        use_container_width=True,
        key=f"employee_roster_analyze_{business_no or company_name}",
    ):
        progress = st.progress(0, text="가입자명부 분석을 준비하고 있습니다.")
        image_files: list[tuple[str, bytes]] = []
        conventional_rows: list[dict[str, Any]] = []
        parse_files: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []

        for uploaded in uploaded_files:
            suffix = Path(uploaded.name).suffix.lower()
            data = uploaded.getvalue()
            if suffix in VISION_IMAGE_SUFFIXES:
                image_files.append((uploaded.name, data))
                continue
            try:
                employees, parse_info = parse_roster(
                    uploaded.name,
                    data,
                )
                conventional_rows.extend(
                    _employees_to_review_rows(
                        employees,
                        start_sequence=len(conventional_rows) + 1,
                    )
                )
                parse_files.append(
                    {
                        "filename": uploaded.name,
                        "method": parse_info.get("method", ""),
                        "employee_count": len(employees),
                        "status": "success",
                    }
                )
            except Exception as exc:
                failures.append(
                    {
                        "filename": uploaded.name,
                        "error": str(exc),
                    }
                )

        vision_result: dict[str, Any] = {}
        vision_rows: list[dict[str, Any]] = []
        if image_files:
            progress.progress(
                0.25,
                text=f"이미지 {len(image_files)}장을 AI 비전으로 분석하고 있습니다.",
            )
            try:
                vision_result = _extract_roster_with_ai_vision(image_files)
                vision_rows = _vision_result_to_review_rows(vision_result)
                parse_files.extend(
                    {
                        "filename": filename,
                        "method": "openai_vision_structured",
                        "status": "success",
                    }
                    for filename, _ in image_files
                )
            except Exception as vision_exc:
                st.warning(
                    f"AI 비전 분석에 실패해 기존 OCR로 보조 분석합니다: "
                    f"{vision_exc}"
                )
                for filename, data in image_files:
                    try:
                        employees, parse_info = parse_roster(filename, data)
                        vision_rows.extend(
                            _employees_to_review_rows(
                                employees,
                                start_sequence=len(vision_rows) + 1,
                            )
                        )
                        parse_files.append(
                            {
                                "filename": filename,
                                "method": "ocr_fallback",
                                "employee_count": len(employees),
                                "status": "fallback",
                                "parse_info": parse_info,
                            }
                        )
                    except Exception as exc:
                        failures.append(
                            {
                                "filename": filename,
                                "error": str(exc),
                            }
                        )

        all_rows = [*vision_rows, *conventional_rows]
        deduped_rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in all_rows:
            identity = (
                f"{re.sub('[^0-9]', '', str(row.get('사업장관리번호', '') or ''))}|"
                f"{str(row.get('사업장명', '') or '').strip()}|"
                f"{_clean_review_name(row.get('성명', ''))}|"
                f"{_clean_birth_six(row.get('생년월일6자리', ''))}"
            )
            if identity in seen:
                continue
            seen.add(identity)
            deduped_rows.append(row)

        if not deduped_rows:
            progress.empty()
            st.error("직원행을 만들지 못했습니다.")
            for failure in failures:
                st.caption(
                    f"{failure['filename']}: {failure['error']}"
                )
        else:
            expected_count = int(
                vision_result.get(
                    "total_expected_employee_count",
                    0,
                )
                or sum(
                    int(
                        workplace.get(
                            "expected_employee_count",
                            0,
                        )
                        or 0
                    )
                    for workplace in (
                        vision_result.get("workplaces", []) or []
                    )
                    if isinstance(workplace, dict)
                )
            )
            st.session_state[state_key] = {
                "rows": deduped_rows,
                "expected_count": expected_count,
                "vision_result": vision_result,
                "parse_files": parse_files,
                "failures": failures,
                "filename": (
                    uploaded_files[0].name
                    if len(uploaded_files) == 1
                    else f"AI검수_다중업로드_{len(uploaded_files)}개파일"
                ),
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            progress.progress(1.0, text="AI 분석 완료 · 검수표를 확인해주세요.")
            st.success(
                f"검수 대상 직원 {len(deduped_rows)}명을 추출했습니다. "
                "아래 표를 확인·수정한 뒤 저장하세요."
            )

    pending = st.session_state.get(state_key)
    if isinstance(pending, dict) and pending.get("rows"):
        st.markdown("##### AI 분석 결과 검수")
        expected_count = int(pending.get("expected_count", 0) or 0)
        current_count = len(pending.get("rows", []))

        c1, c2, c3 = st.columns(3)
        c1.metric(
            "문서 예상 인원",
            f"{expected_count}명" if expected_count else "확인필요",
        )
        c2.metric("AI 추출 인원", f"{current_count}명")
        c3.metric(
            "인원 검증",
            (
                "일치"
                if expected_count and expected_count == current_count
                else "검수필요"
            ),
        )

        workplace_summary = _vision_workplace_summary(
            pending.get("vision_result", {}) or {}
        )
        if workplace_summary:
            st.markdown("###### 사업장별 인원 확인")
            st.dataframe(
                pd.DataFrame(workplace_summary),
                hide_index=True,
                use_container_width=True,
            )

        review_df = pd.DataFrame(pending["rows"])

        duplicate_people: dict[str, set[str]] = {}
        for _, duplicate_row in review_df.iterrows():
            person_key = (
                f"{_clean_review_name(duplicate_row.get('성명', ''))}|"
                f"{_clean_birth_six(duplicate_row.get('생년월일6자리', ''))}"
            )
            workplace_key = (
                str(duplicate_row.get("사업장관리번호", "") or "")
                or str(duplicate_row.get("사업장명", "") or "")
            )
            duplicate_people.setdefault(person_key, set()).add(
                workplace_key
            )
        cross_workplace_duplicates = [
            key
            for key, workplaces in duplicate_people.items()
            if key != "|" and len(workplaces) > 1
        ]
        if cross_workplace_duplicates:
            st.warning(
                f"동일 성명·생년월일 직원 {len(cross_workplace_duplicates)}명이 "
                "여러 사업장에 중복되어 있습니다. 실제 중복 재직인지 확인하세요."
            )

        edited_df = st.data_editor(
            review_df,
            hide_index=True,
            use_container_width=True,
            num_rows="dynamic",
            key=f"{state_key}_editor",
            column_config={
                "포함": st.column_config.CheckboxColumn(
                    "포함",
                    help="저장하지 않을 행은 체크를 해제하세요.",
                    default=True,
                ),
                "사업장명": st.column_config.TextColumn(
                    "사업장명",
                    help="본점·지점·공장 등 명부에 적힌 사업장명입니다.",
                ),
                "사업장관리번호": st.column_config.TextColumn(
                    "사업장관리번호",
                    help="명부 상단의 사업장 관리번호입니다.",
                ),
                "연번": st.column_config.NumberColumn(
                    "연번",
                    min_value=1,
                    step=1,
                ),
                "성명": st.column_config.TextColumn(
                    "성명",
                    help="원본 명부의 성명 열과 비교하세요.",
                ),
                "생년월일6자리": st.column_config.TextColumn(
                    "생년월일6자리",
                    help="주민등록번호 앞 6자리만 입력합니다.",
                ),
            },
        )

        included_count = int(
            edited_df["포함"].fillna(False).astype(bool).sum()
            if "포함" in edited_df.columns
            else len(edited_df)
        )
        mismatch = bool(
            expected_count and included_count != expected_count
        )
        if mismatch:
            st.warning(
                f"문서 예상 인원은 {expected_count}명인데, "
                f"현재 저장 대상은 {included_count}명입니다."
            )
            mismatch_confirmed = st.checkbox(
                "인원 차이를 확인했으며 현재 검수표대로 저장합니다.",
                key=f"{state_key}_mismatch_confirm",
            )
        else:
            mismatch_confirmed = True

        failures = pending.get("failures", []) or []
        if failures:
            with st.expander("읽지 못한 파일·페이지 확인", expanded=False):
                for failure in failures:
                    st.write(
                        f"- {failure.get('filename', '')}: "
                        f"{failure.get('error', '')}"
                    )

        col_save, col_cancel = st.columns([3, 1])
        with col_save:
            save_clicked = st.button(
                "검수 완료 후 직원현황 저장",
                type="primary",
                use_container_width=True,
                disabled=not mismatch_confirmed,
                key=f"{state_key}_save",
            )
        with col_cancel:
            cancel_clicked = st.button(
                "검수 취소",
                use_container_width=True,
                key=f"{state_key}_cancel",
            )

        if cancel_clicked:
            st.session_state.pop(state_key, None)
            st.rerun()

        if save_clicked:
            employees, errors = _review_rows_to_employees(edited_df)
            if errors:
                st.error("검수표를 저장할 수 없습니다.")
                for error in errors[:20]:
                    st.write(f"- {error}")
            elif not employees:
                st.error("저장할 직원이 없습니다.")
            else:
                parse_info = {
                    "method": "ai_vision_human_review",
                    "reviewed": True,
                    "expected_employee_count": expected_count,
                    "saved_employee_count": len(employees),
                    "vision_result": pending.get("vision_result", {}),
                    "files": pending.get("parse_files", []),
                    "failed_files": failures,
                    "reviewed_at": datetime.now().isoformat(
                        timespec="seconds"
                    ),
                }
                _, message = save_employee_roster(
                    user_id,
                    business_no,
                    company_name,
                    pending.get("filename", "AI비전_가입자명부"),
                    employees,
                    parse_info,
                )
                st.session_state.pop(state_key, None)
                st.success(
                    f"검수 완료한 직원 {len(employees)}명을 "
                    f"{message}했습니다."
                )
                st.rerun()

    latest = get_latest_employee_status(
        user_id,
        business_no,
        company_name,
    )
    if not latest:
        st.info("저장된 직원현황이 없습니다.")
        return

    summary = latest.get("summary", {}) or {}
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("가입중", f"{summary.get('active_count', 0)}명")
    c2.metric(
        "최근 6개월 취득",
        f"{summary.get('recent_6m_count', 0)}명",
    )
    c3.metric("청년 추정", f"{summary.get('youth_count', 0)}명")
    c4.metric("고령자 추정", f"{summary.get('senior_count', 0)}명")

    st.caption(
        f"최근 업로드: {str(latest.get('uploaded_at', ''))[:19]} · "
        f"파일: {latest.get('filename', '-')}"
    )

    employee_df = _display_employee_rows(
        latest.get("employees", []) or []
    )
    if not employee_df.empty:
        st.dataframe(
            employee_df,
            hide_index=True,
            use_container_width=True,
        )

    render_employment_support_analysis(
        user_id=user_id,
        business_no=business_no,
        company_name=company_name,
        latest=latest,
    )

    versions = load_employee_versions(
        user_id,
        business_no,
        company_name,
    )
    if len(versions) > 1:
        with st.expander(
            f"이전 명부 업로드 이력 {len(versions) - 1}건",
            expanded=False,
        ):
            history_rows = [
                {
                    "업로드일": str(item.get("uploaded_at", ""))[:19],
                    "파일": item.get("filename", ""),
                    "가입중": item.get("summary", {}).get(
                        "active_count",
                        0,
                    ),
                    "최근6개월취득": item.get("summary", {}).get(
                        "recent_6m_count",
                        0,
                    ),
                }
                for item in versions
            ]
            st.dataframe(
                pd.DataFrame(history_rows),
                hide_index=True,
                use_container_width=True,
            )

