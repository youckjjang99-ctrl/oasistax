from __future__ import annotations

import io
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


def _font_path(bold: bool = False) -> str:
    candidates = (
        [
            "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
            "/usr/share/fonts/truetype/nanum/NanumBarunGothicBold.ttf",
        ]
        if bold
        else [
            "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
            "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
            "/usr/share/fonts/truetype/unfonts-core/UnDotum.ttf",
        ]
    )
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    raise RuntimeError(
        "한글 PDF 폰트를 찾지 못했습니다. packages.txt의 fonts-nanum 설치를 확인해주세요."
    )


def _register_fonts() -> tuple[str, str]:
    regular_name = "OasisKorean"
    bold_name = "OasisKoreanBold"
    if regular_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(regular_name, _font_path(False)))
    if bold_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(bold_name, _font_path(True)))
    return regular_name, bold_name


def _escape(value: Any) -> str:
    text = str(value or "")
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )


def _styles():
    regular, bold = _register_fonts()
    styles = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "OasisTitle",
            fontName=bold,
            fontSize=18,
            leading=25,
            alignment=TA_CENTER,
            spaceAfter=12,
        ),
        "subtitle": ParagraphStyle(
            "OasisSubtitle",
            fontName=bold,
            fontSize=12,
            leading=17,
            alignment=TA_LEFT,
            spaceBefore=8,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "OasisBody",
            fontName=regular,
            fontSize=9.5,
            leading=15,
            alignment=TA_LEFT,
            wordWrap="CJK",
            spaceAfter=4,
        ),
        "small": ParagraphStyle(
            "OasisSmall",
            fontName=regular,
            fontSize=8,
            leading=12,
            wordWrap="CJK",
        ),
        "bold": ParagraphStyle(
            "OasisBold",
            fontName=bold,
            fontSize=9,
            leading=13,
            wordWrap="CJK",
        ),
    }


def _footer(canvas, doc):
    regular, _ = _register_fonts()
    canvas.saveState()
    canvas.setFont(regular, 8)
    canvas.drawString(18 * mm, 10 * mm, "OASIS 정관 개정 검토용 문서")
    canvas.drawRightString(
        A4[0] - 18 * mm,
        10 * mm,
        f"{doc.page}",
    )
    canvas.restoreState()


