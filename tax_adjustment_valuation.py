"""Conservative, evidence-bearing inputs for inheritance-tax share valuation.

Extraction produces review candidates, never an approved assessment. A tax base,
accounting profit, or unidentified year must not become statutory net income.
"""
from __future__ import annotations

from copy import deepcopy
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any


LAW_BASE_DATE = "2026-10-22"
LAW_EFFECTIVE_DATE = "2026-10-01"
LAW_URL = "https://www.law.go.kr/법령/상속세및증여세법시행령/제56조"
RULE_URL = "https://www.law.go.kr/법령/상속세및증여세법시행규칙/제17조의3"
COMPONENTS = (
    {"key": "income", "label": "각 사업연도 소득금액", "sign": 1, "legal_ref": "법인세법 제14조 / 시행령 제56조제4항"},
    {"key": "reserve_allocation", "label": "충당금·준비금 일시환입 안분 가산액", "sign": 1, "legal_ref": "시행령 제56조제4항 후단"},
    {"key": "add_refunded_tax", "label": "국세·지방세 과오납금 환급가산금(이자)", "sign": 1, "legal_ref": "시행령 제56조제4항제1호가목 / 법인세법 제18조제4호"},
    {"key": "add_dividend_exclusion", "label": "수입배당금 익금불산입액", "sign": 1, "legal_ref": "시행령 제56조제4항제1호나목"},
    {"key": "add_prior_donation", "label": "기부금 등 해당 연도 손금산입 가산대상액", "sign": 1, "legal_ref": "시행령 제56조제4항제1호다목"},
    {"key": "add_fx_gain", "label": "미평가 화폐성외화자산 등 평가이익", "sign": 1, "legal_ref": "시행령 제56조제4항제1호라목"},
    {"key": "add_other", "label": "그 밖의 부령상 가산대상액", "sign": 1, "legal_ref": "시행령 제56조제4항제1호마목"},
    {"key": "deduct_taxes", "label": "법인세액·감면액·농특세·지방소득세 등 차감 합계", "sign": -1, "legal_ref": "시행령 제56조제4항제2호가목"},
    {"key": "deduct_nondeductible_expenses", "label": "제21조 등 손금불산입액 및 징수불이행 세액", "sign": -1, "legal_ref": "시행령 제56조제4항제2호나목"},
    {"key": "deduct_limits_interest", "label": "기부금·기업업무추진비·지급이자 등 한도 차감액", "sign": -1, "legal_ref": "시행령 제56조제4항제2호다목"},
    {"key": "deduct_depreciation_gap", "label": "감가상각 시인부족액 − 상각부인 추인액", "sign": -1, "legal_ref": "시행령 제56조제4항제2호라목"},
    {"key": "deduct_fx_loss", "label": "미평가 화폐성외화자산 등 평가손실", "sign": -1, "legal_ref": "시행령 제56조제4항제2호마목"},
    {"key": "capital_adjustment", "label": "유상증자·감자 순손익 조정액(가산 + / 차감 −)", "sign": 1, "legal_ref": "시행령 제56조제5항 / 시행규칙 제17조의3"},
)
_LABELS = {
    "income": ("각사업연도소득금액", "각사업연도소득"),
    "reserve_allocation": ("충당금준비금일시환입안분가산액",),
    "add_refunded_tax": ("국세지방세과오납금환급금에대한이자", "법인세법제18조제4호금액"),
    "add_dividend_exclusion": ("수입배당금익금불산입액",),
    "add_prior_donation": ("기부금등손금산입가산대상액",),
    "add_fx_gain": ("미평가화폐성외화자산등평가이익",),
    "add_other": ("부령상가산대상액",),
    "deduct_taxes": ("법인세액감면액농특세지방소득세등차감합계",),
    "deduct_nondeductible_expenses": ("제21조등손금불산입액및징수불이행세액",),
    "deduct_limits_interest": ("기부금기업업무추진비지급이자등한도차감액",),
    "deduct_depreciation_gap": ("감가상각시인부족액상각부인추인액차이",),
    "deduct_fx_loss": ("미평가화폐성외화자산등평가손실",),
    "capital_adjustment": ("유상증자감자순손익조정액",),
}
_FINANCIAL_LABELS = {label: (label,) for label in (
    "당기순이익", "자산총계", "부채총계", "자본총계", "과세표준", "산출세액",
    "결정세액", "세액공제", "연구개발비", "법인세비용", "법인세액", "지방소득세액",
)}
_UNIT = re.compile(r"단위\s*[:：]?\s*(억원|백만원|천원|원)")
_MONEY = re.compile(r"(?<![\d.])([-+−△▲]?\s*\(?\s*\d[\d,]*(?:\.\d+)?\s*\)?)\s*(억원|백만원|천원|원)?")
_DATE_TOKEN = r"((?:19|20)\d{2})\s*[.년/\-]\s*(\d{1,2})\s*[.월/\-]\s*(\d{1,2})\s*일?\.?"
_PERIOD_LABEL = r"사\s*업\s*연\s*도"
_RANGE_SEPARATOR = r"(?:~|∼|〜|－|–|—|부터|-)"
_RANGE = re.compile(_PERIOD_LABEL + r"\s*[:：]?\s*" + _DATE_TOKEN + r"\s*" + _RANGE_SEPARATOR + r"\s*" + _DATE_TOKEN)
# PDF form cells can lose their range separator, or put the period label
# between two date-only cells. Keep the label adjacent: unrelated filing and
# amendment dates elsewhere in a document are never fiscal-period evidence.
_CELL_RANGE = re.compile(_PERIOD_LABEL + r"\s*[:：]?\s*" + _DATE_TOKEN + r"[\s|]+" + _DATE_TOKEN)
_SPLIT_LABEL_RANGE = re.compile(
    r"(?m)^[ \t]*" + _DATE_TOKEN + r"[ \t]*(?:\r?\n[ \t]*)"
    + _PERIOD_LABEL + r"\s*[:：]?\s*(?:" + _RANGE_SEPARATOR + r"\s*)?" + _DATE_TOKEN
)
_YEAR = re.compile(
    r"(?m)^\s*(?:(?P<before>(?:19|20)\d{2})\s*(?:년\s*)?" + _PERIOD_LABEL
    + r"|" + _PERIOD_LABEL + r"\s*[:：]?\s*(?P<after>(?:19|20)\d{2})(?:\s*년)?"
    r"(?=\s*(?:$|[,:;]|법인세|세무|조정)))"
)
_ATTRIBUTION_YEAR = re.compile(
    r"(?m)^[ \t]*(?P<year>(?:19|20)\d{2})\s*년\s*귀\s*속"
    r"(?=[ \t]*(?:$|법인세|세무\s*조정|조정\s*계산서))"
)
_BUSINESS = re.compile(r"사업자\s*등록\s*번호\s*[:：]?\s*(\d{3})\s*-?\s*(\d{2})\s*-?\s*(\d{5})(?!\d)")


