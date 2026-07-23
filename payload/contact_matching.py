from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urlparse


CORPORATE_TOKENS = (
    "주식회사",
    "유한회사",
    "합자회사",
    "합명회사",
    "사단법인",
    "재단법인",
    "농업회사법인",
    "영농조합법인",
    "(주)",
    "㈜",
    "(유)",
)


def normalize_company_name(value: Any) -> str:
    text = str(value or "").strip().lower()
    for token in CORPORATE_TOKENS:
        text = text.replace(token.lower(), "")
    return re.sub(r"[^0-9a-z가-힣]", "", text)


def search_company_name(value: Any) -> str:
    text = str(value or "").strip()
    for token in CORPORATE_TOKENS:
        text = text.replace(token, " ")
    return re.sub(r"\s+", " ", text).strip() or str(value or "").strip()


def normalize_address(value: Any) -> str:
    text = str(value or "").strip().lower()
    replacements = {
        "서울시": "서울특별시",
        "경기 ": "경기도 ",
        "경기도시": "경기도",
    }
    for before, after in replacements.items():
        text = text.replace(before, after)
    return re.sub(r"[^0-9a-z가-힣]", "", text)


def address_hint(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    tokens = text.split()
    if not tokens:
        return ""
    selected: list[str] = []
    for token in tokens[:4]:
        selected.append(token)
        if token.endswith(("구", "군", "시")) and len(selected) >= 2:
            break
    return " ".join(selected)


def address_tokens(value: Any) -> set[str]:
    text = re.sub(r"[^0-9a-zA-Z가-힣\s-]", " ", str(value or ""))
    ignored = {
        "서울특별시",
        "경기도",
        "서울",
        "경기",
        "대한민국",
        "번지",
    }
    return {
        token.lower()
        for token in re.split(r"[\s,()]+", text)
        if len(token) >= 2 and token not in ignored
    }


def company_score(expected: Any, candidate: Any) -> int:
    left = normalize_company_name(expected)
    right = normalize_company_name(candidate)
    if not left or not right:
        return 0
    if left == right:
        return 45
    if min(len(left), len(right)) >= 3 and (left in right or right in left):
        return 38
    ratio = SequenceMatcher(None, left, right).ratio()
    if ratio >= 0.9:
        return 34
    if ratio >= 0.78:
        return 26
    if ratio >= 0.65:
        return 16
    return 0


def address_match_score(expected: Any, candidate: Any) -> int:
    left = normalize_address(expected)
    right = normalize_address(candidate)
    if not left or not right:
        return 0
    if left == right:
        return 35
    if min(len(left), len(right)) >= 8 and (left in right or right in left):
        return 32

    expected_tokens = address_tokens(expected)
    candidate_tokens = address_tokens(candidate)
    if not expected_tokens or not candidate_tokens:
        return 0
    overlap = expected_tokens & candidate_tokens
    ratio = len(overlap) / max(1, min(len(expected_tokens), len(candidate_tokens)))
    if ratio >= 0.75:
        return 28
    if ratio >= 0.5:
        return 21
    if overlap:
        return 10
    return 0


def contact_match_score(
    expected_name: Any,
    expected_address: Any,
    candidate_name: Any,
    candidate_address: Any,
    *,
    has_phone: bool = False,
    active: bool = True,
) -> int:
    score = company_score(expected_name, candidate_name)
    score += address_match_score(expected_address, candidate_address)
    if active:
        score += 10
    if has_phone:
        score += 10
    return min(100, score)


def normalize_phone(value: Any) -> str:
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    if digits.startswith("82"):
        digits = "0" + digits[2:]
    # 대표번호(15xx/16xx/18xx)를 제외한 임의의 8자리 숫자는
    # 날짜(20260723), 문서번호, 사업자 관련 숫자일 가능성이 높다.
    if len(digits) == 8:
        if re.fullmatch(r"1[568]\d{2}\d{4}", digits):
            return f"{digits[:4]}-{digits[4:]}"
        return ""
    if len(digits) == 11 and digits.startswith("010"):
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    if len(digits) in {10, 11} and digits.startswith(
        ("011", "016", "017", "018", "019")
    ):
        middle = 6 if len(digits) == 10 else 7
        return f"{digits[:3]}-{digits[3:middle]}-{digits[middle:]}"
    if len(digits) == 10 and digits.startswith("02"):
        return f"{digits[:2]}-{digits[2:6]}-{digits[6:]}"
    if len(digits) == 9 and digits.startswith("02"):
        return f"{digits[:2]}-{digits[2:5]}-{digits[5:]}"
    landline_prefixes = (
        "031",
        "032",
        "033",
        "041",
        "042",
        "043",
        "044",
        "051",
        "052",
        "053",
        "054",
        "055",
        "061",
        "062",
        "063",
        "064",
        "050",
        "070",
        "080",
    )
    if len(digits) == 10 and digits.startswith(landline_prefixes):
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    if len(digits) == 11 and digits.startswith(landline_prefixes):
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    return ""


def is_valid_phone(value: Any) -> bool:
    return bool(normalize_phone(value))


def is_mobile_phone(value: Any) -> bool:
    return re.sub(r"[^0-9]", "", str(value or "")).startswith("010")


def normalize_email(value: Any) -> str:
    email = str(value or "").strip().strip(".,;:()[]<>").lower()
    if re.fullmatch(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", email):
        return email
    return ""


def registrable_domain(value: Any) -> str:
    parsed = urlparse(str(value or ""))
    host = (parsed.hostname or str(value or "")).lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def email_matches_domain(email: Any, website_url: Any) -> bool:
    normalized = normalize_email(email)
    host = registrable_domain(website_url)
    if not normalized or not host:
        return False
    email_domain = normalized.rsplit("@", 1)[-1]
    return email_domain == host or email_domain.endswith("." + host)
