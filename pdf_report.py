from __future__ import annotations

import re
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    SimpleDocTemplate,
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

NAVY = colors.HexColor("#0B2B5B")
BLUE = colors.HexColor("#1E5BD7")
GREEN = colors.HexColor("#16835F")
LIGHT = colors.HexColor("#F3F7FC")
MID = colors.HexColor("#D7E2F0")
TEXT = colors.HexColor("#172033")
MUTED = colors.HexColor("#667085")
FONT_NORMAL = "Helvetica"
FONT_BOLD = "Helvetica-Bold"


RED = colors.HexColor("#C43D3D")


def _font_paths() -> tuple[str, str] | None:
    """Return an installed Korean TrueType font pair when available.

    Railway images do not always include the old unfonts-core package.  Never
    return a path that does not exist; the caller can safely use ReportLab's
    built-in Korean CID font instead.
    """
    project_dir = Path(__file__).resolve().parent
    candidates = [
        (project_dir / "assets" / "NanumGothic.ttf", project_dir / "assets" / "NanumGothicBold.ttf"),
        (Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"), Path("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf")),
        (Path("/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf"), Path("/usr/share/fonts/truetype/nanum/NanumBarunGothicBold.ttf")),
        (Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"), Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc")),
        (Path("/usr/share/fonts/truetype/unfonts-core/UnDotum.ttf"), Path("/usr/share/fonts/truetype/unfonts-core/UnDotumBold.ttf")),
    ]
    for regular, bold in candidates:
        if regular.is_file() and bold.is_file():
            return str(regular), str(bold)
    return None


def _register_fonts() -> tuple[str, str]:
    registered = set(pdfmetrics.getRegisteredFontNames())
    if {FONT_NORMAL, "OasisKR-Bold"}.issubset(registered):
        return FONT_NORMAL, "OasisKR-Bold"

    paths = _font_paths()
    if paths:
        regular, bold = paths
        try:
            pdfmetrics.registerFont(TTFont(FONT_NORMAL, regular))
            pdfmetrics.registerFont(TTFont(FONT_BOLD, bold))
            pdfmetrics.registerFontFamily(
                FONT_NORMAL,
                normal=FONT_NORMAL,
                bold=FONT_BOLD,
                italic=FONT_NORMAL,
                boldItalic=FONT_BOLD,
            )
            return FONT_NORMAL, "OasisKR-Bold"
        except Exception:
            pass

    cid_name = "HYSMyeongJo-Medium"
    if cid_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(cid_name))
    return cid_name, cid_name


def _clean(value: Any) -> str:
    if value is None:
        return "-"
    text = str(value).strip()
    return "-" if not text or text.lower() in {"nan", "none", "nat"} else text


def _money(value: Any) -> str:
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return "-"
    return f"{int(round(number)):,}원"


