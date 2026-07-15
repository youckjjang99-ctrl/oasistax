from __future__ import annotations

import io
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

import fitz
import numpy as np
import pytesseract
from PIL import Image, ImageEnhance, ImageFilter, ImageOps


ProgressCallback = Callable[[int, int, str], None]


@dataclass
class PageResult:
    page_number: int
    rotation: int
    orientation_method: str
    quality_score: float
    korean_ratio: float
    text_length: int
    article_markers: int
    deskew_angle: float
    text: str


def _clean_spaces(text: str) -> str:
    text = str(text or "").replace("\x0c", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def text_quality(text: str) -> dict[str, Any]:
    compact = re.sub(r"\s+", "", str(text or ""))
    length = len(compact)
    if not length:
        return {
            "score": 0.0,
            "korean_ratio": 0.0,
            "garbage_ratio": 1.0,
            "article_markers": 0,
            "length": 0,
        }

    korean = len(re.findall(r"[가-힣]", compact))
    latin = len(re.findall(r"[A-Za-z]", compact))
    digits = len(re.findall(r"[0-9]", compact))
    useful = korean + latin + digits
    garbage = max(length - useful, 0)
    korean_ratio = korean / length
    garbage_ratio = garbage / length
    article_markers = len(
        re.findall(
            r"(?:제\s*\d+\s*(?:조|장)|정\s*관|주\s*주\s*총\s*회|이\s*사\s*회|임\s*원|퇴\s*직)",
            text,
        )
    )

    score = (
        min(length, 1800) / 18
        + korean_ratio * 220
        + min(article_markers, 20) * 9
        - garbage_ratio * 75
    )
    return {
        "score": round(score, 2),
        "korean_ratio": round(korean_ratio, 4),
        "garbage_ratio": round(garbage_ratio, 4),
        "article_markers": article_markers,
        "length": length,
    }


def document_quality(text: str) -> dict[str, Any]:
    metrics = text_quality(text)
    score = float(metrics["score"])
    if metrics["length"] >= 800 and metrics["korean_ratio"] >= 0.20:
        grade = "우수"
    elif metrics["length"] >= 300 and metrics["korean_ratio"] >= 0.10:
        grade = "보통"
    elif score >= 55:
        grade = "재검토"
    else:
        grade = "실패"
    return {**metrics, "grade": grade}


def _pil_from_pixmap(pixmap: fitz.Pixmap) -> Image.Image:
    mode = "RGBA" if pixmap.alpha else "RGB"
    return Image.frombytes(
        mode,
        [pixmap.width, pixmap.height],
        pixmap.samples,
    ).convert("RGB")


def _deskew_with_cv(image: Image.Image) -> tuple[Image.Image, float]:
    try:
        import cv2
    except Exception:
        return image, 0.0

    array = np.array(image.convert("L"))
    inverted = cv2.bitwise_not(array)
    _, threshold = cv2.threshold(
        inverted,
        0,
        255,
        cv2.THRESH_BINARY | cv2.THRESH_OTSU,
    )
    coordinates = np.column_stack(np.where(threshold > 0))
    if len(coordinates) < 200:
        return image, 0.0

    angle = cv2.minAreaRect(coordinates)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle

    if abs(angle) < 0.25 or abs(angle) > 8:
        return image, 0.0

    height, width = array.shape
    center = (width // 2, height // 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(
        np.array(image),
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return Image.fromarray(rotated), round(float(angle), 2)


def preprocess_image(image: Image.Image) -> tuple[Image.Image, float]:
    image = ImageOps.exif_transpose(image).convert("RGB")
    image = ImageOps.grayscale(image)
    image = ImageOps.autocontrast(image, cutoff=1)
    image = image.filter(ImageFilter.MedianFilter(size=3))
    image = ImageEnhance.Contrast(image).enhance(1.35)
    image = ImageEnhance.Sharpness(image).enhance(1.45)
    image, angle = _deskew_with_cv(image)
    return image, angle


def _osd_rotation(image: Image.Image) -> int | None:
    try:
        result = pytesseract.image_to_osd(
            image,
            config="--psm 0",
            output_type=pytesseract.Output.DICT,
        )
        rotate = int(result.get("rotate", 0) or 0)
        return rotate if rotate in {0, 90, 180, 270} else None
    except Exception:
        return None


def _sample_for_orientation(image: Image.Image) -> Image.Image:
    sample = image.copy()
    max_side = max(sample.size)
    if max_side > 1500:
        ratio = 1500 / max_side
        sample = sample.resize(
            (
                max(1, int(sample.width * ratio)),
                max(1, int(sample.height * ratio)),
            )
        )
    return sample


def choose_rotation(
    image: Image.Image,
    lang: str = "kor+eng",
) -> tuple[int, str, dict[int, dict[str, Any]]]:
    sample = _sample_for_orientation(image)
    osd = _osd_rotation(sample)

    candidate_order = []
    if osd is not None:
        candidate_order.append(osd)
        candidate_order.append((osd + 180) % 360)
    candidate_order.extend([0, 90, 180, 270])
    candidates = list(dict.fromkeys(candidate_order))

    scores: dict[int, dict[str, Any]] = {}
    for angle in candidates:
        rotated = sample.rotate(angle, expand=True, fillcolor="white")
        text = pytesseract.image_to_string(
            rotated,
            lang=lang,
            config="--oem 1 --psm 6",
        )
        scores[angle] = {
            **text_quality(text),
            "preview": _clean_spaces(text)[:300],
        }

    best_angle = max(
        scores,
        key=lambda angle: (
            scores[angle]["score"],
            scores[angle]["korean_ratio"],
            scores[angle]["length"],
        ),
    )
    method = "OSD+품질비교" if osd is not None else "4방향 품질비교"
    return best_angle, method, scores


def restore_korean_structure(text: str) -> str:
    text = _clean_spaces(text)
    replacements = [
        (r"제\s*(\d+)\s*장", r"제\1장"),
        (r"제\s*(\d+)\s*조", r"제\1조"),
        (r"제\s*(\d+)\s*절", r"제\1절"),
        (r"부\s*칙", "부칙"),
        (r"주\s*주\s*총\s*회", "주주총회"),
        (r"이\s*사\s*회", "이사회"),
        (r"자\s*기\s*주\s*식", "자기주식"),
        (r"임\s*원\s*퇴\s*직\s*금", "임원퇴직금"),
        (r"유\s*족\s*보\s*상\s*금", "유족보상금"),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)

    lines = [line.strip() for line in text.splitlines()]
    restored: list[str] = []
    buffer = ""

    def flush() -> None:
        nonlocal buffer
        if buffer.strip():
            restored.append(buffer.strip())
            buffer = ""

    for line in lines:
        if not line:
            flush()
            if restored and restored[-1] != "":
                restored.append("")
            continue

        heading = bool(
            re.match(
                r"^(?:\[페이지\s*\d+\]|제\d+(?:장|절|조)|부칙|정관|제\s*\d+\s*(?:장|절|조))",
                line,
            )
        )
        enumerated = bool(
            re.match(r"^(?:①|②|③|④|⑤|⑥|⑦|⑧|⑨|⑩|\d+[.)]|[가-힣][.)])", line)
        )

        if heading or enumerated:
            flush()
            restored.append(line)
            continue

        if buffer:
            if buffer.endswith((".", "다.", "함.", "한다.", "있다.", "없다.", "한다")):
                flush()
            else:
                buffer += " " + line
                continue
        buffer = line

    flush()
    return "\n".join(restored).strip()


def _ocr_page(
    image: Image.Image,
    page_number: int,
    lang: str,
) -> PageResult:
    prepared, deskew_angle = preprocess_image(image)
    rotation, method, _ = choose_rotation(prepared, lang=lang)
    oriented = prepared.rotate(rotation, expand=True, fillcolor="white")
    text = pytesseract.image_to_string(
        oriented,
        lang=lang,
        config="--oem 1 --psm 6",
    )
    text = restore_korean_structure(text)
    metrics = text_quality(text)
    return PageResult(
        page_number=page_number,
        rotation=rotation,
        orientation_method=method,
        quality_score=float(metrics["score"]),
        korean_ratio=float(metrics["korean_ratio"]),
        text_length=int(metrics["length"]),
        article_markers=int(metrics["article_markers"]),
        deskew_angle=deskew_angle,
        text=text,
    )


def preprocess_pdf(
    data: bytes,
    progress_callback: ProgressCallback | None = None,
    max_pages: int = 120,
    lang: str = "kor+eng",
) -> tuple[str, dict[str, Any]]:
    document = fitz.open(stream=data, filetype="pdf")
    total_pages = len(document)
    processed_pages = min(total_pages, max_pages)

    embedded_parts: list[str] = []
    for index in range(processed_pages):
        embedded_parts.append(document[index].get_text("text") or "")
    embedded_text = restore_korean_structure("\n".join(embedded_parts))
    embedded_quality = document_quality(embedded_text)

    if (
        embedded_quality["length"] >= 500
        and embedded_quality["korean_ratio"] >= 0.12
        and embedded_quality["grade"] in {"우수", "보통"}
    ):
        return embedded_text, {
            "method": "embedded_text",
            "page_count": total_pages,
            "processed_pages": processed_pages,
            "recognized_pages": processed_pages,
            "truncated": total_pages > max_pages,
            "quality": embedded_quality,
            "pages": [],
        }

    page_results: list[PageResult] = []
    for index in range(processed_pages):
        if progress_callback:
            progress_callback(
                index,
                processed_pages,
                f"{index + 1}페이지 방향·기울기 분석 중",
            )
        page = document[index]
        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(2.0, 2.0),
            alpha=False,
        )
        image = _pil_from_pixmap(pixmap)
        result = _ocr_page(
            image,
            page_number=index + 1,
            lang=lang,
        )
        page_results.append(result)
        if progress_callback:
            progress_callback(
                index + 1,
                processed_pages,
                f"{index + 1}/{processed_pages}페이지 OCR 완료",
            )

    combined = "\n\n".join(
        f"[페이지 {result.page_number}]\n{result.text}"
        for result in page_results
        if result.text.strip()
    )
    combined = restore_korean_structure(combined)
    quality = document_quality(combined)

    return combined, {
        "method": "smart_ocr",
        "page_count": total_pages,
        "processed_pages": processed_pages,
        "recognized_pages": sum(
            1 for result in page_results if result.text_length >= 20
        ),
        "truncated": total_pages > max_pages,
        "quality": quality,
        "pages": [
            {
                key: value
                for key, value in asdict(result).items()
                if key != "text"
            }
            for result in page_results
        ],
    }


def preprocess_image_file(
    data: bytes,
    progress_callback: ProgressCallback | None = None,
    lang: str = "kor+eng",
) -> tuple[str, dict[str, Any]]:
    image = Image.open(io.BytesIO(data)).convert("RGB")
    if progress_callback:
        progress_callback(0, 1, "이미지 방향·기울기 분석 중")
    result = _ocr_page(image, page_number=1, lang=lang)
    if progress_callback:
        progress_callback(1, 1, "이미지 OCR 완료")
    quality = document_quality(result.text)
    return result.text, {
        "method": "smart_ocr",
        "page_count": 1,
        "processed_pages": 1,
        "recognized_pages": int(result.text_length >= 20),
        "truncated": False,
        "quality": quality,
        "pages": [
            {
                key: value
                for key, value in asdict(result).items()
                if key != "text"
            }
        ],
    }


def preprocess_document(
    filename: str,
    data: bytes,
    progress_callback: ProgressCallback | None = None,
) -> tuple[str, dict[str, Any]]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return preprocess_pdf(
            data,
            progress_callback=progress_callback,
        )
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}:
        return preprocess_image_file(
            data,
            progress_callback=progress_callback,
        )
    raise ValueError("문서 전처리 엔진은 PDF, PNG, JPG, WEBP, TIFF를 지원합니다.")
