from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pandas as pd
import requests
import streamlit as st

from utils import ROOT_DIR, get_user_dirs
from integrated_policy_repository import (
    fetch_bizinfo_records,
    load_repository_records,
    refresh_repository,
    repository_status,
)


CACHE_FILE = "multi_source_policy_cache.json"
SOURCE_TIMEOUT = 45
MAX_API_ROWS = 1000

COLUMN_ALIASES = {
    "title": [
        "공고명", "사업명", "지원사업명", "사업공고명", "통합공고사업명",
        "제도명", "상품명", "사업 제목", "title", "biz_pbanc_nm",
    ],
    "agency": [
        "기관명", "수행기관명", "소관명", "주관기관", "전담기관",
        "agency", "organization",
    ],
    "summary": [
        "사업개요내용", "지원내용", "사업 지원 내용", "사업소개정보",
        "사업개요", "내용", "summary", "description",
    ],
    "target": [
        "지원대상", "신청대상", "신청대상내용", "대상", "사업 지원 대상 정보",
        "target", "applicant",
    ],
    "region": [
        "지원지역", "대상지역", "지역", "소재지", "region",
    ],
    "industry": [
        "대상업종", "업종", "지원업종", "industry",
    ],
    "keywords": [
        "해시태그", "추천키워드", "키워드", "지원사업분류",
        "사업 카테고리 코드", "keywords",
    ],
    "start_date": [
        "신청시작일", "접수시작일", "공고접수시작일시",
        "start_date", "begin_date",
    ],
    "end_date": [
        "신청종료일", "접수종료일", "공고접수종료일시",
        "end_date", "close_date",
    ],
    "url": [
        "공고URL", "상세페이지 url", "사업신청URL", "링크", "url",
    ],
    "startup_age": [
        "사업업력", "업력", "창업업력", "startup_age",
    ],
    "priority": [
        "우대사항", "가점", "priority",
    ],
}


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "nat"}:
        return ""
    return re.sub(r"\s+", " ", text)


def _secret(name: str, default: str = "") -> str:
    value = os.environ.get(name, "")
    if value:
        return value.strip()
    try:
        if name in st.secrets:
            return str(st.secrets[name]).strip()
    except Exception:
        pass
    return default


def _parse_json_secret(name: str) -> dict[str, Any]:
    raw = _secret(name)
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _normalize_text(value: Any) -> str:
    text = _clean(value).lower()
    return re.sub(r"[^0-9a-z가-힣]+", " ", text).strip()


def _tokens(value: Any) -> set[str]:
    text = _normalize_text(value)
    result = set()
    for token in text.split():
        if len(token) >= 2:
            result.add(token)
    return result


def _list_value(value: Any) -> list[str]:
    if isinstance(value, list):
        items = value
    else:
        items = re.split(r"[,;/|\n]+", str(value or ""))
    result = []
    seen = set()
    for item in items:
        cleaned = _clean(item)
        key = cleaned.lower()
        if cleaned and key not in seen:
            result.append(cleaned)
            seen.add(key)
    return result


def _value_from_aliases(record: dict[str, Any], field: str) -> str:
    normalized_keys = {
        _normalize_text(key).replace(" ", ""): key
        for key in record.keys()
    }
    for alias in COLUMN_ALIASES.get(field, []):
        original = normalized_keys.get(
            _normalize_text(alias).replace(" ", "")
        )
        if original is not None:
            value = record.get(original)
            if _clean(value):
                return _clean(value)
    return ""


def _parse_date(value: Any) -> date | None:
    text = re.sub(r"[^0-9]", "", _clean(value))
    if len(text) >= 8:
        try:
            return datetime.strptime(text[:8], "%Y%m%d").date()
        except Exception:
            return None
    return None


