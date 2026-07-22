from __future__ import annotations

import hashlib
import io
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

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

    if suffix in {".pdf", ".jpg", ".jpeg", ".png"}:
        text, extraction = preprocess_document(
            filename,
            data,
            progress_callback=progress_callback,
        )
        employees = _parse_pdf_text(text)
        return employees, {
            "method": (
                "pdf_ocr"
                if suffix == ".pdf"
                else "image_ocr"
            ),
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
        "XLSX·CSV·PDF·JPG·JPEG·PNG 형식을 지원합니다. "
        "4대보험 가입자명부의 주민등록번호는 저장하지 않으며, "
        "직원명은 마스킹하고 생년·자격취득일·근속기간만 고용지원금 매칭에 활용합니다."
    )

    uploaded = st.file_uploader(
        "4대보험 가입자명부 업로드",
        type=["xlsx", "xls", "csv", "pdf", "jpg", "jpeg", "png"],
        key=f"employee_roster_upload_{business_no or company_name}",
    )

    if uploaded is not None and st.button(
        "직원현황 분석·저장",
        type="primary",
        use_container_width=True,
        key=f"employee_roster_analyze_{business_no or company_name}",
    ):
        progress = st.progress(
            0,
            text="4대보험 가입자명부를 확인하고 있습니다.",
        )

        def update_progress(
            current: int,
            total: int,
            message: str = "",
        ) -> None:
            progress.progress(
                min(current / max(total, 1), 1.0),
                text=message or "직원명부 분석 중",
            )

        try:
            employees, parse_info = parse_roster(
                uploaded.name,
                uploaded.getvalue(),
                progress_callback=update_progress,
            )
            if not employees:
                raise ValueError(
                    "직원 행을 찾지 못했습니다. "
                    "성명·자격취득일이 표시된 원본 명부인지 확인해주세요."
                )

            _, message = save_employee_roster(
                user_id,
                business_no,
                company_name,
                uploaded.name,
                employees,
                parse_info,
            )
            progress.progress(1.0, text="직원현황 저장 완료")
            st.success(
                f"직원 {len(employees)}명을 분석해 {message}했습니다."
            )
            st.rerun()
        except Exception as exc:
            st.error(f"직원현황 분석 실패: {exc}")

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