def _won(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    raw = str(value).strip().replace(",", "").replace(" ", "")
    if raw in {"", "-", "미확인", "None"}:
        return None
    negative = raw.startswith(("(", "△", "▲", "−"))
    raw = raw.strip("()△▲").replace("−", "-")
    try:
        number = Decimal(raw)
    except InvalidOperation:
        return None
    if not number.is_finite() or number != number.to_integral_value():
        return None
    return -abs(int(number)) if negative else int(number)


def calculate_tax_adjusted_income(values: dict[str, Any]) -> dict[str, Any]:
    """No inferred zeroes; preserve an annual loss before the later 3:2:1 step."""
    parsed = {item["key"]: _won(values.get(item["key"])) for item in COMPONENTS}
    signed_keys = {"income", "deduct_depreciation_gap", "capital_adjustment"}
    invalid = [key for key, value in parsed.items() if value is not None and value < 0 and key not in signed_keys]
    for key in invalid:
        parsed[key] = None
    missing = [key for key, value in parsed.items() if value is None]
    return {
        "complete": not missing,
        "adjusted_net_income": (sum(parsed[item["key"]] * item["sign"] for item in COMPONENTS) if not missing else None),
        "missing": missing,
        "invalid": invalid,
        "components": [{**item, "value": parsed[item["key"]]} for item in COMPONENTS],
        "law_base_date": LAW_BASE_DATE,
        "law_url": LAW_URL,
        "warnings": ["1년 미만 사업연도 연환산, 증감자 및 주식수 환산은 별도 검토가 필요합니다."] + (["가산·차감 대상 금액의 음수 부호를 확인하세요."] if invalid else []),
    }


def _label_pattern(label: str) -> str:
    return r"\s*".join(re.escape(char) for char in label)


def _extract_fields(text: str, labels: dict[str, tuple[str, ...]], *, initial_unit: str = "") -> tuple[dict, dict, list]:
    found: dict[str, list[tuple[int, dict]]] = {}
    ambiguous: set[str] = set()
    unit = initial_unit
    for line_index, line in enumerate(text.splitlines()):
        declared = _UNIT.search(line)
        if declared:
            unit = declared.group(1)
        for key, aliases in labels.items():
            match = next((match for alias in aliases if (match := re.search(_label_pattern(alias), line))), None)
            if not match:
                continue
            tail = line[match.end():].strip(" :：|=")
            # A parenthesised form field number is not a negative money amount.
            tail = re.sub(r"^\(\d{1,3}\)\s*(?=[-+△▲−\d])", "", tail)
            matches = list(_MONEY.finditer(tail))
            if len(matches) != 1:
                ambiguous.add(key)
                continue
            token = matches[0]
            amount_unit = token.group(2) or unit
            if not amount_unit:
                ambiguous.add(key)
                continue
            raw = token.group(1).strip()
            numeric_token = re.sub(r"[-+−△▲()\s]", "", raw)
            if not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", numeric_token):
                ambiguous.add(key)
                continue
            negative = raw.startswith(("(", "△", "▲", "−", "-"))
            digits = re.sub(r"[^\d.]", "", raw)
            try:
                number = Decimal(digits) * {"원": 1, "천원": 1_000, "백만원": 1_000_000, "억원": 100_000_000}[amount_unit]
            except InvalidOperation:
                ambiguous.add(key)
                continue
            if not number.is_finite() or number != number.to_integral_value():
                ambiguous.add(key)
                continue
            amount = int(number) * (-1 if negative else 1)
            found.setdefault(key, []).append((amount, {"line": line_index + 1, "unit": amount_unit, "label": match.group(0)}))
    values, evidence = {}, {}
    for key, occurrences in found.items():
        amounts = {amount for amount, _ in occurrences}
        if len(amounts) != 1:
            ambiguous.add(key)
        elif key not in ambiguous:
            values[key] = occurrences[0][0]
            evidence[key] = [detail for _, detail in occurrences]
    return values, evidence, sorted(ambiguous)


def _periods(text: str) -> list[dict]:
    markers = []
    ranges = []
    for pattern in (_RANGE, _CELL_RANGE, _SPLIT_LABEL_RANGE):
        for match in pattern.finditer(text):
            if not any(existing.start() < match.end() and match.start() < existing.end()
                       for existing in ranges):
                ranges.append(match)
    for match in ranges:
        try:
            start = date(*map(int, match.groups()[:3]))
            end = date(*map(int, match.groups()[3:]))
        except ValueError:
            continue
        if start > end:
            continue
        markers.append({"offset": match.start(), "year": end.year, "period_start": start.isoformat(), "period_end": end.isoformat()})
    for match in _YEAR.finditer(text):
        if any(item.start() <= match.start() < item.end() for item in ranges):
            continue
        markers.append({"offset": match.start(), "year": int(match.group("before") or match.group("after")), "period_start": "", "period_end": ""})
    for match in _ATTRIBUTION_YEAR.finditer(text):
        if any(item.start() <= match.start() < item.end() for item in ranges):
            continue
        markers.append({"offset": match.start(), "year": int(match.group("year")),
                        "period_start": "", "period_end": ""})
    markers.sort(key=lambda item: item["offset"])
    return markers


def analyze_tax_adjustment_text(text: str) -> dict[str, Any]:
    source = unicodedata.normalize("NFKC", str(text or "")).replace("\u200b", "")
    financial, _, _ = _extract_fields(source, _FINANCIAL_LABELS)
    markers = _periods(source)
    declared_units = list(_UNIT.finditer(source))
    unit_names = {item.group(1) for item in declared_units}
    periods = []
    for index, marker in enumerate(markers):
        # A lone fiscal period may head any page in the document.
        section = source if len(markers) == 1 else source[marker["offset"]:(markers[index + 1]["offset"] if index + 1 < len(markers) else len(source))]
        prior_units = [item for item in declared_units if item.end() <= marker["offset"]]
        prior_unit = ""
        if prior_units:
            last_unit = prior_units[-1]
            adjacent = bool(re.fullmatch(r"[\s)\]〉》]*", source[last_unit.end():marker["offset"]]))
            global_header = len(unit_names) == 1 and declared_units[0].end() <= markers[0]["offset"] and bool(re.fullmatch(r"[\s)\]〉》]*", source[declared_units[0].end():markers[0]["offset"]]))
            if adjacent or global_header:
                prior_unit = last_unit.group(1)
        values, evidence, ambiguous = _extract_fields(section, _LABELS, initial_unit=prior_unit)
        facts, fact_evidence, fact_ambiguous = _extract_fields(section, _FINANCIAL_LABELS, initial_unit=prior_unit)
        periods.append({
            **{key: value for key, value in marker.items() if key != "offset"},
            "values": values, "financial_fields": facts,
            "evidence": evidence, "financial_evidence": fact_evidence,
            "ambiguous_fields": sorted(set(ambiguous + fact_ambiguous)),
        })
    business_numbers = sorted({"-".join(match.groups()) for match in _BUSINESS.finditer(source)})
    recognized = bool(re.search(r"세무\s*조정|법인세[^\n]{0,30}신고|조정\s*계산서", source))
    return {
        "recognized": recognized,
        "years": sorted({str(item["year"]) for item in periods}, reverse=True),
        "business_numbers": business_numbers,
        "business_no": business_numbers[0] if len(business_numbers) == 1 else "",
        "financial_fields": financial,
        "valuation_periods": periods,
        "summary": f"재무·세무 항목 {len(financial)}개, 사업연도 후보 {len({item['year'] for item in periods})}개를 확인했습니다. 원문 검토 후 반영하세요.",
        "warnings": ([] if periods else ["사업연도를 특정하지 못해 주가평가에 자동 반영하지 않습니다."]),
        "law_base_date": LAW_BASE_DATE,
    }


def _tax_record_identity(record: dict) -> tuple[bool, bool]:
    """Share the same legacy identity boundary across discovery and recovery."""
    if not isinstance(record, dict) or record.get("document_type") != "tax_adjustment":
        return False, False
    analysis = record.get("extracted_fields") or {}
    if not isinstance(analysis, dict):
        return False, False
    declared_numbers = analysis.get("business_numbers") or []
    if not isinstance(declared_numbers, (list, tuple)):
        declared_numbers = [declared_numbers]
    declared_numbers = [*declared_numbers, analysis.get("business_no", "")]
    declared_numbers = [str(value).strip() for value in declared_numbers if str(value or "").strip()]
    identity_numbers = {
        digits for value in declared_numbers
        if len(digits := re.sub(r"\D", "", value)) == 10
    }
    record_identity = re.sub(r"\D", "", str(record.get("business_no", "")))
    # Older attachments may have been linked incorrectly before upload
    # identity checks existed. Never reuse an explicitly different entity.
    valid = not declared_numbers or (len(record_identity) == 10 and record_identity in identity_numbers)
    return valid, len(identity_numbers) > 1


def _tax_period_year(period: Any) -> int | None:
    if not isinstance(period, dict):
        return None
    try:
        year = int(period["year"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return year if 1900 <= year <= 2100 else None


def unrecognized_tax_records(records: list[dict]) -> list[dict]:
    """Expose stored originals needing a confirmed period, not guessed years.

    Historical ``years`` values were collected from arbitrary document dates.
    They must not silently become approved years or legal-income amounts.
    The caller remains responsible for the owner/company-scoped record query.
    """
    results = []
    for record in records:
        valid, _ = _tax_record_identity(record)
        if not valid:
            continue
        periods = (record.get("extracted_fields") or {}).get("valuation_periods") or []
        if not isinstance(periods, (list, tuple)):
            periods = []
        if not any(_tax_period_year(period) is not None for period in periods):
            results.append(deepcopy(record))
    return results


def reviewed_tax_period_record(
    record: dict, year: int | str, period_start: str, period_end: str,
) -> dict:
    """Build a separately reviewed copy without inferring any legal amounts.

    This pure function does not save, change the original analysis, or infer a
    calendar-year range from a filename/legacy year list. Its caller must obtain
    explicit original/period confirmation and preserve the prior analysis version.
    """
    valid, _ = _tax_record_identity(record)
    if not valid or not str(record.get("record_id") or "").strip():
        raise ValueError("기업에 연결된 원본 자료의 식별정보를 확인하지 못했습니다.")
    if not unrecognized_tax_records([record]):
        raise ValueError("이미 사업연도가 확인된 자료는 이 복구 기능으로 변경할 수 없습니다.")
    try:
        confirmed_year = int(str(year).strip())
        if not all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value))
                   for value in (period_start, period_end)):
            raise ValueError("not an explicit ISO calendar date")
        start = date.fromisoformat(str(period_start))
        end = date.fromisoformat(str(period_end))
    except (TypeError, ValueError):
        raise ValueError("사업연도와 원문에서 확인한 시작일·종료일을 YYYY-MM-DD로 입력해주세요.") from None
    if not 1900 <= confirmed_year <= 2100 or start > end or end.year != confirmed_year:
        raise ValueError("사업연도는 종료일의 연도와 같아야 하며 시작일·종료일 순서를 확인해주세요.")
    restored = deepcopy(record)
    analysis = deepcopy(restored.get("extracted_fields") or {})
    analysis["valuation_periods"] = [{
        "year": confirmed_year, "period_start": start.isoformat(), "period_end": end.isoformat(),
        "values": {}, "financial_fields": {}, "evidence": {}, "financial_evidence": {},
        "ambiguous_fields": [], "manual_period_review": True,
    }]
    restored["extracted_fields"] = analysis
    return restored


