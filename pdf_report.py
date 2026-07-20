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


def _register_fonts() -> None:
    if "OasisKR" in pdfmetrics.getRegisteredFontNames():
        return

    paths = _font_paths()
    if paths:
        regular, bold = paths
        try:
            pdfmetrics.registerFont(TTFont("OasisKR", regular))
            pdfmetrics.registerFont(TTFont("OasisKR-Bold", bold))
            pdfmetrics.registerFontFamily(
                "OasisKR",
                normal="OasisKR",
                bold="OasisKR-Bold",
                italic="OasisKR",
                boldItalic="OasisKR-Bold",
            )
            return
        except Exception:
            # TTC files and minimal Railway images can still reject TTFont.
            # Fall through to ReportLab's built-in Korean CID font.
            pass

    pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))
    # Alias the CID font under the names already used throughout this report.
    pdfmetrics._fonts["OasisKR"] = pdfmetrics.getFont("HYSMyeongJo-Medium")
    pdfmetrics._fonts["OasisKR-Bold"] = pdfmetrics.getFont("HYSMyeongJo-Medium")
    pdfmetrics.registerFontFamily(
        "OasisKR",
        normal="OasisKR",
        bold="OasisKR-Bold",
        italic="OasisKR",
        boldItalic="OasisKR-Bold",
    )


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
    canvas.setFont("OasisKR", 7.5)
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
    canvas.setFont("OasisKR", 12)
    canvas.drawString(22 * mm, height - 92 * mm, "AI CONSULTING REPORT")
    canvas.setFont("OasisKR-Bold", 27)
    company = _clean(analysis.get("company_name"))
    canvas.drawString(22 * mm, height - 115 * mm, company[:26])
    canvas.setFont("OasisKR", 11)
    canvas.drawString(22 * mm, height - 134 * mm, datetime.now().strftime("%Y년 %m월 %d일"))

    canvas.setFillColor(colors.HexColor("#E9F0FB"))
    canvas.roundRect(22 * mm, 42 * mm, 165 * mm, 42 * mm, 4 * mm, fill=1, stroke=0)
    canvas.setFillColor(NAVY)
    canvas.setFont("OasisKR-Bold", 11)
    canvas.drawString(28 * mm, 70 * mm, "오아시스 기업컨설팅")
    canvas.setFont("OasisKR", 9)
    canvas.drawString(28 * mm, 59 * mm, f"담당 컨설턴트  {consultant_name or '-'}")
    canvas.drawString(28 * mm, 49 * mm, "기업의 현재를 진단하고 실행 가능한 개선 방향을 제안합니다.")
    canvas.restoreState()


def build_representative_pdf(
    analysis: dict[str, Any],
    consultant_name: str = "",
    logo_path: str | None = None,
) -> bytes:
    """대표님 제출용 PDF. CRM 내부 메모/상담 질문/계약확률은 포함하지 않는다."""
    _register_fonts()
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
    title = ParagraphStyle("title", parent=styles["Heading1"], fontName="OasisKR-Bold", fontSize=20, leading=27, textColor=NAVY, spaceAfter=10)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontName="OasisKR-Bold", fontSize=14, leading=20, textColor=NAVY, spaceBefore=8, spaceAfter=8)
    body = ParagraphStyle("body", parent=styles["BodyText"], fontName="OasisKR", fontSize=9.5, leading=15, textColor=TEXT)
    small = ParagraphStyle("small", parent=body, fontSize=8, leading=12, textColor=MUTED)
    center = ParagraphStyle("center", parent=body, alignment=TA_CENTER)
    score_style = ParagraphStyle("score", parent=center, fontName="OasisKR-Bold", fontSize=18, leading=22, textColor=NAVY)

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
        ("FONTNAME", (0, 0), (-1, -1), "OasisKR"),
        ("FONTNAME", (0, 0), (0, -1), "OasisKR-Bold"),
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
        ("FONTNAME", (0, 0), (-1, -1), "OasisKR"),
        ("FONTNAME", (0, 0), (0, -1), "OasisKR-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "OasisKR-Bold"),
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
        ("FONTNAME", (0, 0), (-1, -1), "OasisKR"),
        ("FONTNAME", (0, 0), (-1, 0), "OasisKR-Bold"),
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
            ("FONTNAME", (0, 0), (-1, -1), "OasisKR"),
            ("FONTNAME", (0, 0), (-1, 0), "OasisKR-Bold"),
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
        ("FONTNAME", (0, 0), (-1, -1), "OasisKR"), ("FONTNAME", (0, 0), (-1, 0), "OasisKR-Bold"), ("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("GRID", (0, 0), (-1, -1), 0.4, MID), ("FONTSIZE", (0, 0), (-1, -1), 8.5), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)
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