def _pct(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "-"


def _safe_list(values: Any, fallback: str) -> list[str]:
    items = [str(x).strip() for x in (values or []) if str(x).strip()]
    return items or [fallback]


def _header_footer(canvas, doc) -> None:
    canvas.saveState()
    width, height = A4
    canvas.setStrokeColor(MID)
    canvas.line(18 * mm, 14 * mm, width - 18 * mm, 14 * mm)
    canvas.setFont(FONT_NORMAL, 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 9 * mm, "OASIS AI CONSULTING REPORT")
    canvas.drawRightString(width - 18 * mm, 9 * mm, f"{doc.page}")
    canvas.restoreState()


def _cover(canvas, doc, analysis: dict[str, Any], logo_path: str | None, consultant_name: str) -> None:
    canvas.saveState()
    width, height = A4
    canvas.setFillColor(NAVY)
    canvas.rect(0, 0, width, height, fill=1, stroke=0)
    canvas.setFillColor(BLUE)
    canvas.circle(width * 0.88, height * 0.88, 85 * mm, fill=1, stroke=0)
    canvas.setFillColor(GREEN)
    canvas.circle(width * 0.1, height * 0.05, 70 * mm, fill=1, stroke=0)

    if logo_path and Path(logo_path).exists():
        try:
            canvas.drawImage(logo_path, 18 * mm, height - 55 * mm, 52 * mm, 37 * mm, preserveAspectRatio=True, mask="auto")
        except Exception:
            pass

    canvas.setFillColor(colors.white)
    canvas.setFont(FONT_NORMAL, 12)
    canvas.drawString(22 * mm, height - 92 * mm, "AI CONSULTING REPORT")
    canvas.setFont(FONT_BOLD, 27)
    company = _clean(analysis.get("company_name"))
    canvas.drawString(22 * mm, height - 115 * mm, company[:26])
    canvas.setFont(FONT_NORMAL, 11)
    canvas.drawString(22 * mm, height - 134 * mm, datetime.now().strftime("%Y년 %m월 %d일"))

    canvas.setFillColor(colors.HexColor("#E9F0FB"))
    canvas.roundRect(22 * mm, 42 * mm, 165 * mm, 42 * mm, 4 * mm, fill=1, stroke=0)
    canvas.setFillColor(NAVY)
    canvas.setFont(FONT_BOLD, 11)
    canvas.drawString(28 * mm, 70 * mm, "오아시스 기업컨설팅")
    canvas.setFont(FONT_NORMAL, 9)
    canvas.drawString(28 * mm, 59 * mm, f"담당 컨설턴트  {consultant_name or '-'}")
    canvas.drawString(28 * mm, 49 * mm, "기업의 현재를 진단하고 실행 가능한 개선 방향을 제안합니다.")
    canvas.restoreState()


def build_representative_pdf(
    analysis: dict[str, Any],
    consultant_name: str = "",
    logo_path: str | None = None,
) -> bytes:
    """대표님 제출용 PDF. CRM 내부 메모/상담 질문/계약확률은 포함하지 않는다."""
    global FONT_NORMAL, FONT_BOLD
    FONT_NORMAL, FONT_BOLD = _register_fonts()
    output = BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=20 * mm,
        title=f"{_clean(analysis.get('company_name'))} AI 컨설팅 보고서",
        author="OASIS",
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle("title", parent=styles["Heading1"], fontName=FONT_BOLD, fontSize=20, leading=27, textColor=NAVY, spaceAfter=10)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontName=FONT_BOLD, fontSize=14, leading=20, textColor=NAVY, spaceBefore=8, spaceAfter=8)
    body = ParagraphStyle("body", parent=styles["BodyText"], fontName=FONT_NORMAL, fontSize=9.5, leading=15, textColor=TEXT)
    small = ParagraphStyle("small", parent=body, fontSize=8, leading=12, textColor=MUTED)
    center = ParagraphStyle("center", parent=body, alignment=TA_CENTER)
    score_style = ParagraphStyle("score", parent=center, fontName=FONT_BOLD, fontSize=18, leading=22, textColor=NAVY)

    story = [PageBreak()]
    story += [Paragraph("목차", title), Spacer(1, 5 * mm)]
    contents = [
        ["01", "기업 개요와 핵심지표"],
        ["02", "재무 현황 및 진단"],
        ["03", "기업 강점과 주요 점검사항"],
        ["04", "정책자금·고용지원·세무 검토 방향"],
        ["05", "AI 절세기회 분석"],
        ["06", "오아시스 실행 로드맵"],
    ]
    toc = Table(contents, colWidths=[18 * mm, 145 * mm], rowHeights=15 * mm)
    toc.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), FONT_NORMAL),
        ("FONTNAME", (0, 0), (0, -1), FONT_BOLD),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("TEXTCOLOR", (0, 0), (0, -1), BLUE),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, MID),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story += [toc, PageBreak()]

    story += [Paragraph("01. 기업 개요와 핵심지표", title)]
    overview = [
        ["기업명", _clean(analysis.get("company_name")), "사업자등록번호", _clean(analysis.get("business_no"))],
        ["업종", _clean(analysis.get("industry")), "설립일", _clean(analysis.get("establishment"))],
        ["사업장", _clean(analysis.get("address")), "종업원수", _clean(analysis.get("employees"))],
    ]
    table = Table(overview, colWidths=[28 * mm, 57 * mm, 30 * mm, 55 * mm])
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), FONT_NORMAL),
        ("FONTNAME", (0, 0), (0, -1), FONT_BOLD),
        ("FONTNAME", (2, 0), (2, -1), FONT_BOLD),
        ("BACKGROUND", (0, 0), (0, -1), LIGHT),
        ("BACKGROUND", (2, 0), (2, -1), LIGHT),
        ("GRID", (0, 0), (-1, -1), 0.4, MID),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story += [table, Spacer(1, 8 * mm)]

    kpis = [
        ("매출액", _money(analysis.get("sales"))),
        ("영업이익", _money(analysis.get("operating_profit"))),
        ("당기순이익", _money(analysis.get("net_income"))),
        ("종업원수", (_clean(analysis.get("employees")) or "-") + ("명" if _clean(analysis.get("employees")) else "")),
    ]
    cells = []
    for label, value in kpis:
        cells.append(Table([[Paragraph(label, small)], [Paragraph(value, score_style)]], colWidths=[39 * mm], rowHeights=[9 * mm, 19 * mm], style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), LIGHT), ("BOX", (0, 0), (-1, -1), 0.7, MID), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")
        ])))
    story += [Table([cells], colWidths=[42 * mm] * 4, hAlign="LEFT"), Spacer(1, 8 * mm)]

    story += [Paragraph("02. 재무 현황 및 진단", title)]
    finance = [
        ["지표", "현재값", "진단 관점"],
        ["영업이익률", _pct(analysis.get("operating_margin")), "본업의 수익창출력을 확인하는 지표"],
        ["순이익률", _pct(analysis.get("net_margin")), "영업외손익과 세후 최종 수익성"],
        ["부채비율", _pct(analysis.get("debt_ratio")), "자본 대비 부채 부담과 재무안정성"],
        ["자산총계", _money(analysis.get("assets")), "기업의 보유자산 규모"],
        ["부채총계", _money(analysis.get("liabilities")), "상환부담과 금융구조 검토 대상"],
        ["자본총계", _money(analysis.get("equity")), "재무완충력과 기업가치의 기초"],
    ]
    ft = Table(finance, colWidths=[35 * mm, 43 * mm, 92 * mm], repeatRows=1)
    ft.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), FONT_NORMAL),
        ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, MID),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story += [ft, Spacer(1, 7 * mm)]

    strengths = _safe_list(analysis.get("strengths"), "현재 확보된 자료를 기준으로 추가 강점 분석이 필요합니다.")
    cautions = _safe_list(analysis.get("cautions"), "현재 자료상 즉시 확인되는 중대한 위험요인은 없습니다.")
    story += [Paragraph("03. 기업 강점과 주요 점검사항", title)]
    left = [Paragraph("주요 강점", ParagraphStyle("greenh", parent=h2, textColor=GREEN))] + [Paragraph(f"• {x}", body) for x in strengths[:7]]
    right = [Paragraph("주요 점검사항", ParagraphStyle("redh", parent=h2, textColor=RED))] + [Paragraph(f"• {x}", body) for x in cautions[:7]]
    two = Table([[left, right]], colWidths=[84 * mm, 84 * mm])
    two.setStyle(TableStyle([
        ("BOX", (0, 0), (0, 0), 0.7, colors.HexColor("#B9DDCF")),
        ("BOX", (1, 0), (1, 0), 0.7, colors.HexColor("#EAC5C5")),
        ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#F2FBF7")),
        ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#FFF7F7")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story += [two, PageBreak()]

    story += [Paragraph("04. 정책자금·고용지원·세무 검토 방향", title)]
    strategies = _safe_list(analysis.get("strategy"), "세부 사업계획 확인 후 맞춤형 지원제도를 검토합니다.")
    for i, text in enumerate(strategies[:10], start=1):
        block = Table([[Paragraph(f"{i:02d}", score_style), Paragraph(text, body)]], colWidths=[18 * mm, 150 * mm])
        block.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), LIGHT), ("BOX", (0, 0), (-1, -1), 0.5, MID), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)
        ]))
        story += [block, Spacer(1, 3 * mm)]

    tax_result = analysis.get("tax_diagnosis") or {}
    tax_items = tax_result.get("items") or []
    if tax_items:
        story += [PageBreak(), Paragraph("05. AI 절세기회 분석", title)]
        story += [Paragraph(
            f"적용 기준일: {_clean(tax_result.get('tax_rule_basis_date'))} · "
            "현재 확보된 자료를 기준으로 한 사전검토용 예상 범위입니다.", small
        ), Spacer(1, 4 * mm)]
        tax_rows = [["검토 항목", "상태", "예상 공제·감면", "신뢰도"]]
        for item in tax_items[:6]:
            tax_rows.append([
                _clean(item.get("name")), _clean(item.get("status")),
                _clean(item.get("rate_range")), f"{item.get('confidence', 0)}%",
            ])
        tt = Table(tax_rows, colWidths=[60 * mm, 28 * mm, 52 * mm, 28 * mm], repeatRows=1)
        tt.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), FONT_NORMAL),
            ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.4, MID),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        story += [tt, Spacer(1, 5 * mm)]
        for item in tax_items[:3]:
            reasons = ", ".join((item.get("reasons") or [])[:3]) or "추가 자료 확인 필요"
            story += [Paragraph(
                f"<b>{_clean(item.get('name'))}</b> · {_clean(item.get('rate_label'))} "
                f"{_clean(item.get('rate_range'))}<br/>{reasons}", body
            ), Spacer(1, 2 * mm)]
        story += [Paragraph(
            "※ 예상 공제율과 공제액은 확정 세액이 아니며, 사업연도·기업규모·업종·소재지·증빙·중복공제 제한을 반영한 세무사 최종 검토가 필요합니다.", small
        )]

    story += [Spacer(1, 4 * mm), Paragraph("06. 오아시스 실행 로드맵", title)]
    roadmap = [
        ["단계", "실행 내용", "목표"],
        ["1단계", "필요자료와 사업계획 확인", "지원 가능성 및 우선순위 확정"],
        ["2단계", "정책자금·보증·고용지원 세부요건 검토", "신청 가능 제도 선별"],
        ["3단계", "재무·세무·정관·기업가치 개선안 설계", "기업 리스크 완화와 자금조달력 개선"],
        ["4단계", "신청·실행·사후관리", "실행 결과 점검 및 후속 지원"],
    ]
    rt = Table(roadmap, colWidths=[25 * mm, 83 * mm, 62 * mm], repeatRows=1)
    rt.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), FONT_NORMAL), ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD), ("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("GRID", (0, 0), (-1, -1), 0.4, MID), ("FONTSIZE", (0, 0), (-1, -1), 8.5), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)
    ]))
    story += [rt, Spacer(1, 10 * mm)]
    disclaimer = (
        "본 보고서는 현재 제공된 기업정보를 기반으로 작성한 사전 컨설팅 자료입니다. "
        "정책자금, 고용지원금, 세무 및 기업가치 관련 최종 판단은 최신 공고·증빙자료·관계 법령을 추가 확인한 후 확정됩니다."
    )
    story += [Table([[Paragraph("안내", ParagraphStyle("noticeh", parent=h2, textColor=colors.white)), Paragraph(disclaimer, small)]], colWidths=[24 * mm, 144 * mm], style=TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), NAVY), ("BACKGROUND", (1, 0), (1, 0), LIGHT), ("BOX", (0, 0), (-1, -1), 0.5, MID), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 9)
    ]))]

    def first_page(canvas, _doc):
        _cover(canvas, _doc, analysis, logo_path, consultant_name)

    doc.build(story, onFirstPage=first_page, onLaterPages=_header_footer)
    output.seek(0)
    return output.getvalue()