def build_revised_articles_pdf(
    company_name: str,
    business_no: str,
    final_text: str,
    version_name: str,
) -> bytes:
    styles = _styles()
    output = io.BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"{company_name} 정관 개정안",
    )
    story = [
        Paragraph(_escape(f"{company_name} 정관 개정안"), styles["title"]),
        Paragraph(
            _escape(
                f"사업자등록번호: {business_no or '-'} | "
                f"버전: {version_name} | 생성일: {datetime.now():%Y-%m-%d}"
            ),
            styles["small"],
        ),
        Spacer(1, 8 * mm),
    ]

    for line in str(final_text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            story.append(Spacer(1, 3 * mm))
            continue
        if re.match(r"^제\s*\d+\s*장", stripped):
            story.append(Paragraph(_escape(stripped), styles["subtitle"]))
        elif re.match(r"^제\s*\d+\s*조", stripped):
            story.append(Spacer(1, 2 * mm))
            story.append(Paragraph(_escape(stripped), styles["bold"]))
        elif stripped in {"정관 개정 권고 조항", "부칙"}:
            story.append(PageBreak())
            story.append(Paragraph(_escape(stripped), styles["subtitle"]))
        else:
            story.append(Paragraph(_escape(stripped), styles["body"]))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return output.getvalue()


def build_comparison_pdf(
    company_name: str,
    business_no: str,
    comparisons: list[dict[str, Any]],
    version_name: str,
) -> bytes:
    styles = _styles()
    output = io.BytesIO()
    page_size = landscape(A4)
    doc = SimpleDocTemplate(
        output,
        pagesize=page_size,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=f"{company_name} 신구조문 대비표",
    )
    story = [
        Paragraph(_escape(f"{company_name} 신·구조문 대비표"), styles["title"]),
        Paragraph(
            _escape(
                f"사업자등록번호: {business_no or '-'} | "
                f"버전: {version_name} | 생성일: {datetime.now():%Y-%m-%d}"
            ),
            styles["small"],
        ),
        Spacer(1, 5 * mm),
    ]
    header = [
        Paragraph("검토조항", styles["bold"]),
        Paragraph("개정 전", styles["bold"]),
        Paragraph("개정 후", styles["bold"]),
        Paragraph("개정 목적·확인사항", styles["bold"]),
    ]
    rows = [header]
    for item in comparisons:
        reason = str(item.get("reason", "") or "")
        checks = ", ".join(item.get("checks", []) or [])
        if checks:
            reason += f"\n확인: {checks}"
        rows.append(
            [
                Paragraph(_escape(item.get("title", "")), styles["small"]),
                Paragraph(_escape(item.get("before", "관련 조항 미확인")), styles["small"]),
                Paragraph(_escape(item.get("after", "")), styles["small"]),
                Paragraph(_escape(reason), styles["small"]),
            ]
        )

    table = Table(
        rows,
        colWidths=[38 * mm, 76 * mm, 94 * mm, 62 * mm],
        repeatRows=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "OasisKorean"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF8")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#A8B3C7")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(table)
    doc.build(story)
    return output.getvalue()


def build_review_report_pdf(
    company_name: str,
    business_no: str,
    review: dict[str, Any],
    comparisons: list[dict[str, Any]],
    version_name: str,
) -> bytes:
    styles = _styles()
    output = io.BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"{company_name} 정관 검토보고서",
    )
    story = [
        Paragraph(_escape(f"{company_name} 정관 검토보고서"), styles["title"]),
        Paragraph(
            _escape(
                f"사업자등록번호: {business_no or '-'} | "
                f"정관점수: {review.get('score', 0)}점 | "
                f"버전: {version_name}"
            ),
            styles["body"],
        ),
        Spacer(1, 4 * mm),
        Paragraph("1. 우선 개정 검토사항", styles["subtitle"]),
    ]
    priorities = review.get("priority_items", []) or []
    if priorities:
        for index, item in enumerate(priorities, start=1):
            story.append(
                Paragraph(
                    _escape(
                        f"{index}. {item.get('title', '')} - "
                        f"{item.get('recommendation', '')}"
                    ),
                    styles["body"],
                )
            )
    else:
        story.append(Paragraph("주요 우선개정 항목이 확인되지 않았습니다.", styles["body"]))

    story.append(Paragraph("2. 이번 개정안 반영항목", styles["subtitle"]))
    if comparisons:
        for index, item in enumerate(comparisons, start=1):
            story.append(
                Paragraph(
                    _escape(
                        f"{index}. {item.get('title', '')}: "
                        f"{item.get('reason', '')}"
                    ),
                    styles["body"],
                )
            )
    else:
        story.append(Paragraph("사용자가 적용한 개정조항이 없습니다.", styles["body"]))

    story.append(Paragraph("3. 후속 확인자료", styles["subtitle"]))
    followups = [
        "최종 개정문구 및 조항번호",
        "주주총회 또는 이사회 결의 필요 여부",
        "주주총회 의사록 및 별도 지급규정",
        "시행일과 적용대상",
        "지급배율·지급한도·행사가격 등 미확정 숫자",
    ]
    for item in followups:
        story.append(Paragraph(_escape(f"- {item}"), styles["body"]))

    story.append(Spacer(1, 5 * mm))
    story.append(
        Paragraph(
            "본 문서는 시스템에서 생성한 정관 개정 검토용 초안입니다. "
            "사용자가 확정한 문구와 회사의 실제 결의·등기 절차를 기준으로 최종본을 관리합니다.",
            styles["small"],
        )
    )
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return output.getvalue()
