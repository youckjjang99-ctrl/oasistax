from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import sys
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any


NON_DIGITS = re.compile(r"[^0-9]")
WHITESPACE = re.compile(r"\s+")

PROVINCE_ALIASES = {
    "서울": "서울특별시",
    "서울시": "서울특별시",
    "서울특별시": "서울특별시",
    "부산": "부산광역시",
    "부산시": "부산광역시",
    "부산광역시": "부산광역시",
    "대구": "대구광역시",
    "대구시": "대구광역시",
    "대구광역시": "대구광역시",
    "인천": "인천광역시",
    "인천시": "인천광역시",
    "인천광역시": "인천광역시",
    "광주": "광주광역시",
    "광주시": "광주광역시",
    "광주광역시": "광주광역시",
    "대전": "대전광역시",
    "대전시": "대전광역시",
    "대전광역시": "대전광역시",
    "울산": "울산광역시",
    "울산시": "울산광역시",
    "울산광역시": "울산광역시",
    "세종": "세종특별자치시",
    "세종시": "세종특별자치시",
    "세종특별자치시": "세종특별자치시",
    "경기": "경기도",
    "경기도": "경기도",
    "강원": "강원특별자치도",
    "강원도": "강원특별자치도",
    "강원특별자치도": "강원특별자치도",
    "충북": "충청북도",
    "충청북도": "충청북도",
    "충남": "충청남도",
    "충청남도": "충청남도",
    "전북": "전북특별자치도",
    "전라북도": "전북특별자치도",
    "전북특별자치도": "전북특별자치도",
    "전남": "전라남도",
    "전라남도": "전라남도",
    "경북": "경상북도",
    "경상북도": "경상북도",
    "경남": "경상남도",
    "경상남도": "경상남도",
    "제주": "제주특별자치도",
    "제주도": "제주특별자치도",
    "제주특별자치도": "제주특별자치도",
}

OUTPUT_COLUMNS = (
    "business_no",
    "company_name",
    "primary_workplace_management_no",
    "zip_code",
    "address",
    "province",
    "district",
    "industry_code",
    "industry_name",
    "workers_2023",
    "workers_2024",
    "workers_2025",
    "growth_2023_2024",
    "growth_2024_2025",
    "growth_2023_2025",
    "workplace_count_2025",
    "source_year_mask",
    "is_new_2025",
)


def _digits(value: Any) -> str:
    return NON_DIGITS.sub("", str(value or ""))


def _integer(value: Any) -> int:
    text = str(value or "").replace(",", "").strip()
    try:
        return max(0, int(float(text)))
    except (TypeError, ValueError):
        return 0


def _text(value: Any) -> str:
    return WHITESPACE.sub(" ", str(value or "")).strip()


def _encoding(path: Path) -> str:
    with path.open("rb") as stream:
        return "utf-8-sig" if stream.read(3) == b"\xef\xbb\xbf" else "cp949"


def _region(address: str) -> tuple[str, str]:
    parts = _text(address).split()
    if not parts:
        return "", ""
    province = PROVINCE_ALIASES.get(parts[0], "")
    if not province:
        return "", ""
    if province == "세종특별자치시":
        return province, ""
    return province, parts[1] if len(parts) > 1 else ""


def _fallback_management_no(
    business_no: str,
    company_name: str,
    address: str,
) -> str:
    raw = f"{business_no}|{company_name}|{address}".encode("utf-8")
    return f"fallback:{hashlib.sha256(raw).hexdigest()[:24]}"


def _iter_csv_rows(path: Path) -> Iterator[list[str]]:
    encoding = _encoding(path)
    with path.open("r", encoding=encoding, errors="replace", newline="") as stream:
        reader = csv.reader(stream)
        try:
            next(reader)
        except StopIteration:
            return
        for columns in reader:
            if len(columns) >= 15:
                yield columns


def _workplace_row(year: int, columns: list[str]) -> tuple[Any, ...] | None:
    business_no = _digits(columns[13])
    if len(business_no) != 10:
        return None

    company_name = _text(columns[2])
    address = _text(columns[4])
    management_no = _digits(columns[14]) or _fallback_management_no(
        business_no,
        company_name,
        address,
    )
    employment_workers = _integer(columns[10])
    industrial_workers = _integer(columns[9])
    workers = max(employment_workers, industrial_workers)
    industry_code = _text(columns[5])
    industry_name = _text(columns[6])
    if year == 2025 and len(columns) >= 17:
        industry_code = _text(columns[15]) or industry_code
        industry_name = _text(columns[16]) or industry_name

    return (
        year,
        management_no,
        business_no,
        company_name,
        _text(columns[3]),
        address,
        industry_code,
        industry_name,
        workers,
    )


