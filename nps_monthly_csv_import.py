from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import time
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import requests


NON_IDENTITY_CHARACTERS = re.compile(r"[^0-9a-z가-힣]")
NON_DIGITS = re.compile(r"[^0-9]")


def _normalized(value: Any) -> str:
    return NON_IDENTITY_CHARACTERS.sub("", str(value or "").lower())


def _integer(value: Any) -> int:
    digits = NON_DIGITS.sub("", str(value or ""))
    return int(digits) if digits else 0


def _date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _snapshot_identity(columns: list[str]) -> str:
    business_digits = NON_DIGITS.sub("", columns[2]) or "unknown"
    normalized_name = _normalized(columns[1])
    address = columns[6].strip() or columns[5].strip()
    normalized_address = _normalized(address)
    if normalized_name and normalized_address:
        place_key = f"{normalized_name}|{normalized_address}"
    else:
        location = columns[7].strip() or columns[8].strip() or columns[4].strip()
        place_key = f"{normalized_name}|{location}"
    place_hash = hashlib.sha256(place_key.encode("utf-8")).hexdigest()
    return f"business:{business_digits}|place:{place_hash}"


def _source_key(identity: str) -> str:
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return f"nps-csv:{digest}"


def _row(columns: list[str], source_file_name: str) -> dict[str, Any]:
    identity = _snapshot_identity(columns)
    road_address = columns[6].strip()
    lot_address = columns[5].strip()
    address = road_address or lot_address
    month = NON_DIGITS.sub("", columns[0])
    if len(month) != 6:
        raise ValueError(f"잘못된 자료생성년월: {columns[0]!r}")
    return {
        "snapshot_identity": identity,
        "data_created_ym": month,
        "employee_count": _integer(columns[18]),
        "company_name": columns[1].strip(),
        "address": address,
        "source_key": _source_key(identity),
        "normalized_name": _normalized(columns[1]),
        "normalized_address": _normalized(address),
        "business_no": NON_DIGITS.sub("", columns[2]),
        "new_employee_count": _integer(columns[20]),
        "lost_employee_count": _integer(columns[21]),
        "join_status_code": columns[3].strip(),
        "zip_code": columns[4].strip(),
        "lot_address": lot_address,
        "road_address": road_address,
        "legal_dong_code": columns[7].strip(),
        "admin_dong_code": columns[8].strip(),
        "province_code": columns[9].strip(),
        "district_code": columns[10].strip(),
        "neighborhood_code": columns[11].strip(),
        "workplace_type_code": columns[12].strip(),
        "industry_code": columns[13].strip(),
        "industry_name": columns[14].strip(),
        "applied_on": _date(columns[15]),
        "reregistered_on": _date(columns[16]),
        "withdrawn_on": _date(columns[17]),
        "monthly_billed_amount": _integer(columns[19]),
        "source_file_name": source_file_name,
    }


def _reader(path: Path) -> Iterable[list[str]]:
    with path.open("r", encoding="cp949", newline="") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        if len(header) != 22:
            raise ValueError(f"{path.name}: 예상 컬럼 22개, 실제 {len(header)}개")
        for line_number, columns in enumerate(reader, start=2):
            if len(columns) != 22:
                raise ValueError(
                    f"{path.name}:{line_number}: 예상 컬럼 22개, 실제 {len(columns)}개"
                )
            yield columns


def _duplicate_identities(path: Path) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for columns in _reader(path):
        if _integer(columns[18]) < 10:
            continue
        identity = _snapshot_identity(columns)
        if identity in seen:
            duplicates.add(identity)
        else:
            seen.add(identity)
    return duplicates


def _post_batch(
    session: requests.Session,
    function_url: str,
    api_key: str,
    rows: list[dict[str, Any]],
) -> None:
    payload = json.dumps({"rows": rows}, ensure_ascii=False).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "apikey": api_key,
        "Content-Type": "application/json; charset=utf-8",
    }
    last_error = ""
    for attempt in range(1, 6):
        try:
            response = session.post(
                function_url,
                headers=headers,
                data=payload,
                timeout=120,
            )
            if response.ok:
                result = response.json()
                if int(result.get("upserted") or 0) != len(rows):
                    raise RuntimeError(f"적재 건수 불일치: {result}")
                return
            last_error = f"HTTP {response.status_code}: {response.text[:500]}"
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = str(exc)
        if attempt < 5:
            time.sleep(min(30, 2 ** attempt))
    raise RuntimeError(f"Supabase 적재 실패: {last_error}")


def import_file(
    path: Path,
    *,
    function_url: str,
    api_key: str,
    batch_size: int,
    resume_after: int = 0,
) -> tuple[int, int]:
    duplicates = _duplicate_identities(path)
    best_duplicate_rows: dict[str, dict[str, Any]] = {}
    batch: list[dict[str, Any]] = []
    uploaded = max(0, int(resume_after))
    eligible_seen = 0
    source_file_name = path.name
    with requests.Session() as session:
        for columns in _reader(path):
            if _integer(columns[18]) < 10:
                continue
            row = _row(columns, source_file_name)
            identity = row["snapshot_identity"]
            if identity in duplicates:
                previous = best_duplicate_rows.get(identity)
                if previous is None or row["employee_count"] > previous["employee_count"]:
                    best_duplicate_rows[identity] = row
                continue
            eligible_seen += 1
            if eligible_seen <= resume_after:
                continue
            batch.append(row)
            if len(batch) >= batch_size:
                _post_batch(session, function_url, api_key, batch)
                uploaded += len(batch)
                batch.clear()
                if uploaded % 10_000 == 0:
                    print(
                        json.dumps(
                            {
                                "file": source_file_name,
                                "uploaded": uploaded,
                                "duplicates": len(duplicates),
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
        batch.extend(best_duplicate_rows.values())
        while batch:
            sending = batch[:batch_size]
            _post_batch(session, function_url, api_key, sending)
            uploaded += len(sending)
            del batch[:batch_size]
    return uploaded, len(duplicates)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument(
        "--resume-first",
        type=int,
        default=0,
        help="첫 파일에서 이미 적재된 비중복 행 수",
    )
    args = parser.parse_args()
    function_url = os.environ.get("NPS_IMPORT_FUNCTION_URL", "").strip()
    api_key = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    if not function_url or not api_key:
        raise SystemExit(
            "NPS_IMPORT_FUNCTION_URL, SUPABASE_ANON_KEY 환경변수가 필요합니다."
        )
    for file_index, path in enumerate(args.files):
        uploaded, duplicate_count = import_file(
            path,
            function_url=function_url,
            api_key=api_key,
            batch_size=max(1, min(1000, args.batch_size)),
            resume_after=args.resume_first if file_index == 0 else 0,
        )
        print(
            json.dumps(
                {
                    "file": path.name,
                    "uploaded": uploaded,
                    "duplicates_removed": duplicate_count,
                    "status": "complete",
                },
                ensure_ascii=False,
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
