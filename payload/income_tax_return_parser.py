"""종합소득세 신고서에서 개인사업자·사업장 정보를 안전하게 추출합니다."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path


def _clean(value) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split()).strip()


def _number(value):
    text = re.sub(r"[^0-9\-]", "", str(value or ""))
    if text in {"", "-"}:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _first(pattern, text, flags=0):
    match = re.search(pattern, text or "", flags)
    return _clean(match.group(1)) if match else ""


def _extract_text(pdf_path):
    errors = []
    try:
        import pdfplumber

        with pdfplumber.open(str(pdf_path)) as pdf:
            pages = [(page.extract_text(x_tolerance=2, y_tolerance=3) or "") for page in pdf.pages]
        text = "\f".join(pages)
        if text.strip():
            return text, ""
    except Exception as exc:
        errors.append(f"pdfplumber: {exc}")

    try:
        from pypdf import PdfReader

        reader = PdfReader(str(pdf_path))
        pages = []
        for page in reader.pages:
            try:
                page_text = page.extract_text(extraction_mode="layout") or ""
            except (TypeError, ValueError):
                page_text = page.extract_text() or ""
            pages.append(page_text)
        text = "\f".join(pages)
        if text.strip():
            return text, ""
    except Exception as exc:
        errors.append(f"pypdf: {exc}")

    return "", "PDF 텍스트를 읽지 못했습니다. " + " / ".join(errors[:2])


def _normalize_business_no(value):
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"
    return ""


def _business_validation_errors(business):
    errors = []
    company_name = str(business.get("업체명", "") or "").strip()
    if not company_name or company_name.startswith("개인사업장 "):
        errors.append("상호")
    if not _normalize_business_no(business.get("사업자등록번호", "")):
        errors.append("사업자등록번호")
    if business.get("매출액") is None:
        errors.append("총수입금액")
    if business.get("필요경비") is None:
        errors.append("필요경비")
    if business.get("사업소득금액") is None:
        errors.append("사업소득금액")
    return errors


def _global_summary(text):
    first_pages = "\f".join((text or "").split("\f")[:2])
    dense = re.sub(r"[ \t]", "", first_pages)
    return {
        "대표자명": _first(r"(?:①성명|신고인)([가-힣A-Za-z]{2,30})", dense),
        "귀속연도": _first(r"\((20\d{2})년귀속", dense),
        "종합소득금액": _number(_first(r"종합소득금액(?:19)?([\-0-9,]+)", dense)),
        "소득공제": _number(_first(r"소득공제(?:20)?([\-0-9,]+)", dense)),
        "과세표준": _number(_first(r"과세표준[^\n]*?21([\-0-9,]+?)(?:41|$)", dense, flags=re.M)),
        "적용세율": _first(r"세율22([0-9.]+?)(?:42|$)", dense, flags=re.M),
        "산출세액": _number(_first(r"산출세액23([\-0-9,]+?)(?:43|$)", dense, flags=re.M)),
        "세액감면": _number(_first(r"세액감면(?:24)([\-0-9,]+)", dense)),
        "세액공제": _number(_first(r"세액공제(?:25)([\-0-9,]+)", dense)),
        "결정세액": _number(
            _first(r"합계\(26\+27\)28([\-0-9,]+?)(?:46|$)", dense, flags=re.M)
        ),
        "납부환급세액": _number(
            _first(
                r"납부\(환급\)할총세액[^\n]*?33([\-0-9,]+?)(?:51|$)",
                dense,
                flags=re.M,
            )
        ),
    }


def _split_business_sections(text):
    starts = [match.start() for match in re.finditer(r"[❼⑦]?\s*사업소득명세서", text or "")]
    if not starts:
        return []
    sections = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        section = text[start:end]
        next_form = re.search(
            r"\f\s*(?:[❽-❿⑧-⑩]|\(?\d{1,2}\)?\s+)[^\n]{0,40}(?:명세서|계산서)",
            section[30:],
        )
        if next_form:
            section = section[: next_form.start() + 30]
        sections.append(section)
    return sections


def _extract_business_address(section):
    block = _first(
        r"②\s*일\s*련\s*번\s*호\s*\d+\s*([\s\S]+?)"
        r"국내1/국외9\s*소재지국코드",
        section,
    )
    if not block:
        return ""
    block = re.sub(r"③\s*사\s*소재지\s*", "", block)
    block = re.sub(r"\n\s*업\s+", "\n", block)
    block = re.sub(r"\n\s*장\s*$", "", block)
    road_address = block.split(")", 1)[0]
    if ")" in block:
        road_address += ")"
    road_address = _clean(road_address)
    road_address = re.sub(r"([가-힣])\s+([가-힣])", r"\1\2", road_address)
    regions = (
        "서울특별시", "부산광역시", "대구광역시", "인천광역시",
        "광주광역시", "대전광역시", "울산광역시", "세종특별자치시",
        "경기도", "강원특별자치도", "충청북도", "충청남도",
        "전북특별자치도", "전라남도", "경상북도", "경상남도",
        "제주특별자치도",
    )
    for region in regions:
        if road_address.startswith(region):
            remainder = road_address[len(region):]
            city_match = re.match(r"([가-힣]+(?:시|군))", remainder)
            if city_match:
                city = city_match.group(1)
                remainder = remainder[len(city):]
                district_match = re.match(r"([가-힣]+구)", remainder)
                if district_match:
                    district = district_match.group(1)
                    remainder = remainder[len(district):]
                    road_address = (
                        f"{region} {city} {district} {remainder}"
                    )
                else:
                    road_address = f"{region} {city} {remainder}"
            else:
                road_address = f"{region} {remainder}"
            break
    road_address = re.sub(r"(길|로)(\d)", r"\1 \2", road_address)
    road_address = re.sub(r",\s*", ", ", road_address)
    road_address = re.sub(r"(동|층)(\d)", r"\1 \2", road_address)
    return road_address


def _financial_statement_data(text):
    statements = {}
    pages = (text or "").split("\f")
    for page in pages:
        dense = re.sub(r"[ \t]", "", page)
        business_no = _normalize_business_no(
            _first(
                r"사업자등록번호([0-9]{3}-[0-9]{2}-[0-9]{5})",
                dense,
            )
        )
        if not business_no:
            continue
        values = statements.setdefault(business_no, {})

        if "표준재무상태표" in dense:
            asset = _number(
                _first(r"자산총계[^\n]*?62([\-0-9,]+)", dense)
            )
            liability = _number(
                _first(r"부채총계[^\n]*?87([\-0-9,]+)", dense)
            )
            capital = _number(
                _first(r"자본총계[^\n]*?90([\-0-9,]+)", dense)
            )
            if asset is not None:
                values["자산총계"] = asset
            if liability is not None:
                values["부채총계"] = liability
            if capital is not None:
                values["자본총계"] = capital

        if "표준손익계산서" in dense:
            statement_sales = _number(
                _first(r"Ⅰ\.매출액\s+01\s+([\-0-9,]+)", page)
            )
            operating = _number(
                _first(
                    r"Ⅴ\.영업손익[^\n]*?\s62\s+([\-0-9,]+)",
                    page,
                )
            )
            statement_net = _number(
                _first(
                    r"Ⅷ\.당기순손익[^\n]*?\s99\s+([\-0-9,]+)",
                    page,
                )
            )
            if statement_sales is not None:
                values["표준손익계산서매출액"] = statement_sales
            if operating is not None:
                values["영업손익"] = operating
                values["영업이익"] = operating
            if statement_net is not None:
                values["표준손익계산서당기순손익"] = statement_net
    return statements


def _extract_business(section, global_data, index, statement_data=None):
    dense = re.sub(r"[ \t]", "", section or "")
    business_no = _normalize_business_no(
        _first(r"사업자등록번호([0-9]{3}-[0-9]{2}-[0-9]{5})", dense)
    )
    company_name = _first(r"④상호([^\n]+)", dense)
    if company_name:
        company_name = company_name.split("⑤사업자등록번호")[0].strip()

    address = _extract_business_address(section)

    revenue = _number(_first(r"(?:⑨|9)총수입금액([\-0-9,]+)", dense))
    expense = _number(_first(r"(?:⑩|10)필요경비([\-0-9,]+)", dense))
    income = _number(_first(r"(?:⑪|11)소득금액[^\n]*?([\-0-9,]+)$", dense, flags=re.M))

    result = dict(global_data)
    result.update(
        {
            "사업장순번": index,
            "사업자유형": "개인사업자",
            "업체명": company_name or f"개인사업장 {index}",
            "대표자명": global_data.get("대표자명", ""),
            "사업자등록번호": business_no,
            "사업장 소재지": address,
            "기장의무": _first(r"⑥기장의무([^\n]+)", dense),
            "신고유형": _first(r"⑦신고유형(?:코드)?([^\n]+)", dense),
            "주업종코드": _first(r"⑧주업종코드([0-9]+)", dense),
            "매출액": revenue,
            "연매출": revenue,
            "전년도매출": revenue,
            "필요경비": expense,
            "사업소득금액": income,
            "각사업연도소득금액": income,
            "당기순이익": income,
            "과세기간시작일": _first(r"(?:⑫|12)과세기간개시일([0-9.]+)", dense),
            "과세기간종료일": _first(r"(?:⑬|13)과세기간종료일([0-9.]+)", dense),
            "PDF추출일시": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "자료출처": "종합소득세 신고서",
        }
    )
    result.update(statement_data or {})
    if revenue not in (None, 0) and income is not None:
        result["소득률"] = round(income / revenue * 100, 2)
    if revenue not in (None, 0) and expense is not None:
        result["필요경비율"] = round(expense / revenue * 100, 2)
    return result


def parse_income_tax_return(pdf_path):
    """PDF 한 건을 분석해 사업자번호별 등록 후보와 신고서 요약을 반환합니다."""
    path = Path(pdf_path)
    if not path.exists():
        return {"businesses": [], "summary": {}}, "PDF 파일을 찾을 수 없습니다."

    text, error = _extract_text(path)
    if error:
        return {"businesses": [], "summary": {}}, error

    global_data = _global_summary(text)
    statement_map = _financial_statement_data(text)
    businesses = [
        _extract_business(
            section,
            global_data,
            index,
            statement_map.get(
                _normalize_business_no(
                    _first(
                        r"사업자등록번호([0-9]{3}-[0-9]{2}-[0-9]{5})",
                        re.sub(r"[ \t]", "", section),
                    )
                ),
                {},
            ),
        )
        for index, section in enumerate(_split_business_sections(text), start=1)
    ]
    invalid_businesses = []
    valid_businesses = []
    for item in businesses:
        missing = _business_validation_errors(item)
        if missing:
            invalid_businesses.append(
                {
                    "사업장순번": item.get("사업장순번"),
                    "누락항목": missing,
                }
            )
        else:
            valid_businesses.append(item)
    businesses = valid_businesses
    if not businesses:
        missing_labels = sorted(
            {
                label
                for item in invalid_businesses
                for label in item.get("누락항목", [])
            }
        )
        detail = (
            f" 필수항목 누락: {', '.join(missing_labels)}."
            if missing_labels
            else ""
        )
        return (
            {
                "businesses": [],
                "summary": global_data,
                "invalid_businesses": invalid_businesses,
            },
            "사업소득명세서의 필수정보를 정확히 읽지 못해 등록을 차단했습니다."
            + detail
            + " Railway 재배포 완료 후 다시 분석해주세요.",
        )

    return {
        "businesses": businesses,
        "summary": global_data,
        "invalid_businesses": invalid_businesses,
        "page_count": len(text.split("\f")),
    }, ""