def _connect(path: Path, *, rebuild: bool) -> sqlite3.Connection:
    if rebuild and path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    connection.execute("pragma journal_mode = off")
    connection.execute("pragma synchronous = off")
    connection.execute("pragma temp_store = memory")
    connection.execute("pragma cache_size = -262144")
    connection.execute("pragma locking_mode = exclusive")
    connection.execute(
        """
        create table if not exists workplaces (
            year integer not null,
            management_no text not null,
            business_no text not null,
            company_name text not null,
            zip_code text not null,
            address text not null,
            industry_code text not null,
            industry_name text not null,
            workers integer not null,
            primary key (year, management_no)
        ) without rowid
        """
    )
    return connection


UPSERT_WORKPLACE_SQL = """
insert into workplaces (
    year,
    management_no,
    business_no,
    company_name,
    zip_code,
    address,
    industry_code,
    industry_name,
    workers
) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
on conflict (year, management_no) do update set
    business_no = case
        when excluded.workers >= workplaces.workers then excluded.business_no
        else workplaces.business_no
    end,
    company_name = case
        when excluded.workers >= workplaces.workers then excluded.company_name
        else workplaces.company_name
    end,
    zip_code = case
        when excluded.workers >= workplaces.workers then excluded.zip_code
        else workplaces.zip_code
    end,
    address = case
        when excluded.workers >= workplaces.workers then excluded.address
        else workplaces.address
    end,
    industry_code = case
        when excluded.workers >= workplaces.workers then excluded.industry_code
        else workplaces.industry_code
    end,
    industry_name = case
        when excluded.workers >= workplaces.workers then excluded.industry_name
        else workplaces.industry_name
    end,
    workers = max(workplaces.workers, excluded.workers)
"""