# v8.6.1 representative PDF redesign.
# This later definition intentionally replaces the legacy builder above.
def _pdf_number(value: Any) -> float:
    try:
        return float(str(value or 0).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _pdf_section_title(text: str, styles: dict[str, Any]) -> Table:
    title_style = ParagraphStyle(
        f"section_{abs(hash(text))}",
        parent=styles["BodyText"],
        fontName=FONT_BOLD,
        fontSize=14,
        leading=19,
        textColor=NAVY,
    )
    table = Table(
        [[Paragraph(text, title_style)]],
        colWidths=[174 * mm],
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F3F7FC")),
                ("LINEBEFORE", (0, 0), (0, 0), 4, BLUE),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


def _pdf_kpi_card(
    label: str,
    value: str,
    note: str,
    background,
    styles: dict[str, Any],
) -> Table:
    label_style = ParagraphStyle(
        f"kpi_label_{abs(hash(label))}",
        parent=styles["BodyText"],
        fontName=FONT_BOLD,
        fontSize=8,
        leading=11,
        textColor=MUTED,
    )
    value_style = ParagraphStyle(
        f"kpi_value_{abs(hash(label))}",
        parent=styles["BodyText"],
        fontName=FONT_BOLD,
        fontSize=15,
        leading=19,
        textColor=NAVY,
    )
    note_style = ParagraphStyle(
        f"kpi_note_{abs(hash(label))}",
        parent=styles["BodyText"],
        fontName=FONT_NORMAL,
        fontSize=7,
        leading=10,
        textColor=BLUE,
    )
    card = Table(
        [
            [Paragraph(label, label_style)],
            [Paragraph(value, value_style)],
            [Paragraph(note, note_style)],
        ],
        colWidths=[40 * mm],
        rowHeights=[8 * mm, 13 * mm, 8 * mm],
    )
    card.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), background),
                ("BOX", (0, 0), (-1, -1), 0.7, MID),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return card


