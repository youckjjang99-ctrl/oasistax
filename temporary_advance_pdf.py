from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from temporary_advance_calculator import (
    company_burden_text,
    format_estimate_range,
    representative_burden_text,
)


NAVY = colors.HexColor("#0B2B5B")
BLUE = colors.HexColor("#1E5BD7")
GREEN = colors.HexColor("#16835F")
RED = colors.HexColor("#C43D3D")
LIGHT_BLUE = colors.HexColor("#EAF2FF")
LIGHT_GREEN = colors.HexColor("#EAF8F2")
LIGHT_RED = colors.HexColor("#FFF0F0")
GRID = colors.HexColor("#D7E2F0")
TEXT = colors.HexColor("#172033")
MUTED = colors.HexColor("#667085")


def _register_fonts() -> tuple[str, str]:
    project_dir = Path(__file__).resolve().parent
    candidates = [
        (
            project_dir / "assets" / "NanumGothic.ttf",
            project_dir / "assets" / "NanumGothicBold.ttf",
        ),
        (
            Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
            Path("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
        ),
        (
            Path("/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf"),
            Path("/usr/share/fonts/truetype/nanum/NanumBarunGothicBold.ttf"),
        ),
    ]
    if "AdvanceKR" in pdfmetrics.getRegisteredFontNames():
        return "AdvanceKR", "AdvanceKR-Bold"

    for regular, bold in candidates:
        if regular.is_file() and bold.is_file():
            try:
                pdfmetrics.registerFont(TTFont("AdvanceKR", str(regular)))
                pdfmetrics.registerFont(TTFont("AdvanceKR-Bold", str(bold)))
                return "AdvanceKR", "AdvanceKR-Bold"
            except Exception:
                continue

    cid_name = "HYSMyeongJo-Medium"
    if cid_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(cid_name))
    return cid_name, cid_name


def _money(value: Any) -> str:
    try:
        return f"{int(round(float(value or 0))):,}원"
    except (TypeError, ValueError):
        return "0원"