def stage_year(
    connection: sqlite3.Connection,
    year: int,
    files: Iterable[Path],
    *,
    batch_size: int,
) -> dict[str, int]:
    started = time.monotonic()
    read_rows = 0
    eligible_rows = 0
    batch: list[tuple[Any, ...]] = []
    for path in files:
        for columns in _iter_csv_rows(path):
            read_rows += 1
            row = _workplace_row(year, columns)
            if row is not None:
                eligible_rows += 1
                batch.append(row)
            if len(batch) >= batch_size:
                connection.executemany(UPSERT_WORKPLACE_SQL, batch)
                batch.clear()
            if read_rows % 250_000 == 0:
                print(
                    json.dumps(
                        {
                            "stage": "read",
                            "year": year,
                            "rows": read_rows,
                            "eligible": eligible_rows,
                            "elapsed_seconds": round(time.monotonic() - started, 1),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
        connection.commit()
    if batch:
        connection.executemany(UPSERT_WORKPLACE_SQL, batch)
        connection.commit()
    unique_workplaces = int(
        connection.execute(
            "select count(*) from workplaces where year = ?",
            (year,),
        ).fetchone()[0]
    )
    result = {
        "year": year,
        "read_rows": read_rows,
        "eligible_rows": eligible_rows,
        "excluded_missing_business_no": read_rows - eligible_rows,
        "unique_workplaces": unique_workplaces,
    }
    print(
        json.dumps(
            {
                "stage": "year_complete",
                **result,
                "elapsed_seconds": round(time.monotonic() - started, 1),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return result


COMPACT_QUERY = """
with yearly as (
    select
        business_no,
        year,
        sum(workers) as workers,
        count(*) as workplace_count
    from workplaces
    group by business_no, year
),
annual as (
    select
        business_no,
        max(case when year = 2023 then workers end) as workers_2023_raw,
        max(case when year = 2024 then workers end) as workers_2024_raw,
        max(case when year = 2025 then workers end) as workers_2025_raw,
        max(case when year = 2025 then workplace_count end) as workplaces_2025,
        sum(
            case year
                when 2023 then 1
                when 2024 then 2
                when 2025 then 4
                else 0
            end
        ) as source_year_mask
    from yearly
    group by business_no
),
ranked_latest as (
    select
        w.*,
        row_number() over (
            partition by w.business_no
            order by w.workers desc, w.management_no
        ) as row_rank
    from workplaces w
    where w.year = 2025
)
select
    a.business_no,
    r.company_name,
    r.management_no,
    r.zip_code,
    r.address,
    r.industry_code,
    r.industry_name,
    coalesce(a.workers_2023_raw, 0) as workers_2023,
    coalesce(a.workers_2024_raw, 0) as workers_2024,
    a.workers_2025_raw as workers_2025,
    coalesce(a.workers_2024_raw, 0) - coalesce(a.workers_2023_raw, 0)
        as growth_2023_2024,
    a.workers_2025_raw - coalesce(a.workers_2024_raw, 0)
        as growth_2024_2025,
    a.workers_2025_raw - coalesce(a.workers_2023_raw, 0)
        as growth_2023_2025,
    a.workplaces_2025,
    a.source_year_mask,
    case
        when a.workers_2023_raw is null and a.workers_2024_raw is null then 1
        else 0
    end as is_new_2025
from annual a
join ranked_latest r
  on r.business_no = a.business_no
 and r.row_rank = 1
where a.workers_2025_raw >= 1
order by a.business_no
"""


def write_compact_csv(
    connection: sqlite3.Connection,
    output_path: Path,
) -> dict[str, int]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    written = 0
    positive_growth = 0
    new_2025 = 0
    with output_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        cursor = connection.execute(COMPACT_QUERY)
        for row in cursor:
            (
                business_no,
                company_name,
                management_no,
                zip_code,
                address,
                industry_code,
                industry_name,
                workers_2023,
                workers_2024,
                workers_2025,
                growth_2023_2024,
                growth_2024_2025,
                growth_2023_2025,
                workplace_count_2025,
                source_year_mask,
                is_new_2025,
            ) = row
            province, district = _region(address)
            record = {
                "business_no": business_no,
                "company_name": company_name,
                "primary_workplace_management_no": management_no,
                "zip_code": zip_code,
                "address": address,
                "province": province,
                "district": district,
                "industry_code": industry_code,
                "industry_name": industry_name,
                "workers_2023": workers_2023,
                "workers_2024": workers_2024,
                "workers_2025": workers_2025,
                "growth_2023_2024": growth_2023_2024,
                "growth_2024_2025": growth_2024_2025,
                "growth_2023_2025": growth_2023_2025,
                "workplace_count_2025": workplace_count_2025,
                "source_year_mask": source_year_mask,
                "is_new_2025": bool(is_new_2025),
            }
            writer.writerow(record)
            written += 1
            positive_growth += int(growth_2024_2025 > 0)
            new_2025 += int(bool(is_new_2025))
            if written % 100_000 == 0:
                print(
                    json.dumps(
                        {
                            "stage": "write",
                            "rows": written,
                            "elapsed_seconds": round(time.monotonic() - started, 1),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    result = {
        "compact_companies": written,
        "positive_growth_2024_2025": positive_growth,
        "new_in_2025": new_2025,
    }
    print(
        json.dumps(
            {
                "stage": "compact_complete",
                **result,
                "output": str(output_path.resolve()),
                "elapsed_seconds": round(time.monotonic() - started, 1),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return result


def _year_files(root: Path, year: int) -> list[Path]:
    files = sorted((root / str(year)).glob("*.csv"))
    if not files:
        raise FileNotFoundError(f"{year} CSV 파일을 찾지 못했습니다: {root / str(year)}")
    return files


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "근로복지공단 연도별 원본을 고용인원 1명 이상, 사업자번호 1행으로 압축합니다."
        )
    )
    parser.add_argument(
        "--input-root",
        type=Path,
        default=Path("data/comwel"),
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/comwel/comwel_staging.sqlite"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/comwel/comwel_annual_compact_2023_2025.csv"),
    )
    parser.add_argument("--batch-size", type=int, default=20_000)
    parser.add_argument(
        "--reuse-stage",
        action="store_true",
        help="이미 생성한 SQLite 중간 집계를 재사용합니다.",
    )
    args = parser.parse_args()

    args.database.parent.mkdir(parents=True, exist_ok=True)
    connection = _connect(args.database, rebuild=not args.reuse_stage)
    report: dict[str, Any] = {"years": {}}
    try:
        if not args.reuse_stage:
            for year in (2023, 2024, 2025):
                report["years"][str(year)] = stage_year(
                    connection,
                    year,
                    _year_files(args.input_root, year),
                    batch_size=max(100, args.batch_size),
                )
        report["compact"] = write_compact_csv(connection, args.output)
    finally:
        connection.close()
    print(
        json.dumps(
            {"stage": "complete", **report},
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(
            json.dumps(
                {"stage": "failed", "error": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )
        raise
