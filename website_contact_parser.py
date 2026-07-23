from __future__ import annotations

import html
import ipaddress
import re
import socket
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests

from contact_matching import (
    address_tokens,
    email_matches_domain,
    normalize_company_name,
    normalize_email,
    normalize_phone,
)


USER_AGENT = "OASIS-CRM-ContactBot/9.7.0"
MAX_BYTES = 2_000_000
CONTACT_WORDS = (
    "contact",
    "company",
    "about",
    "location",
    "고객문의",
    "문의",
    "회사소개",
    "오시는길",
    "찾아오시는길",
)


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.texts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._href = ""
        self._anchor_text: list[str] = []
        self._ignored = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored += 1
        if tag == "a":
            self._href = dict(attrs).get("href") or ""
            self._anchor_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._ignored:
            self._ignored -= 1
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join(self._anchor_text)))
            self._href = ""
            self._anchor_text = []

    def handle_data(self, data: str) -> None:
        if self._ignored:
            return
        text = re.sub(r"\s+", " ", data).strip()
        if text:
            self.texts.append(text)
            if self._href:
                self._anchor_text.append(text)


def _public_host(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    if host in {"localhost"} or host.endswith((".local", ".internal")):
        return False
    try:
        default_port = 443 if parsed.scheme == "https" else 80
        addresses = socket.getaddrinfo(host, parsed.port or default_port)
    except socket.gaierror:
        return False
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
        ):
            return False
    return True


def _robots_allowed(url: str, timeout: int) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    robot = RobotFileParser()
    robot.set_url(robots_url)
    try:
        response = requests.get(
            robots_url,
            headers={"User-Agent": USER_AGENT},
            timeout=min(5, timeout),
        )
        if response.ok:
            robot.parse(response.text.splitlines())
            return robot.can_fetch(USER_AGENT, url)
    except requests.RequestException:
        pass
    return True


def _fetch_html(url: str, timeout: int) -> tuple[str, str]:
    if not _public_host(url):
        return "", ""
    if not _robots_allowed(url, timeout):
        return "", ""
    response = requests.get(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        },
        timeout=timeout,
        allow_redirects=True,
    )
    if not response.ok or not _public_host(response.url):
        return "", ""
    content_type = response.headers.get("content-type", "").lower()
    if "html" not in content_type:
        return "", ""
    content = response.content[:MAX_BYTES]
    encoding = response.encoding or response.apparent_encoding or "utf-8"
    return content.decode(encoding, errors="replace"), response.url


def extract_public_contacts(text: str) -> tuple[list[str], list[str]]:
    decoded = html.unescape(str(text or ""))
    email_pattern = r"(?<![\w.+-])[\w.%+\-]+@[\w.\-]+\.[A-Za-z]{2,}(?![\w.-])"
    phone_pattern = (
        r"(?<!\d)(?:\+?82[\s.-]?)?"
        r"(?:0?2|0?1[016789]|0?[3-6][1-5])"
        r"[\s).-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
    )
    emails = sorted(
        {
            normalized
            for value in re.findall(email_pattern, decoded)
            if (normalized := normalize_email(value))
            and not normalized.endswith((".png", ".jpg", ".gif", ".svg"))
        }
    )
    phones = sorted(
        {
            normalized
            for value in re.findall(phone_pattern, decoded)
            if (normalized := normalize_phone(value))
        }
    )
    return phones, emails


def _official_score(
    company_name: str,
    address: str,
    business_no: str,
    text: str,
) -> int:
    compact_text = normalize_company_name(text)
    company = normalize_company_name(company_name)
    score = 0
    if company and company in compact_text:
        score += 45

    page_tokens = address_tokens(text)
    expected_tokens = address_tokens(address)
    overlap = expected_tokens & page_tokens
    if overlap:
        score += min(25, 8 + len(overlap) * 5)

    business_digits = re.sub(r"[^0-9]", "", str(business_no or ""))
    page_digits = re.sub(r"[^0-9]", "", text)
    if len(business_digits) == 10 and business_digits in page_digits:
        score += 25
    return min(100, score)


def inspect_website(
    url: str,
    company_name: str,
    address: str,
    business_no: str = "",
    *,
    timeout: int = 10,
    max_pages: int = 4,
) -> dict[str, Any]:
    try:
        first_html, resolved_url = _fetch_html(url, timeout)
    except requests.Timeout:
        return {
            "ok": False,
            "status": "TIMEOUT",
            "message": "홈페이지 응답시간 초과",
            "contacts": [],
        }
    except requests.RequestException as exc:
        return {
            "ok": False,
            "status": "NETWORK_ERROR",
            "message": type(exc).__name__,
            "contacts": [],
        }
    if not first_html:
        return {
            "ok": False,
            "status": "BLOCKED_OR_NOT_HTML",
            "message": "접근 가능한 HTML 홈페이지가 아닙니다.",
            "contacts": [],
        }

    first_parser = _PageParser()
    first_parser.feed(first_html)
    root_host = urlparse(resolved_url).hostname
    queue: list[str] = [resolved_url]
    for href, label in first_parser.links:
        target = urljoin(resolved_url, href)
        if urlparse(target).hostname != root_host:
            continue
        hint = f"{href} {label}".lower()
        if any(word in hint for word in CONTACT_WORDS) and target not in queue:
            queue.append(target)
        if len(queue) >= max(1, int(max_pages)):
            break

    pages: list[dict[str, Any]] = []
    all_text: list[str] = []
    for index, page_url in enumerate(queue[:max_pages]):
        try:
            if index == 0:
                page_html, final_url = first_html, resolved_url
            else:
                page_html, final_url = _fetch_html(page_url, timeout)
        except requests.RequestException:
            continue
        if not page_html:
            continue
        parser = _PageParser()
        parser.feed(page_html)
        text = " ".join(parser.texts)
        phones, emails = extract_public_contacts(text)
        pages.append(
            {
                "url": final_url,
                "text": text,
                "phones": phones,
                "emails": emails,
            }
        )
        all_text.append(text)

    combined_text = " ".join(all_text)
    confidence = _official_score(
        company_name,
        address,
        business_no,
        combined_text,
    )
    contacts: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for page in pages:
        for phone in page["phones"]:
            key = ("phone", phone)
            if key in seen:
                continue
            seen.add(key)
            contacts.append(
                {
                    "contact_type": "phone",
                    "contact_value": phone,
                    "contact_label": "공식 홈페이지 공개전화",
                    "source_type": "official_website",
                    "source_url": page["url"],
                    "confidence": confidence,
                    "metadata": {},
                }
            )
        for email in page["emails"]:
            key = ("email", email)
            if key in seen:
                continue
            seen.add(key)
            domain_match = email_matches_domain(email, resolved_url)
            contacts.append(
                {
                    "contact_type": "email",
                    "contact_value": email,
                    "contact_label": (
                        "대표 이메일" if domain_match else "이메일 확인 필요"
                    ),
                    "source_type": "official_website",
                    "source_url": page["url"],
                    "confidence": min(100, confidence + (10 if domain_match else 0)),
                    "metadata": {"domain_match": domain_match},
                }
            )
    return {
        "ok": True,
        "status": "SUCCESS",
        "message": f"공식 홈페이지 후보 {len(pages)}페이지 확인",
        "website_url": resolved_url,
        "confidence": confidence,
        "contacts": contacts,
        "pages_checked": len(pages),
    }