def build_temporary_advance_pdf(
    company_name: str,
    business_no: str,
    result: dict[str, Any],
    consultant_name: str = "",
) -> bytes:
    normal, bold = _register_fonts()
    output = BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=13 * mm,
        rightMargin=13 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=f"{company_name or '기업'} 가지급금 사전진단",
        author="OASIS",
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "advance_title",
        parent=styles["Heading1"],
        fontName=bold,
        fontSize=18,
        leading=23,
        textColor=NAVY,
        spaceAfter=3,
    )
    subtitle = ParagraphStyle(
        "advance_subtitle",
        parent=styles["BodyText"],
        fontName=normal,
        fontSize=8,
        leading=11,
        textColor=MUTED,
    )
    body = ParagraphStyle(
        "advance_body",
        parent=styles["BodyText"],
        fontName=normal,
        fontSize=7.6,
        leading=10.8,
        textColor=TEXT,
    )
    body_bold = ParagraphStyle(
        "advance_body_bold",
        parent=body,
        fontName=bold,
    )
    table_header = ParagraphStyle(
        "advance_table_header",
        parent=body_bold,
        textColor=colors.white,
    )
    small = ParagraphStyle(
        "advance_small",
        parent=body,
        fontSize=6.7,
        leading=9.2,
        textColor=MUTED,
    )
    section = ParagraphStyle(
        "advance_section",
        parent=body,
        fontName=bold,
        fontSize=10.5,
        leading=14,
        textColor=NAVY,
    )

    inputs = result.get("inputs", {}) or {}
    representative = result.get("representative", {}) or {}
    company = result.get("company", {}) or {}
    estimation = result.get("estimation", {}) or {}
    diagnosis_status = (
        "확인값 반영"
        if not estimation.get("has_unknowns")
        else "일부 입력 미확인/범위 표시"
    )

    story = [
        Paragraph("가지급금 세무/4대보험 사전진단", title),
        Paragraph(
            f"{company_name or '-'} / 사업자번호 {business_no or '-'} / "
            f"작성일 {datetime.now().strftime('%Y-%m-%d')} / "
            f"담당 {consultant_name or '-'} / {diagnosis_status}",
            subtitle,
        ),
        Spacer(1, 4 * mm),
    ]

    assumptions = [
        [
            Paragraph("가지급금 잔액", body_bold),
            Paragraph(_money(inputs.get("balance")), body),
            Paragraph("인정이자율/기간", body_bold),
            Paragraph(
                f"{float(inputs.get('recognized_interest_rate_pct', 0) or 0):.2f}% / "
                f"{int(inputs.get('days', 0) or 0)}일",
                body,
            ),
        ],
        [
            Paragraph("대표자 기존 과세표준", body_bold),
            Paragraph(
                (
                    _money(inputs.get("representative_tax_base"))
                    if inputs.get("representative_tax_base_known")
                    else "미확인(범위 표시)"
                ),
                body,
            ),
            Paragraph("법인 기존 과세표준", body_bold),
            Paragraph(
                (
                    _money(inputs.get("corporate_tax_base"))
                    if inputs.get("corporate_tax_base_known")
                    else "미확인(범위 표시)"
                ),
                body,
            ),
        ],
        [
            Paragraph("실제 회수 이자", body_bold),
            Paragraph(_money(inputs.get("received_interest")), body),
            Paragraph("소득처분 시나리오", body_bold),
            Paragraph(str(inputs.get("disposition_mode", "-")), body),
        ],
    ]
    assumptions_table = Table(
        assumptions,
        colWidths=[33 * mm, 49 * mm, 36 * mm, 53 * mm],
    )
    assumptions_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), normal),
                ("BACKGROUND", (0, 0), (0, -1), LIGHT_BLUE),
                ("BACKGROUND", (2, 0), (2, -1), LIGHT_BLUE),
                ("GRID", (0, 0), (-1, -1), 0.35, GRID),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story += [assumptions_table, Spacer(1, 4 * mm)]

    kpi_data = [
        [
            Paragraph("연간 인정이자", body_bold),
            Paragraph(_money(company.get("recognized_interest")), body_bold),
            Paragraph("대표자 세금/보험", body_bold),
            Paragraph(representative_burden_text(result), body_bold),
            Paragraph("법인 세금/보험", body_bold),
            Paragraph(company_burden_text(result), body_bold),
        ]
    ]
    kpi_table = Table(kpi_data, colWidths=[29 * mm, 30 * mm] * 3)
    kpi_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (1, 0), LIGHT_BLUE),
                ("BACKGROUND", (2, 0), (3, 0), LIGHT_RED),
                ("BACKGROUND", (4, 0), (5, 0), LIGHT_GREEN),
                ("TEXTCOLOR", (1, 0), (1, 0), BLUE),
                ("TEXTCOLOR", (3, 0), (3, 0), RED),
                ("TEXTCOLOR", (5, 0), (5, 0), GREEN),
                ("BOX", (0, 0), (-1, -1), 0.5, GRID),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, GRID),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("ALIGN", (3, 0), (3, 0), "RIGHT"),
                ("ALIGN", (5, 0), (5, 0), "RIGHT"),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story += [kpi_table, Spacer(1, 4 * mm)]

    representative_rows = [
        ["대표자 관점", "추정금액", "계산 의미"],
        [
            "상여처분 소득",
            _money(representative.get("bonus_disposition")),
            "미회수 인정이자를 대표자 근로소득으로 처분하는 가정",
        ],
        [
            "소득세/지방소득세",
            format_estimate_range(
                representative.get(
                    "tax_total_min",
                    representative.get("tax_total", 0),
                ),
                representative.get(
                    "tax_total_max",
                    representative.get("tax_total", 0),
                ),
            ),
            (
                "과세표준 확인값"
                if representative.get("tax_base_known")
                else "과세표준 미확인/최저~최고 세율 범위"
            ),
        ],
        [
            "대표자 4대보험",
            (
                "미확인"
                if representative.get("insurance_pending")
                else _money(representative.get("insurance_total"))
            ),
            "직장가입/월 보수와 피보험자격 기준",
        ],
        [
            "대표자 예상 부담",
            representative_burden_text(result),
            "세금 + 확인된 대표자 부담 보험료",
        ],
    ]
    company_rows = [
        ["법인 관점", "추정금액", "계산 의미"],
        [
            "연간 인정이자",
            _money(company.get("recognized_interest")),
            "회사에 귀속되어야 할 이자수익",
        ],
        [
            "지급이자 손금불산입",
            _money(company.get("disallowed_interest")),
            "차입금/지급이자 입력값에 따른 단순 적수 가정",
        ],
        [
            "법인세/지방소득세",
            format_estimate_range(
                company.get(
                    "tax_total_min",
                    company.get("tax_total", 0),
                ),
                company.get(
                    "tax_total_max",
                    company.get("tax_total", 0),
                ),
            ),
            (
                "과세표준 확인값"
                if company.get("tax_base_known")
                else "과세표준 미확인/최저~최고 세율 범위"
            ),
        ],
        [
            "법인 부담 4대보험",
            (
                "미확인"
                if company.get("insurance_pending")
                else _money(company.get("employer_insurance_total"))
            ),
            "상여의 보수반영과 피보험자격 기준",
        ],
        [
            "법인 추가 세금/보험",
            company_burden_text(result),
            "미회수 인정이자 제외",
        ],
        [
            "법인 총 재무 노출",
            company_burden_text(
                result,
                include_uncollected_interest=True,
            ),
            "회수 가능한 채권을 포함/확정 손실 아님",
        ],
    ]

    def result_table(rows: list[list[str]], header_color) -> Table:
        rendered = []
        for row_index, row in enumerate(rows):
            rendered.append(
                [
                    Paragraph(
                        str(row[0]),
                        body_bold if row_index else table_header,
                    ),
                    Paragraph(
                        str(row[1]),
                        body_bold if row_index else table_header,
                    ),
                    Paragraph(
                        str(row[2]),
                        body if row_index else table_header,
                    ),
                ]
            )
        table = Table(
            rendered,
            colWidths=[40 * mm, 36 * mm, 95 * mm],
            repeatRows=1,
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), header_color),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.35, GRID),
                    ("ALIGN", (1, 1), (1, -1), "RIGHT"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 4.5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        return table

    story += [
        Paragraph("대표자 부담", section),
        Spacer(1, 1.5 * mm),
        result_table(representative_rows, RED),
        Spacer(1, 3 * mm),
        Paragraph("법인 부담과 재무 노출", section),
        Spacer(1, 1.5 * mm),
        result_table(company_rows, GREEN),
        Spacer(1, 3 * mm),
    ]

    insurance_rows = [["보험", "대표자", "법인", "적용 기준"]]
    for row in result.get("insurance_rows", []) or []:
        insurance_rows.append(
            [
                str(row.get("구분", "")),
                (
                    "미확인"
                    if row.get("미확인")
                    else _money(row.get("대표자 부담"))
                ),
                (
                    "미확인"
                    if row.get("미확인")
                    else _money(row.get("법인 부담"))
                ),
                (
                    str(row.get("기준", ""))
                    if row.get("적용") or row.get("미확인")
                    else "미적용 - 피보험자격 선택 안 함"
                ),
            ]
        )
    rendered_insurance = [
        [
            Paragraph(str(value), table_header if index == 0 else body)
            for value in row
        ]
        for index, row in enumerate(insurance_rows)
    ]
    insurance_table = Table(
        rendered_insurance,
        colWidths=[29 * mm, 32 * mm, 32 * mm, 78 * mm],
        repeatRows=1,
    )
    insurance_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.35, GRID),
                ("ALIGN", (1, 1), (2, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story += [
        Paragraph("4대보험 상세", section),
        Spacer(1, 1.5 * mm),
        insurance_table,
        Spacer(1, 2.5 * mm),
    ]

    notes = [
        "가지급금 원금 자체를 대표자 소득으로 단정하지 않으며, 인정이자 미회수/상여처분 시나리오를 계산한 사전진단입니다.",
        "인정이자율은 원칙적으로 가중평균차입이자율이며 4.6%는 법정 당좌대출이자율을 선택/적용할 수 있는 경우의 가정입니다.",
        "대표자 세액은 상여처분액 전액이 과세표준에 더해지는 보수적 추정입니다. 근로소득공제/세액공제와 실제 보수 신고를 반영하면 달라집니다.",
        "과세표준이 미확인된 경우 세금은 최저~최고 세율 범위이며, 보수정보 미확인 보험료는 합계와 분리해 표시합니다.",
        "대표이사는 통상 고용/산재보험 근로자성이 인정되지 않으므로 실제 피보험자격을 확인해야 합니다.",
        "최종 신고 전 세무대리인과 공단에 계정별원장, 금전소비대차계약, 이자수취, 차입금 적수 및 보수총액을 확인하세요.",
    ]
    story.append(
        Table(
            [[
                Paragraph("중요 안내", body_bold),
                Paragraph("<br/>".join(f"• {note}" for note in notes), small),
            ]],
            colWidths=[25 * mm, 146 * mm],
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F7F9FC")),
                    ("BOX", (0, 0), (-1, -1), 0.45, GRID),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            ),
        )
    )

    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setStrokeColor(GRID)
        canvas.line(13 * mm, 8 * mm, A4[0] - 13 * mm, 8 * mm)
        canvas.setFont(normal, 6.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(
            13 * mm,
            4.5 * mm,
            "OASIS | 법령/보험요율 기준일 2026-07-29 | 세무/노무 전문가 최종검토 필요",
        )
        canvas.drawRightString(A4[0] - 13 * mm, 4.5 * mm, "1 / 1")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer)
    output.seek(0)
    return output.getvalue()