def _record_id(source: str, title: str, agency: str, url: str) -> str:
    raw = "|".join([source, title, agency, url])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def normalize_record(
    raw: dict[str, Any],
    source: str,
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None

    title = _value_from_aliases(raw, "title")
    if not title:
        return None

    agency = _value_from_aliases(raw, "agency")
    summary = _value_from_aliases(raw, "summary")
    target = _value_from_aliases(raw, "target")
    region = _value_from_aliases(raw, "region")
    industry = _value_from_aliases(raw, "industry")
    keywords = _value_from_aliases(raw, "keywords")
    start_date = _value_from_aliases(raw, "start_date")
    end_date = _value_from_aliases(raw, "end_date")
    url = _value_from_aliases(raw, "url")
    startup_age = _value_from_aliases(raw, "startup_age")
    priority = _value_from_aliases(raw, "priority")

    return {
        "id": _record_id(source, title, agency, url),
        "source": source,
        "title": title,
        "agency": agency,
        "summary": summary,
        "target": target,
        "region": region,
        "industry": industry,
        "keywords": keywords,
        "start_date": start_date,
        "end_date": end_date,
        "url": url,
        "startup_age": startup_age,
        "priority": priority,
        "raw": raw,
    }


def _flatten_json(value: Any) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    def visit(node: Any) -> None:
        if isinstance(node, list):
            if node and all(isinstance(item, dict) for item in node):
                candidates.extend(node)
            else:
                for item in node:
                    visit(item)
        elif isinstance(node, dict):
            title_like = any(
                _normalize_text(key).replace(" ", "")
                in {
                    _normalize_text(alias).replace(" ", "")
                    for alias in COLUMN_ALIASES["title"]
                }
                for key in node.keys()
            )
            if title_like:
                candidates.append(node)
            for child in node.values():
                if isinstance(child, (dict, list)):
                    visit(child)

    visit(value)

    deduped = []
    seen = set()
    for item in candidates:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


def _fetch_configured_api(
    source_name: str,
    url_secret: str,
    key_secret: str,
    params_secret: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    url = _secret(url_secret)
    if not url:
        return [], {
            "source": source_name,
            "status": "미설정",
            "message": f"{url_secret} 미설정",
            "count": 0,
        }

    params = _parse_json_secret(params_secret)
    key = _secret(key_secret)
    if key:
        key_parameter = str(
            params.pop("_key_parameter", "serviceKey")
        )
        params.setdefault(key_parameter, key)

    params.setdefault("pageNo", 1)
    params.setdefault("numOfRows", MAX_API_ROWS)

    try:
        response = requests.get(
            url,
            params=params,
            timeout=SOURCE_TIMEOUT,
        )
        if not response.ok:
            return [], {
                "source": source_name,
                "status": "오류",
                "message": f"HTTP {response.status_code}: {response.text[:300]}",
                "count": 0,
            }

        content_type = response.headers.get("content-type", "")
        raw_items: list[dict[str, Any]]

        if "json" in content_type.lower():
            raw_items = _flatten_json(response.json())
        else:
            try:
                raw_items = _flatten_json(response.json())
            except Exception:
                raw_items = pd.read_xml(response.text).to_dict("records")

        records = []
        for raw in raw_items:
            normalized = normalize_record(raw, source_name)
            if normalized:
                records.append(normalized)

        return records, {
            "source": source_name,
            "status": "정상",
            "message": f"{urlparse(url).netloc} 연결",
            "count": len(records),
        }
    except Exception as exc:
        return [], {
            "source": source_name,
            "status": "오류",
            "message": str(exc),
            "count": 0,
        }


def fetch_kstartup() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return _fetch_configured_api(
        "K-Startup",
        "KSTARTUP_API_URL",
        "KSTARTUP_API_KEY",
        "KSTARTUP_API_PARAMS_JSON",
    )


def fetch_kosmes() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return _fetch_configured_api(
        "중진공 OpenAPI",
        "KOSMES_API_URL",
        "KOSMES_API_KEY",
        "KOSMES_API_PARAMS_JSON",
    )


def _candidate_excel_files() -> list[Path]:
    patterns = [
        "*지원사업DB*.xlsx",
        "*정책자금*.xlsx",
        "*고용지원금*.xlsx",
        "*기업마당*.xlsx",
        "*상시*.xlsx",
    ]
    roots = [
        ROOT_DIR,
        ROOT_DIR / "templates",
        ROOT_DIR / "data",
    ]

    files = []
    seen = set()
    for root in roots:
        if not root.exists():
            continue
        for pattern in patterns:
            for path in root.glob(pattern):
                resolved = str(path.resolve())
                if resolved not in seen:
                    seen.add(resolved)
                    files.append(path)
    return files


def load_local_sources() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    repository_rows, status = load_repository_records()
    records: list[dict[str, Any]] = []
    for row in repository_rows:
        raw = row.get("raw_data", {})
        if not isinstance(raw, dict):
            continue
        enriched = dict(raw)
        if not enriched.get("기관명") and enriched.get("기관"):
            enriched["기관명"] = enriched.get("기관")
        if not enriched.get("사업명") and enriched.get("상품명"):
            enriched["사업명"] = enriched.get("상품명")
        if not enriched.get("사업명") and enriched.get("제도명"):
            enriched["사업명"] = enriched.get("제도명")
        source_type = str(row.get("source_type", "internal"))
        source_name = str(row.get("source_name", "") or source_type)
        normalized = normalize_record(enriched, f"{source_name}:{source_type}")
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
    return records, {
        "source": "내부 통합 정책DB",
        "status": "정상" if records else "자료없음",
        "message": status.get("message", ""),
        "count": len(records),
    }



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

def _deduplicate(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}

    for record in records:
        title_key = re.sub(
            r"[^0-9a-z가-힣]",
            "",
            _normalize_text(record.get("title", "")),
        )
        agency_key = re.sub(
            r"[^0-9a-z가-힣]",
            "",
            _normalize_text(record.get("agency", "")),
        )
        key = f"{title_key}|{agency_key}"

        if not title_key:
            continue

        source = _clean(
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

        for field in [
            "summary", "target", "region", "industry",
            "keywords", "priority", "url", "startup_age",
            "start_date", "end_date",
        ]:
            if len(_clean(record.get(field))) > len(_clean(existing.get(field))):
                existing[field] = record[field]

    return list(groups.values())


def _customer_value(customer: pd.Series, *keys: str) -> str:
    for key in keys:
        if key in customer.index and _clean(customer.get(key)):
            return _clean(customer.get(key))
    return ""


def _startup_years(customer: pd.Series) -> float | None:
    establishment = _customer_value(customer, "설립일", "설립년도")
    digits = re.sub(r"[^0-9]", "", establishment)
    if len(digits) < 4:
        return None
    try:
        year = int(digits[:4])
        return max(date.today().year - year, 0)
    except Exception:
        return None


def _region_tokens(address: str) -> set[str]:
    text = _normalize_text(address)
    aliases = {
        "서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구",
        "인천광역시": "인천", "광주광역시": "광주", "대전광역시": "대전",
        "울산광역시": "울산", "세종특별자치시": "세종", "경기도": "경기",
        "강원특별자치도": "강원", "충청북도": "충북", "충청남도": "충남",
        "전북특별자치도": "전북", "전라남도": "전남", "경상북도": "경북",
        "경상남도": "경남", "제주특별자치도": "제주",
    }
    for long_name, short_name in aliases.items():
        text = text.replace(long_name.lower(), short_name)
    return _tokens(text)


def score_record(
    record: dict[str, Any],
    customer: pd.Series,
    preferences: dict[str, Any],
) -> dict[str, Any]:
    title = record.get("title", "")
    combined = " ".join(
        _clean(record.get(field))
        for field in [
            "title", "summary", "target", "region",
            "industry", "keywords", "priority", "startup_age",
        ]
    )
    record_tokens = _tokens(combined)

    company = _customer_value(customer, "업체명")
    industry = _customer_value(customer, "업종명", "표준산업분류")
    address = _customer_value(customer, "사업장 소재지", "시도", "시군구")
    employees = _customer_value(customer, "종업원수", "상시근로자수")
    sales = _customer_value(customer, "매출액", "연매출", "전년도매출")

    matching_keywords = _list_value(preferences.get("매칭키워드", []))
    interests = _list_value(preferences.get("관심지원분야", []))
    exclusions = _list_value(preferences.get("제외키워드", []))
    purpose = _clean(preferences.get("자금사용목적", ""))

    evidence: list[str] = []
    penalties: list[str] = []
    score = 20.0

    keyword_hits = []
    for keyword in matching_keywords + interests:
        keyword_tokens = _tokens(keyword)
        if keyword_tokens and keyword_tokens & record_tokens:
            keyword_hits.append(keyword)

    if keyword_hits:
        unique_hits = list(dict.fromkeys(keyword_hits))
        score += min(34, 8 * len(unique_hits))
        evidence.append(
            "고객 매칭키워드 일치: " + ", ".join(unique_hits[:5])
        )

    purpose_hits = _tokens(purpose) & record_tokens
    if purpose_hits:
        score += min(18, 6 * len(purpose_hits))
        evidence.append(
            "자금사용목적 일치: " + ", ".join(sorted(purpose_hits)[:4])
        )

    industry_hits = _tokens(industry) & record_tokens
    if industry_hits:
        score += min(14, 5 * len(industry_hits))
        evidence.append(
            "업종 일치: " + ", ".join(sorted(industry_hits)[:4])
        )

    customer_region = _region_tokens(address)
    record_region = _region_tokens(record.get("region", ""))
    if record_region:
        nationwide = bool(
            {"전국", "대한민국"} & record_region
            or "전국" in _normalize_text(record.get("region", ""))
        )
        if nationwide:
            score += 5
            evidence.append("전국 대상")
        elif customer_region & record_region:
            score += 14
            evidence.append(
                "지역 일치: " + ", ".join(sorted(customer_region & record_region))
            )
        else:
            score -= 28
            penalties.append("기업 소재지와 지원지역 불일치")

    years = _startup_years(customer)
    startup_text = _normalize_text(record.get("startup_age", ""))
    if years is not None and startup_text:
        if "예비" in startup_text and years > 0:
            score -= 30
            penalties.append("기창업 기업이나 예비창업 대상 공고")
        if "3년" in startup_text and years > 3:
            score -= 22
            penalties.append("업력 3년 요건 초과 가능")
        if "7년" in startup_text and years > 7:
            score -= 22
            penalties.append("업력 7년 요건 초과 가능")
        if years <= 7 and record.get("source") == "K-Startup":
            score += 8
            evidence.append(f"창업기업 업력 약 {years:.0f}년")

    for exclusion in exclusions:
        if _tokens(exclusion) & record_tokens:
            score -= 35
            penalties.append(f"제외키워드 포함: {exclusion}")

    end_date = _parse_date(record.get("end_date"))
    if end_date:
        if end_date < date.today():
            score -= 60
            penalties.append("신청기간 종료")
        elif (end_date - date.today()).days <= 7:
            evidence.append("마감 7일 이내")

    source_list = record.get("source_list", [record.get("source", "")])
    if len(source_list) >= 2:
        score += 5
        evidence.append("복수 공고소스에서 확인")

    completeness = sum(
        bool(_clean(record.get(field)))
        for field in ["summary", "target", "region", "industry", "end_date"]
    )
    score += completeness * 1.5

    if employees:
        evidence.append(f"기업 종업원 정보 보유: {employees}")
    if sales:
        evidence.append("기업 매출정보 보유")
    if company:
        evidence.append(f"분석기업: {company}")

    final_score = max(0, min(round(score), 100))
    grade = (
        "매우 높음" if final_score >= 85
        else "높음" if final_score >= 70
        else "검토" if final_score >= 55
        else "낮음"
    )

    return {
        **record,
        "score": final_score,
        "grade": grade,
        "evidence": evidence,
        "penalties": penalties,
    }


def _cache_path(user_id: str) -> Path:
    return get_user_dirs(user_id)["base"] / CACHE_FILE


def save_results(
    user_id: str,
    business_no: str,
    company_name: str,
    results: list[dict[str, Any]],
    source_status: list[dict[str, Any]],
) -> None:
    path = _cache_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(existing, dict):
            existing = {}
    except Exception:
        existing = {}

    key = re.sub(r"[^0-9]", "", business_no) or company_name
    existing[key] = {
        "company_name": company_name,
        "business_no": business_no,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "source_status": source_status,
        "results": [
            {
                key: value
                for key, value in result.items()
                if key != "raw"
            }
            for result in results[:100]
        ],
    }
    path.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def collect_all_sources() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    all_records = []
    statuses = []

    refresh_repository(force=False)

    for loader in [
        load_local_sources,
        load_bizinfo_source,
        fetch_kstartup,
        fetch_kosmes,
    ]:
        records, status = loader()
        all_records.extend(records)
        statuses.append(status)

    return _deduplicate(all_records), statuses


def render_multi_source_match(
    user_id: str,
    customer: pd.Series,
    preferences: dict[str, Any],
) -> None:
    st.markdown("#### 다중소스 증거기반 매칭")
    st.caption(
        "내부 상시정책자금·고용지원금 DB에 기업마당·K-Startup·중진공 API를 "
        "결합하고, 고객정보·상담키워드·지역·업력·제외조건을 근거로 점수를 계산합니다."
    )

    with st.expander("공고소스 설정 및 작동방식", expanded=False):
        st.markdown(
            """
            **현재 사용 소스**
            - 기존 기업마당·상시형 정책자금·고용지원금 내부DB
            - K-Startup OpenAPI
            - 중소벤처기업진흥공단 OpenAPI

            외부 API는 Streamlit Secrets에 URL과 인증키를 등록하면 활성화됩니다.
            기업마당 공공데이터 API는 기존 기업마당 자료와 중복 가능성이 높아
            별도 중복소스로 추가하지 않고 기존 동기화 데이터를 사용합니다.
            """
        )
        st.code(
            '\n'.join(
                [
                    'KSTARTUP_API_URL = "발급받은 K-Startup API 호출 URL"',
                    'KSTARTUP_API_KEY = "인증키"',
                    'KSTARTUP_API_PARAMS_JSON = \'{"pageNo":1,"numOfRows":1000,"type":"json"}\'',
                    '',
                    'KOSMES_API_URL = "사용할 중진공 OpenAPI 호출 URL"',
                    'KOSMES_API_KEY = "인증키"',
                    'KOSMES_API_PARAMS_JSON = \'{"pageNo":1,"numOfRows":1000}\'',
                ]
            ),
            language="toml",
        )

    repository_info = repository_status()
    st.caption(
        f"내부 정책DB {repository_info.get('count', 0)}건 · "
        f"최근 자동확인 {repository_info.get('last_attempt_at') or '미실행'}"
    )
    refresh_col, _ = st.columns([1, 3])
    with refresh_col:
        if st.button(
            "정책DB 지금 최신화",
            key=f"policy_repository_refresh_{customer.name}",
            use_container_width=True,
        ):
            with st.spinner("기업마당 및 내부 정책DB를 최신화하고 있습니다..."):
                refresh_result = refresh_repository(force=True)
            st.info(
                f"{refresh_result.get('message', '')} "
                f"현재 {refresh_result.get('count', 0)}건"
            )
            st.rerun()

    if st.button(
        "다중소스 AI 매칭 실행",
        type="primary",
        use_container_width=True,
        key=f"multi_source_match_{customer.name}",
    ):
        with st.status(
            "공고소스를 통합하고 매칭점수를 계산하고 있습니다.",
            expanded=True,
        ) as status:
            st.write("1. 기존 내부DB를 확인합니다.")
            records, source_status = collect_all_sources()

            st.write("2. 중복 공고를 통합합니다.")
            st.write(f"통합 공고 {len(records):,}건")

            st.write("3. 기업정보와 상담키워드로 적합도를 계산합니다.")
            scored = [
                score_record(record, customer, preferences)
                for record in records
            ]
            scored.sort(key=lambda item: item["score"], reverse=True)

            company_name = _customer_value(customer, "업체명")
            business_no = _customer_value(customer, "사업자등록번호")
            save_results(
                user_id,
                business_no,
                company_name,
                scored,
                source_status,
            )
            st.session_state[
                f"multi_source_results_{business_no or company_name}"
            ] = {
                "results": scored,
                "source_status": source_status,
            }

            status.update(
                label=f"통합매칭 완료 · 공고 {len(records):,}건 분석",
                state="complete",
                expanded=False,
            )

    company_name = _customer_value(customer, "업체명")
    business_no = _customer_value(customer, "사업자등록번호")
    stored = st.session_state.get(
        f"multi_source_results_{business_no or company_name}"
    )
    if not stored:
        return

    source_status = stored["source_status"]
    results = stored["results"]

    st.markdown("##### 공고소스 상태")
    st.dataframe(
        pd.DataFrame(source_status),
        hide_index=True,
        use_container_width=True,
    )

    minimum_score = st.slider(
        "표시할 최소점수",
        min_value=0,
        max_value=100,
        value=55,
        key=f"multi_source_min_score_{business_no}",
    )
    visible = [
        result
        for result in results
        if result["score"] >= minimum_score
    ][:50]

    if not visible:
        st.info("설정한 점수 이상인 공고가 없습니다.")
        return

    table_rows = []
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
