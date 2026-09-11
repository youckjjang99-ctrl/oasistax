"""Conservative OCR evidence for CRETOP's three-column summary tables.

This module never replaces the document's text. A verified table is an optional
evidence source; an uncertain crop, column or OCR result stays unconfirmed.
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation


WARNING = "financial_table_ocr_incomplete"
_TITLES = {"income": "요약 손익계산서", "balance": "요약 재무상태표"}
_ACCOUNTS = {
    "income": ("매출액", "영업이익", "당기순이익"),
    "balance": ("자산총계", "부채총계", "자본총계"),
}
_NUMBER = r"[+\-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_CELL = rf"(?:{_NUMBER}|\([ \t]*{_NUMBER}[ \t]*\)|[-–—])"
_YEAR = r"(?:20\d{2}(?:[./-]\d{1,2}[./-]\d{1,2})?년?|[-–—])"
_UNIT = re.compile(r"단위[ \t]*[:：]?[ \t]*(백[ \t]*만[ \t]*원|천[ \t]*원|원)(?![가-힣])")


def _label_pattern(label):
    return r"[ \t]*".join(map(re.escape, re.sub(r"\s+", "", label)))


def _title_pattern(kind):
    return _label_pattern(_TITLES[kind]).replace("[ \\t]*", r"\s*")


def _fix_gridless_labels(text):
    # Only known alphabetic OCR confusions in a geometrically verified crop.
    # Never repair digits, separators, signs or missing numerical cells.
    text = re.sub(r"(?m)^(\s*요약[ \t]*)재무싱태표", r"\1재무상태표", text)
    for wrong, right in (("부채종계", "부채총계"), ("자본종계", "자본총계")):
        text = re.sub(r"(?m)^(\s*)" + wrong + r"(?=[ \t])", r"\1" + right, text)
    return text


def _number_cells(raw):
    if not re.fullmatch(rf"{_CELL}(?:[ \t]+{_CELL})*", raw.strip()):
        return None
    values = []
    for token in re.findall(_CELL, raw):
        if token in {"-", "–", "—"}:
            values.append(None)
            continue
        parenthesized = token.startswith("(")
        token = token.strip("() \t").replace(",", "").replace("−", "-")
        try:
            value = Decimal(token)
        except InvalidOperation:
            return None
        if not value.is_finite() or len(value.as_tuple().digits) > 30:
            return None
        values.append(-abs(value) if parenthesized else value)
    return tuple(values)


def _year_columns(raw):
    if not re.fullmatch(rf"{_YEAR}(?:[ \t]+{_YEAR})*", raw.strip()):
        return None
    columns, years = [], []
    for token in re.findall(_YEAR, raw):
        if token in {"-", "–", "—"}:
            columns.append(None)
            continue
        parts = re.split(r"[./-]", token.rstrip("년"))
        year = int(parts[0])
        if not 2000 <= year <= datetime.now().year + 1:
            return None
        if len(parts) == 3:
            try:
                date = datetime(year, int(parts[1]), int(parts[2]))
            except ValueError:
                return None
            columns.append(date.strftime("%Y.%m.%d"))
        else:
            columns.append(str(year))
        years.append(year)
    # This evidence adapter deliberately supports only the verified three-slot
    # summary layout, not arbitrary full-width or detail statements.
    if len(columns) != 3 or not years or len(years) != len(set(years)):
        return None
    return tuple(columns)


def _parse_summary(text, kind, *, label_corrections=False):
    if kind not in _TITLES or not isinstance(text, str):
        return None
    if label_corrections:
        text = _fix_gridless_labels(text)
    title = re.match(r"\s*" + _title_pattern(kind), text)
    if not title:
        return None
    body = text[title.end():]
    units = list(_UNIT.finditer(body))
    if len(units) != 1:
        return None
    unit_match = units[0]
    # Unit must precede the header/rows, not be borrowed from another table.
    if body[:unit_match.start()].strip():
        return None
    unit = re.sub(r"\s+", "", unit_match.group(1))
    lines = [line.strip() for line in body[unit_match.end():].splitlines() if line.strip()]
    if not lines:
        return None
    header = re.fullmatch(r"(?:구[ \t]*분|계정[ \t]*과목)[ \t]+(.+)", lines[0])
    if not header:
        return None
    columns = _year_columns(header.group(1))
    if columns is None:
        return None
    required = _ACCOUNTS[kind]
    allowed = required + (("자본금",) if kind == "balance" else ())
    values = {}
    for line in lines[1:]:
        found = None
        for label in allowed:
            suffix = r"(?:\((?:손실|순손실)\))?" if label in {"영업이익", "당기순이익"} else ""
            match = re.fullmatch(_label_pattern(label) + suffix + r"[ \t]+(.+)", line)
            if match:
                found = (label, _number_cells(match.group(1)))
                break
        if found is None:
            return None
        label, cells = found
        if label in values or cells is None or len(cells) != len(columns):
            return None
        values[label] = cells
    if not all(label in values for label in required):
        return None
    return {"kind": kind, "unit": unit, "columns": columns, "values": values}


def _normalized_summary(table):
    def cell(value):
        return "-" if value is None else format(value, "f")

    lines = [_TITLES[table["kind"]], "단위: " + table["unit"],
             "구분 " + " ".join(value or "-" for value in table["columns"])]
    for label in _ACCOUNTS[table["kind"]] + (("자본금",) if table["kind"] == "balance" else ()):
        if label in table["values"]:
            lines.append(label + " " + " ".join(cell(value) for value in table["values"][label]))
    return "\n".join(lines)


def _agreeing_summary(first, second, kind, *, label_corrections=False):
    a = _parse_summary(first, kind, label_corrections=label_corrections)
    b = _parse_summary(second, kind, label_corrections=label_corrections)
    return _normalized_summary(a) if a is not None and a == b else None


def _heading_box(data, kind):
    lines = {}
    for i, word in enumerate(data.get("text", ())):
        if not isinstance(word, str) or not word.strip():
            continue
        key = tuple(data[name][i] for name in ("block_num", "par_num", "line_num"))
        lines.setdefault(key, []).append(i)
    matches = []
    wanted = re.sub(r"\s+", "", _TITLES[kind])
    for indices in lines.values():
        if wanted in re.sub(r"\s+", "", "".join(data["text"][i] for i in indices)):
            matches.append((min(data["top"][i] for i in indices),
                            max(data["top"][i] + data["height"][i] for i in indices)))
    return matches[0] if len(matches) == 1 else None


def _table_crop(full_image, data, kind):
    """Locate title + a contiguous left-hand grid; reject a full-width grid."""
    import numpy as np

    heading = _heading_box(data, kind)
    if heading is None:
        return None
    width, height = full_image.size
    half = width // 2
    if half < 40 or height < 80:
        return None
    gray = np.min(np.asarray(full_image.convert("RGB")), axis=2) < 230
    rows = np.flatnonzero(np.sum(gray[:, :half], axis=1) > half * .85)
    rows = rows[rows > heading[1]]
    groups = np.split(rows, np.flatnonzero(np.diff(rows) != 1) + 1)
    chosen = []
    for group in groups:
        if not len(group):
            continue
        if not chosen and group[0] - heading[1] > height * .1:
            return None
        if chosen and group[0] - chosen[-1][-1] > height * .06:
            break
        chosen.append(group)
    minimum = 4 if kind == "income" else 5
    if not minimum <= len(chosen) <= 12:
        return None
    # The full source, not the cropped half, proves that no year/value columns
    # continue through the midpoint. A chart must have a genuine white gutter.
    band = max(3, int(width * .01))
    selected_rows = np.concatenate(chosen)
    if np.any(np.mean(gray[selected_rows, half-band:half+band], axis=1) >= .85):
        return None
    # Require space between the table's right edge and the crop boundary too.
    edge = max(2, int(width * .002))
    if np.any(np.all(gray[selected_rows, half-edge:half+edge], axis=1)):
        return None
    # Independently verify the account column + three year columns in pixels.
    # A chart's horizontal grid alone is not evidence of a financial table.
    grid_top, grid_bottom = int(chosen[0][0]), int(chosen[-1][-1])
    rule_xs = np.flatnonzero(gray[grid_top, :half])
    if not len(rule_xs):
        return None
    left, right = int(rule_xs[0]), int(rule_xs[-1])
    table_width = right - left
    if table_width <= 0 or grid_bottom <= grid_top:
        return None
    columns = np.flatnonzero(np.sum(gray[grid_top:grid_bottom+1, :half], axis=0)
                             > (grid_bottom-grid_top+1) * .85)
    columns = columns[(columns > left + table_width * .08) & (columns < right - table_width * .08)]
    column_groups = [group for group in np.split(columns, np.flatnonzero(np.diff(columns) != 1) + 1) if len(group)]
    if (len(column_groups) != 3
            or any(len(group) > max(3, table_width * .02) for group in column_groups)
            or any(abs((float(np.mean(group))-left)/table_width - expected) > .08
                   for group, expected in zip(column_groups, (.25, .5, .75)))):
        return None
    margin = max(4, int(height * .005))
    top, bottom = max(0, heading[0] - margin), min(height, int(chosen[-1][-1]) + margin)
    return full_image.crop((0, top, half, bottom))


def _without_thin_rules(image):
    import numpy as np
    from PIL import Image, ImageOps

    pixels = np.array(image.convert("RGB"))
    mask = np.min(pixels, axis=2) < 230
    candidates = np.flatnonzero(np.sum(mask, axis=1) > image.width * .85)
    groups = np.split(candidates, np.flatnonzero(np.diff(candidates) != 1) + 1)
    thick = [group for group in groups if len(group) > max(4, image.height * .01)]
    if not thick:
        return image
    # Work only inside the table so a column rule cannot cut the title/unit.
    table_top = int(thick[0][0])
    thin = [group for group in groups if 0 < len(group) <= max(4, image.height * .01)]
    for group in thin:
        for y in group:
            if y >= table_top:
                pixels[max(table_top, y-1):min(image.height, y+2), :, :] = 255
    columns = np.flatnonzero(np.sum(mask[table_top:], axis=0) > (image.height-table_top) * .85)
    for x in columns:
        pixels[table_top:, max(0, x-1):min(image.width, x+2), :] = 255
    return ImageOps.autocontrast(ImageOps.grayscale(Image.fromarray(pixels)))


def _render_page(document, index):
    import fitz
    from PIL import Image

    page = document[index]
    if page.rect.width * page.rect.height * 6.25 > 30_000_000:
        raise ValueError("financial_table_page_too_large")
    pixmap = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), alpha=False)
    return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)


def _ocr_data(image, timeout):
    import pytesseract
    from cretop_worker import _require_korean_ocr_languages

    _require_korean_ocr_languages()
    return pytesseract.image_to_data(image, lang="kor+eng", config="--oem 1 --psm 4",
                                    output_type=pytesseract.Output.DICT, timeout=timeout)


def _ocr_text(image, psm, timeout):
    import pytesseract

    return pytesseract.image_to_string(image, lang="kor+eng", config=f"--oem 1 --psm {psm}", timeout=timeout) or ""


def get_verified_financial_tables(document, index, text, *, deadline):
    """Return separate normalized evidence and safe warning codes, never PII."""
    kinds = [kind for kind in _TITLES if re.search(_title_pattern(kind), text or "")]
    if not kinds:
        return {}, []
    evidence = {}
    extra_psm_used = False
    try:
        if deadline - time.monotonic() <= 1:
            return {}, [WARNING]
        image = _render_page(document, index)
        if deadline - time.monotonic() <= 1:
            return {}, [WARNING]
        data = _ocr_data(image.crop((0, 0, image.width // 2, image.height)),
                         min(12, deadline - time.monotonic()))
        for kind in kinds[:2]:
            if deadline - time.monotonic() <= 1:
                break
            crop = _table_crop(image, data, kind)
            if crop is None:
                continue
            # Never merge individual accounts. Complete valid readings must all
            # agree, including across preprocessing; a majority cannot override
            # one conflicting, structurally valid reading.
            readings = []
            conflicted = False
            for remove_rules in (False, True):
                candidate_image = _without_thin_rules(crop) if remove_rules else crop
                results = []
                for psm in (4, 6):
                    remaining = deadline - time.monotonic()
                    if remaining <= 1:
                        break
                    results.append(_ocr_text(candidate_image, psm, min(12, remaining)))
                if len(results) != 2:
                    break
                for psm, result in zip((4, 6), results):
                    parsed = _parse_summary(result, kind, label_corrections=remove_rules)
                    if parsed is not None:
                        readings.append((psm, parsed))
                if readings and any(table != readings[0][1] for _, table in readings):
                    conflicted = True
                    break
                if len({psm for psm, _ in readings}) >= 2:
                    evidence[kind] = _normalized_summary(readings[0][1])
                    break
            if (kind not in evidence and readings and not conflicted and not extra_psm_used
                    and deadline - time.monotonic() > 1):
                # Sparse shaded headers occasionally fail PSM6. Permit one
                # additional, different segmentation mode per page, not an
                # unbounded search for a preferred numerical result.
                extra_psm_used = True
                candidate = _ocr_text(_without_thin_rules(crop), 3, min(12, deadline-time.monotonic()))
                parsed = _parse_summary(candidate, kind, label_corrections=True)
                if parsed is not None and all(parsed == table for _, table in readings):
                    evidence[kind] = _normalized_summary(parsed)
    except Exception:
        # Optional evidence cannot turn usable document text into a fatal error,
        # nor disclose OCR input/exception text in logs or warnings.
        pass
    return evidence, ([WARNING] if len(evidence) != len(kinds) else [])
