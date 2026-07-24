from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import openpyxl


def _category(service_name: str) -> str:
    match = re.search(r"행정안전부_([^_]+)_", service_name)
    return match.group(1).strip() if match else "기타"


def _label(service_name: str) -> str:
    value = re.sub(r"^행정안전부_[^_]+_", "", service_name)
    return re.sub(r"\s*조회서비스\s*$", "", value).strip()


def build_catalog(source_path: Path) -> list[dict[str, object]]:
    workbook = openpyxl.load_workbook(
        source_path,
        read_only=True,
        data_only=True,
    )
    sheet = workbook["조회"]
    catalog: list[dict[str, object]] = []
    for row in sheet.iter_rows(min_row=2, values_only=True):
        number, service_name, operation_name, url = row[:4]
        if not service_name or not url:
            continue
        parsed = urlparse(str(url).replace("http://", "https://", 1))
        parts = [part for part in parsed.path.split("/") if part]
        service_key = parts[-2] if len(parts) >= 2 else f"service_{number}"
        catalog.append(
            {
                "order": int(number),
                "key": service_key,
                "category": _category(str(service_name)),
                "label": _label(str(service_name)),
                "service_name": str(service_name),
                "operation_name": str(operation_name or ""),
                "url": f"https://{parsed.netloc}{parsed.path}",
                "history_url": (
                    f"https://{parsed.netloc}"
                    f"{parsed.path.rsplit('/', 1)[0]}/history"
                ),
                "catalog_url": "",
            }
        )
    return catalog


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(
            "usage: generate_localdata_catalog.py SOURCE.xlsx OUTPUT.json"
        )
    source_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    catalog = build_catalog(source_path)
    if len(catalog) != 195:
        raise RuntimeError(f"expected 195 services, got {len(catalog)}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