def _pdf_bar_row(
    label: str,
    value: float,
    maximum: float,
    display: str,
    color,
    styles: dict[str, Any],
) -> list[Any]:
    ratio = 0 if maximum <= 0 else max(0, min(value / maximum, 1))
    filled = max(0, min(round(ratio * 12), 12))
    cells = ["" for _ in range(12)]
    label_style = ParagraphStyle(
        f"bar_label_{abs(hash(label + display))}",
        parent=styles["BodyText"],
        fontName=FONT_BOLD,
        fontSize=8,
        leading=10,
        textColor=TEXT,
    )
    row = [Paragraph(label, label_style), *cells, display]
    style_commands = [
        ("GRID", (1, 0), (12, 0), 0.2, colors.HexColor("#E4EAF3")),
        ("BACKGROUND", (1, 0), (12, 0), colors.HexColor("#F4F6F9")),
        ("ALIGN", (13, 0), (13, 0), "RIGHT"),
        ("FONTNAME", (13, 0), (13, 0), FONT_BOLD),
        ("FONTSIZE", (13, 0), (13, 0), 8),
        ("TEXTCOLOR", (13, 0), (13, 0), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    if filled:
        style_commands.append(
            ("BACKGROUND", (1, 0), (filled, 0), color)
        )
    return row, style_commands


def build_representative_pdf(
    analysis: dict[str, Any],
    consultant_name: str = "",
    logo_path: str | None = None,
) -> bytes:
    global FONT_NORMAL, FONT_BOLD
    FONT_NORMAL, FONT_BOLD = _register_fonts()

    output = BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=17 * mm,
        bottomMargin=19 * mm,
        title=f"{_clean(analysis.get('company_name'))} AI 기업컨설팅 보고서",
        author="OASIS TAX & ACCOUNTING",
    )
    styles = getSampleStyleSheet()

    cover_title = ParagraphStyle(
        "v861_cover_title",
        parent=styles["Heading1"],
        fontName=FONT_BOLD,
        fontSize=25,
        leading=32,
        textColor=colors.white,
        spaceAfter=8,
    )
    cover_sub = ParagraphStyle(
        "v861_cover_sub",
        parent=styles["BodyText"],
        fontName=FONT_NORMAL,
        fontSize=10,
        leading=16,
        textColor=colors.HexColor("#DCE8FA"),
    )
    body = ParagraphStyle(
        "v861_body",
        parent=styles["BodyText"],
        fontName=FONT_NORMAL,
        fontSize=9,
        leading=15,
        textColor=TEXT,
    )
    body_small = ParagraphStyle(
        "v861_body_small",
        parent=body,
        fontSize=7.8,
        leading=12,
        textColor=MUTED,
    )
    body_bold = ParagraphStyle(
        "v861_body_bold",
        parent=body,
        fontName=FONT_BOLD,
    )

    company = _clean(analysis.get("company_name"))
    business_no = _clean(analysis.get("business_no"))
    industry = _clean(analysis.get("industry"))
    report_date = datetime.now().strftime("%Y년 %m월 %d일")

    story = []

    cover_content = [
        [Paragraph("OASIS AI CORPORATE CONSULTING", cover_sub)],
        [Spacer(1, 9 * mm)],
        [Paragraph(company, cover_title)],
        [Paragraph("기업 현황 진단 및 실행전략 보고서", cover_sub)],
        [Spacer(1, 20 * mm)],
        [
            Table(
                [
                    ["사업자등록번호", business_no],
                    ["업종", industry],
                    ["담당 컨설턴트", consultant_name or "-"],
                    ["작성일", report_date],
                ],
                colWidths=[42 * mm, 108 * mm],
                style=TableStyle(
                    [
                        ("FONTNAME", (0, 0), (0, -1), FONT_BOLD),
                        ("FONTNAME", (1, 0), (1, -1), FONT_NORMAL),
                        ("FONTSIZE", (0, 0), (-1, -1), 9),
                        ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
                        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#6487B8")),
                        ("TOPPADDING", (0, 0), (-1, -1), 8),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                    ]
                ),
            )
        ],
        [Spacer(1, 33 * mm)],
        [
            Paragraph(
                "본 보고서는 현재 확보된 기업자료를 기반으로 상담 방향과 "
                "실행 우선순위를 제시하는 사전진단 자료입니다.",
                cover_sub,
            )
        ],
    ]
    cover = Table(
        cover_content,
        colWidths=[174 * mm],
        rowHeights=None,
    )
    cover.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), NAVY),
                ("BOX", (0, 0), (-1, -1), 0, NAVY),
                ("LEFTPADDING", (0, 0), (-1, -1), 18),
                ("RIGHTPADDING", (0, 0), (-1, -1), 18),
                ("TOPPADDING", (0, 0), (-1, -1), 18),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 18),
            ]
        )
    )
    story += [cover, PageBreak()]

    story += [_pdf_section_title("01. Executive Summary", styles), Spacer(1, 5 * mm)]

    completeness = int(analysis.get("completeness", 0) or 0)
    kpis = [
        _pdf_kpi_card(
            "자료 충족도",
            f"{completeness}%",
            analysis.get("completeness_status", "보완 필요"),
            colors.HexColor("#EAF2FF"),
            styles,
        ),
        _pdf_kpi_card(
            "매출액",
            _money(analysis.get("sales")),
            "최근 결산 기준",
            colors.HexColor("#EAF8F2"),
            styles,
        ),
        _pdf_kpi_card(
            "영업이익",
            _money(analysis.get("operating_profit")),
            "본업 수익성",
            colors.HexColor("#F3EDFF"),
            styles,
        ),
        _pdf_kpi_card(
            "당기순이익",
            _money(analysis.get("net_income")),
            "세후 최종손익",
            colors.HexColor("#FFF4E7"),
            styles,
        ),
    ]
    story += [
        Table(
            [kpis],
            colWidths=[43.5 * mm] * 4,
            style=TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 1.5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 1.5),
                ]
            ),
        ),
        Spacer(1, 7 * mm),
    ]

    summary_table = Table(
        [
            [
                Paragraph("기업 개요", body_bold),
                Paragraph(
                    f"{company}은(는) {industry or '업종 미확인'} 기업으로, "
                    f"현재 자료 충족도는 {completeness}%입니다.",
                    body,
                ),
            ],
            [
                Paragraph("핵심 진단", body_bold),
                Paragraph(
                    (analysis.get("strengths") or ["추가 자료 확인 필요"])[0],
                    body,
                ),
            ],
            [
                Paragraph("우선 과제", body_bold),
                Paragraph(
                    (analysis.get("cautions") or ["중대한 경고사항 없음"])[0],
                    body,
                ),
            ],
        ],
        colWidths=[30 * mm, 144 * mm],
    )
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F3F7FC")),
                ("GRID", (0, 0), (-1, -1), 0.4, MID),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story += [summary_table, Spacer(1, 8 * mm)]

    story += [_pdf_section_title("02. 재무 현황과 가독성 그래프", styles), Spacer(1, 5 * mm)]

    financial_items = [
        ("매출액", _pdf_number(analysis.get("sales")), _money(analysis.get("sales")), BLUE),
        ("영업이익", _pdf_number(analysis.get("operating_profit")), _money(analysis.get("operating_profit")), GREEN),
        ("당기순이익", _pdf_number(analysis.get("net_income")), _money(analysis.get("net_income")), colors.HexColor("#7048C8")),
        ("자산총계", _pdf_number(analysis.get("assets")), _money(analysis.get("assets")), colors.HexColor("#2B78C5")),
        ("부채총계", _pdf_number(analysis.get("liabilities")), _money(analysis.get("liabilities")), colors.HexColor("#D98B32")),
        ("자본총계", _pdf_number(analysis.get("equity")), _money(analysis.get("equity")), colors.HexColor("#16835F")),
    ]
    maximum = max([abs(item[1]) for item in financial_items] + [1])
    bar_rows = []
    bar_styles = []
    for row_index, item in enumerate(financial_items):
        row, commands = _pdf_bar_row(
            item[0],
            abs(item[1]),
            maximum,
            item[2],
            item[3],
            styles,
        )
        bar_rows.append(row)
        for command in commands:
            cmd = list(command)
            for pos in (1, 2):
                if isinstance(cmd[pos], tuple):
                    cmd[pos] = (cmd[pos][0], row_index)
            bar_styles.append(tuple(cmd))
    chart = Table(
        bar_rows,
        colWidths=[27 * mm] + [7 * mm] * 12 + [42 * mm],
        rowHeights=[8 * mm] * len(bar_rows),
    )
    chart.setStyle(TableStyle(bar_styles))
    story += [chart, Spacer(1, 6 * mm)]

    ratio_data = [
        ["지표", "현재값", "판단 관점"],
        [
            "영업이익률",
            _pct(analysis.get("operating_margin")),
            "매출 대비 본업 수익창출력",
        ],
        [
            "순이익률",
            _pct(analysis.get("net_margin")),
            "영업외손익과 세후 수익성",
        ],
        [
            "부채비율",
            _pct(analysis.get("debt_ratio")),
            "자본 대비 부채 부담",
        ],
    ]
    ratio_table = Table(
        ratio_data,
        colWidths=[42 * mm, 38 * mm, 94 * mm],
        repeatRows=1,
    )
    ratio_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
                ("FONTNAME", (0, 1), (-1, -1), FONT_NORMAL),
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.4, MID),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story += [ratio_table, PageBreak()]

    story += [_pdf_section_title("03. 강점 · 확인 필요 · 실행전략", styles), Spacer(1, 5 * mm)]

    def make_list_box(title_text, values, bg, border, title_color):
        title_style = ParagraphStyle(
            f"box_title_{abs(hash(title_text))}",
            parent=body,
            fontName=FONT_BOLD,
            fontSize=11,
            leading=15,
            textColor=title_color,
        )
        content = [Paragraph(title_text, title_style)]
        for value in (values or ["추가 확인이 필요합니다."])[:7]:
            content.append(Paragraph(f"• {value}", body))
            content.append(Spacer(1, 1.5 * mm))
        box = Table([[content]], colWidths=[82 * mm])
        box.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), bg),
                    ("BOX", (0, 0), (-1, -1), 0.8, border),
                    ("LEFTPADDING", (0, 0), (-1, -1), 11),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 11),
                    ("TOPPADDING", (0, 0), (-1, -1), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        return box

    strengths_box = make_list_box(
        "기업 강점",
        analysis.get("strengths"),
        colors.HexColor("#F2FBF7"),
        colors.HexColor("#B9DDCF"),
        GREEN,
    )
    cautions_box = make_list_box(
        "확인 필요",
        analysis.get("cautions"),
        colors.HexColor("#FFF7F7"),
        colors.HexColor("#EAC5C5"),
        RED,
    )
    story += [
        Table(
            [[strengths_box, cautions_box]],
            colWidths=[87 * mm, 87 * mm],
            style=TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ]
            ),
        ),
        Spacer(1, 7 * mm),
    ]

    strategy_rows = [["우선순위", "실행전략"]]
    for index, value in enumerate(
        (analysis.get("strategy") or [])[:10],
        start=1,
    ):
        strategy_rows.append([f"{index:02d}", Paragraph(value, body)])
    strategy_table = Table(
        strategy_rows,
        colWidths=[22 * mm, 152 * mm],
        repeatRows=1,
    )
    strategy_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
                ("FONTNAME", (0, 1), (0, -1), FONT_BOLD),
                ("BACKGROUND", (0, 0), (-1, 0), BLUE),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("TEXTCOLOR", (0, 1), (0, -1), BLUE),
                ("BACKGROUND", (0, 1), (0, -1), colors.HexColor("#EAF2FF")),
                ("GRID", (0, 0), (-1, -1), 0.4, MID),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story += [strategy_table, Spacer(1, 8 * mm)]

    saved_policies = [
        item
        for item in (
            (analysis.get("preferences") or {}).get("저장정책자금", []) or []
        )
        if isinstance(item, dict)
    ]
    if saved_policies:
        story += [
            _pdf_section_title("04. 저장된 정책자금 추천", styles),
            Spacer(1, 5 * mm),
        ]
        policy_rows = [["점수", "분류", "공고명", "기관", "신청종료"]]
        for item in saved_policies[:12]:
            policy_rows.append(
                [
                    str(item.get("score", "")),
                    _clean(item.get("category", "")),
                    Paragraph(_clean(item.get("title", "")), body_small),
                    Paragraph(_clean(item.get("agency", "")), body_small),
                    _clean(item.get("end_date", "")),
                ]
            )
        policy_table = Table(
            policy_rows,
            colWidths=[14 * mm, 28 * mm, 73 * mm, 35 * mm, 24 * mm],
            repeatRows=1,
        )
        policy_table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
                    ("FONTNAME", (0, 1), (-1, -1), FONT_NORMAL),
                    ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.35, MID),
                    ("FONTSIZE", (0, 0), (-1, -1), 7.4),
                    ("ALIGN", (0, 1), (0, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story += [policy_table, Spacer(1, 8 * mm)]

    tax_result = analysis.get("tax_diagnosis") or {}
    tax_items = tax_result.get("items") or []
    if tax_items:
        story += [
            _pdf_section_title("05. AI 절세기회 사전검토", styles),
            Spacer(1, 5 * mm),
        ]
        tax_rows = [["검토 항목", "상태", "예상 범위", "신뢰도"]]
        for item in tax_items[:8]:
            tax_rows.append(
                [
                    Paragraph(_clean(item.get("name")), body_small),
                    _clean(item.get("status")),
                    _clean(item.get("rate_range")),
                    f"{item.get('confidence', 0)}%",
                ]
            )
        tax_table = Table(
            tax_rows,
            colWidths=[68 * mm, 32 * mm, 48 * mm, 26 * mm],
            repeatRows=1,
        )
        tax_table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
                    ("FONTNAME", (0, 1), (-1, -1), FONT_NORMAL),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#16835F")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.35, MID),
                    ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story += [tax_table, Spacer(1, 7 * mm)]

    story += [
        Spacer(1, 4 * mm),
        Paragraph(
            "※ 본 보고서는 현재 저장자료에 기반한 사전진단이며, "
            "정책자금 신청 가능 여부와 세무효과는 최신 공고·신고서·증빙자료를 "
            "추가 확인한 후 확정해야 합니다.",
            body_small,
        ),
    ]

    def first_page(canvas, doc_obj):
        canvas.saveState()
        canvas.setFillColor(NAVY)
        canvas.rect(0, 0, A4[0], A4[1], fill=1, stroke=0)
        canvas.restoreState()

    def later_pages(canvas, doc_obj):
        _header_footer(canvas, doc_obj)

    doc.build(
        story,
        onFirstPage=first_page,
        onLaterPages=later_pages,
    )
    output.seek(0)
    return output.getvalue()