def build_tax_adjustment_candidates(records: list[dict], evaluation_date: date | str) -> list[dict]:
    """Merge same-year corroboration; leave differing values unresolved."""
    as_of = evaluation_date if isinstance(evaluation_date, date) else date.fromisoformat(str(evaluation_date)[:10])
    grouped: dict[int, list[tuple[dict, dict]]] = {}
    for record in records:
        valid, identity_review = _tax_record_identity(record)
        if not valid:
            continue
        analysis = record.get("extracted_fields") or {}
        record = {**record, "_tax_identity_review_required": identity_review}
        periods = analysis.get("valuation_periods") or []
        if not isinstance(periods, (list, tuple)):
            continue
        for period in periods:
            if not isinstance(period, dict):
                continue
            year = _tax_period_year(period)
            if year is not None:
                grouped.setdefault(year, []).append((record, period))
    results = []
    for year, items in sorted(grouped.items(), reverse=True):
        values, facts, conflicts, field_sources = {}, {}, [], {}
        for group_name, target, keys in (
            ("values", values, [item["key"] for item in COMPONENTS]),
            ("financial_fields", facts, list(_FINANCIAL_LABELS)),
        ):
            for key in keys:
                observed = {_won(period.get(group_name, {}).get(key)) for _, period in items}
                observed.discard(None)
                field_sources[key] = [
                    {"record_id": item.get("record_id", ""), "filename": item.get("filename", ""),
                     "evidence": period.get("evidence" if group_name == "values" else "financial_evidence", {}).get(key, [])}
                    for item, period in items if _won(period.get(group_name, {}).get(key)) is not None
                ]
                if len(observed) > 1 or any(key in period.get("ambiguous_fields", []) for _, period in items):
                    target[key] = None
                    conflicts.append(key)
                else:
                    target[key] = next(iter(observed), None)
        starts = {period.get("period_start") for _, period in items if period.get("period_start")}
        ends = {period.get("period_end") for _, period in items if period.get("period_end")}
        if len(starts) > 1 or len(ends) > 1:
            conflicts.append("fiscal_period")
        start, end = (next(iter(starts), "") if len(starts) <= 1 else ""), (next(iter(ends), "") if len(ends) <= 1 else "")
        verified = bool(start and end and "fiscal_period" not in conflicts)
        short_period = False
        valid_dates = True
        if verified:
            try:
                start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
                days = (end_date - start_date).days + 1
                valid_dates = 0 < days <= 366 and end_date.year == year
                short_period = days < 365
            except ValueError:
                valid_dates = False
        legal_review = not (date.fromisoformat(LAW_EFFECTIVE_DATE) <= as_of <= date.fromisoformat(LAW_BASE_DATE))
        eligible = valid_dates and (end < as_of.isoformat() if end else year < as_of.year)
        warnings = ["세무조정 원문과 구성항목, 연도별 주식수 및 증감자 여부를 확인해야 합니다."]
        identity_review = any(record.get("_tax_identity_review_required") for record, _ in items)
        if identity_review:
            warnings.append("문서에서 여러 사업자 식별값이 확인돼 선택 기업의 자료인지 원문 확인이 필요합니다.")
        if not verified:
            warnings.append("정확한 사업연도 시작·종료일이 확인되지 않았습니다.")
        if short_period:
            warnings.append("1년 미만 사업연도이므로 연환산을 별도 검토해야 합니다.")
        if legal_review:
            warnings.append("평가기준일의 법령이 현재 확인 범위 밖이므로 별도 법령 검토가 필요합니다.")
        manually_reviewed = any(period.get("manual_period_review") for _, period in items)
        if manually_reviewed:
            warnings.append("사업연도 기간을 원문 대조 후 직접 확인한 자료입니다. 법정 금액은 별도로 입력·검토해야 합니다.")
        results.append({
            "year": year, "period_start": start, "period_end": end,
            "period_verified": verified and valid_dates, "short_period": short_period,
            "identity_review_required": identity_review,
            "legal_review_required": legal_review, "values": values,
            "financial_fields": facts, "field_sources": field_sources, "conflicts": sorted(set(conflicts)),
            "sources": [{"record_id": item.get("record_id", ""), "filename": item.get("filename", ""), "uploaded_at": item.get("uploaded_at", ""),
                         **({"manual_period_review": True} if period.get("manual_period_review") else {})}
                        for item, period in items],
            "missing": calculate_tax_adjusted_income(values)["missing"],
            "eligible": eligible, "warnings": warnings,
        })
    return results
