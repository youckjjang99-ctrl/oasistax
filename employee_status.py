from __future__ import annotations

import hashlib
import io
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pandas as pd
import streamlit as st
import pytesseract
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from cloud_db import CloudDatabase, cloud_is_configured
from customer_history import save_customer_event
from document_preprocessor import preprocess_document
from utils import get_user_dirs

TABLE_EMPLOYEE_ROSTERS = "oasis_employee_rosters"

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
            "--oem 1 --psm 7",
            "--oem 1 --psm 8",
            "--oem 1 --psm 6",
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
        41,
        13,
    )

    horizontal_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (max(gray.shape[1] // 18, 35), 1),
    )
    vertical_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (1, max(gray.shape[0] // 35, 25)),
    )
    horizontal = cv2.morphologyEx(
        binary,
        cv2.MORPH_OPEN,
        horizontal_kernel,
        iterations=1,
    )
    vertical = cv2.morphologyEx(
        binary,
        cv2.MORPH_OPEN,
        vertical_kernel,
        iterations=1,
    )
    grid = cv2.bitwise_or(horizontal, vertical)

    contours, _ = cv2.findContours(
        grid,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    candidates = []
    image_area = gray.shape[0] * gray.shape[1]
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        area = width * height
        if (
            width >= gray.shape[1] * 0.55
            and height >= gray.shape[0] * 0.12
            and area >= image_area * 0.08
        ):
            candidates.append((area, x, y, width, height))

    if not candidates:
        return None, [], []

    _, x, y, width, height = max(candidates)
    crop_horizontal = horizontal[y:y + height, x:x + width]
    crop_vertical = vertical[y:y + height, x:x + width]

    horizontal_strength = np.sum(crop_horizontal > 0, axis=1)
    vertical_strength = np.sum(crop_vertical > 0, axis=0)

    y_values = np.where(
        horizontal_strength >= max(width * 0.35, 30)
    )[0].tolist()
    x_values = np.where(
        vertical_strength >= max(height * 0.25, 25)
    )[0].tolist()

    y_lines = _cluster_line_positions(y_values, tolerance=7)
    x_lines = _cluster_line_positions(x_values, tolerance=7)

    y_lines = [
        value for value in y_lines
        if 0 <= value < height
    ]
    x_lines = [
        value for value in x_lines
        if 0 <= value < width
    ]
    return (x, y, width, height), x_lines, y_lines


def _select_roster_columns(
    x_lines: list[int],
    width: int,
) -> list[int]:
    usable = sorted(
        value
        for value in x_lines
        if 0 <= value <= width
    )
    if len(usable) >= 8:
        # The roster table normally has 8 vertical boundaries:
        # serial, RRN, name and four insurance-date columns.
        best: list[int] | None = None
        best_score = float("inf")
        expected = [0.0, 0.10, 0.31, 0.46, 0.595, 0.73, 0.865, 1.0]
        for start in range(0, len(usable) - 7):
            candidate = usable[start:start + 8]
            span = candidate[-1] - candidate[0]
            if span < width * 0.65:
                continue
            ratios = [
                (value - candidate[0]) / max(span, 1)
                for value in candidate
            ]
            score = sum(
                abs(actual - target)
                for actual, target in zip(ratios, expected)
            )
            if score < best_score:
                best = candidate
                best_score = score
        if best is not None:
            return best

    # Safe proportional fallback for the standardized 4-insurance form.
    return [
        int(width * ratio)
        for ratio in (0.0, 0.10, 0.31, 0.46, 0.595, 0.73, 0.865, 1.0)
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

    return list(deduped.values()), {
        "method": "cell_grid_ocr",
        "grid_found": True,
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
        "XLSX·CSV·PDF·JPG·JPEG·PNG 파일을 여러 개 동시에 업로드할 수 있습니다. "
        "여러 페이지·파일에서 확인된 직원은 하나의 명부로 합치고 중복을 제거합니다. "
        "4대보험 가입자명부의 주민등록번호는 저장하지 않으며, "
        "직원명은 마스킹하고 생년·자격취득일·근속기간만 고용지원금 매칭에 활용합니다."
    )

    uploaded_files = st.file_uploader(
        "4대보험 가입자명부 업로드",
        type=["xlsx", "xls", "csv", "pdf", "jpg", "jpeg", "png"],
        accept_multiple_files=True,
        key=f"employee_roster_upload_{business_no or company_name}",
        help=(
            "가입자명부가 여러 장이면 모든 이미지 또는 파일을 함께 선택하세요. "
            "분석 후 직원정보를 하나로 합칩니다."
        ),
    )

    if uploaded_files and st.button(
        "선택한 파일 전체 분석·저장",
        type="primary",
        use_container_width=True,
        key=f"employee_roster_analyze_{business_no or company_name}",
    ):
        total_files = len(uploaded_files)
        progress = st.progress(
            0,
            text=f"가입자명부 {total_files}개 파일을 확인하고 있습니다.",
        )

        merged_employees: dict[str, dict[str, Any]] = {}
        parse_files: list[dict[str, Any]] = []
        failed_files: list[dict[str, str]] = []

        for file_index, uploaded in enumerate(uploaded_files):
            file_base = file_index / max(total_files, 1)
            file_share = 1 / max(total_files, 1)

            def update_progress(
                current: int,
                total: int,
                message: str = "",
                *,
                _base: float = file_base,
                _share: float = file_share,
                _name: str = uploaded.name,
                _index: int = file_index,
            ) -> None:
                inner_ratio = min(
                    max(current / max(total, 1), 0.0),
                    1.0,
                )
                overall = min(_base + inner_ratio * _share, 1.0)
                progress.progress(
                    overall,
                    text=(
                        f"{_index + 1}/{total_files} {_name} · "
                        f"{message or '직원명부 분석 중'}"
                    ),
                )

            try:
                employees, parse_info = parse_roster(
                    uploaded.name,
                    uploaded.getvalue(),
                    progress_callback=update_progress,
                )
                if not employees:
                    raise ValueError(
                        "성명·자격취득일이 표시된 직원 행을 찾지 못했습니다."
                    )

                for employee in employees:
                    employee_id = str(
                        employee.get("employee_id", "")
                    ).strip()
                    if not employee_id:
                        continue
                    merged_employees[employee_id] = employee

                parse_files.append(
                    {
                        "filename": uploaded.name,
                        "employee_count": len(employees),
                        "status": "success",
                        "parse_info": parse_info,
                    }
                )
                progress.progress(
                    min((file_index + 1) / total_files, 1.0),
                    text=(
                        f"{file_index + 1}/{total_files} "
                        f"{uploaded.name} 분석 완료"
                    ),
                )
            except Exception as exc:
                failed_files.append(
                    {
                        "filename": uploaded.name,
                        "error": str(exc),
                    }
                )
                parse_files.append(
                    {
                        "filename": uploaded.name,
                        "employee_count": 0,
                        "status": "failed",
                        "error": str(exc),
                    }
                )
                progress.progress(
                    min((file_index + 1) / total_files, 1.0),
                    text=(
                        f"{file_index + 1}/{total_files} "
                        f"{uploaded.name} 분석 실패 · 다음 파일 계속"
                    ),
                )

        employees = list(merged_employees.values())
        if not employees:
            st.error(
                "선택한 파일에서 직원정보를 찾지 못했습니다. "
                "Railway에 Tesseract 한국어 OCR이 설치된 새 배포인지 확인하고, "
                "문서가 선명하고 표 전체가 보이는지 확인해주세요."
            )
            for failure in failed_files:
                st.caption(
                    f"실패: {failure['filename']} · {failure['error']}"
                )
        else:
            combined_filename = (
                uploaded_files[0].name
                if total_files == 1
                else f"다중업로드_{total_files}개파일"
            )
            parse_info = {
                "method": "multi_file_merge",
                "file_count": total_files,
                "success_count": len(parse_files) - len(failed_files),
                "failed_count": len(failed_files),
                "files": parse_files,
                "deduplicated_employee_count": len(employees),
            }

            _, message = save_employee_roster(
                user_id,
                business_no,
                company_name,
                combined_filename,
                employees,
                parse_info,
            )
            progress.progress(1.0, text="통합 직원현황 저장 완료")
            st.success(
                f"{total_files}개 파일에서 중복을 제거한 직원 "
                f"{len(employees)}명을 분석해 {message}했습니다."
            )

            preview_df = _display_employee_rows(employees)
            if not preview_df.empty:
                with st.expander(
                    f"이번 업로드 인식 결과 {len(employees)}명 확인",
                    expanded=True,
                ):
                    st.dataframe(
                        preview_df,
                        hide_index=True,
                        use_container_width=True,
                    )

            if len(employees) < 5:
                st.warning(
                    "인식된 직원 수가 적습니다. 문서의 실제 인원과 "
                    "아래 인식 결과를 반드시 비교해주세요."
                )

            if failed_files:
                st.warning(
                    f"{len(failed_files)}개 파일은 읽지 못했지만 "
                    "나머지 파일의 직원현황은 정상 저장했습니다."
                )
                with st.expander(
                    "읽지 못한 파일과 오류 확인",
                    expanded=True,
                ):
                    for failure in failed_files:
                        st.write(
                            f"- {failure['filename']}: "
                            f"{failure['error']}"
                        )

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

    st.markdown("##### 고용지원금 매칭 활용정보")
    matching_points = [
        f"현재 가입중 직원 {summary.get('active_count', 0)}명",
        f"최근 3개월 자격취득 {summary.get('recent_3m_count', 0)}명",
        f"최근 6개월 자격취득 {summary.get('recent_6m_count', 0)}명",
        f"청년 연령구간 추정 {summary.get('youth_count', 0)}명",
        f"중장년 연령구간 추정 {summary.get('middle_aged_count', 0)}명",
        f"고령자 연령구간 추정 {summary.get('senior_count', 0)}명",
        f"2년 이상 장기근속 {summary.get('long_tenure_count', 0)}명",
    ]
    for point in matching_points:
        st.write(f"- {point}")

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
