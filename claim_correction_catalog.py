from __future__ import annotations

from dataclasses import dataclass
from datetime import date


AUTOMATIC_COLLECTION_CODES = frozenset(
    {
        "hometax_business_registration_list",
        "hometax_business_registration_certificate",
        "hometax_tax_payment_certificate",
        "comwel_total_remuneration",
        "comwel_management_number_list",
        "comwel_workplace_rate",
    }
)


def automatic_collection_supported(document_code: str) -> bool:
    return str(document_code or "").strip() in AUTOMATIC_COLLECTION_CODES


@dataclass(frozen=True)
class ClaimDocumentSpec:
    code: str
    name: str
    source: str
    period: str
    endpoint_hint: str
    description: str


DOCUMENT_SPECS: tuple[ClaimDocumentSpec, ...] = (
    ClaimDocumentSpec(
        code="comwel_total_remuneration",
        name="보수총액신고내역",
        source="근로복지공단",
        period="7개년",
        endpoint_hint="SelectBoSuJeopSuList",
        description="연도별 보수총액 신고 내역",
    ),
    ClaimDocumentSpec(
        code="hometax_tax_payment_certificate",
        name="국세납세증명서",
        source="홈택스",
        period="현재",
        endpoint_hint="UTERDAAA04",
        description="국세 체납 여부 확인용 증명",
    ),
    ClaimDocumentSpec(
        code="hometax_business_registration_list",
        name="홈택스 사업자정보 조회",
        source="홈택스",
        period="현재",
        endpoint_hint="MyBizInfo",
        description="홈택스 응답에서 확인한 사업자정보의 마스킹 조회본",
    ),
    ClaimDocumentSpec(
        code="comwel_management_number_list",
        name="사업장관리번호명세서",
        source="근로복지공단",
        period="현재",
        endpoint_hint="MyBizInfo",
        description="고용·산재보험 사업장 관리번호 목록",
    ),
    ClaimDocumentSpec(
        code="hometax_business_registration_certificate",
        name="사업자등록증명원",
        source="홈택스",
        period="현재",
        endpoint_hint="UTEABGAA21",
        description="사업자등록 사실 증명",
    ),
    ClaimDocumentSpec(
        code="comwel_workplace_rate",
        name="사업장요율",
        source="근로복지공단",
        period="7개년",
        endpoint_hint="T100110021005",
        description="연도별 고용·산재보험 요율",
    ),
    ClaimDocumentSpec(
        code="hometax_income_tax_help",
        name="종합소득세 신고도움 서비스",
        source="홈택스",
        period="최대 7개년",
        endpoint_hint="UTERNAAT32 계열",
        description="홈택스가 제공하는 신고 참고자료",
    ),
    ClaimDocumentSpec(
        code="hometax_income_tax_return",
        name="종합소득세 신고서",
        source="홈택스",
        period="7개년",
        endpoint_hint="UTERNAZ110/JongHabSoDeugSe/SinGo",
        description="연도별 종합소득세 신고서와 결정세액",
    ),
    ClaimDocumentSpec(
        code="hometax_closure_certificate",
        name="폐업사실증명서",
        source="홈택스",
        period="현재",
        endpoint_hint="UTEABDAA03",
        description="폐업 이력이 있을 때 발급되는 증명",
    ),
    ClaimDocumentSpec(
        code="hometax_refund",
        name="환급금",
        source="홈택스",
        period="조회 가능 기간",
        endpoint_hint="계약 API 확정 필요",
        description="환급 가능액과 처리 상태",
    ),
    ClaimDocumentSpec(
        code="comwel_worker_status",
        name="근로자고용정보현황",
        source="근로복지공단",
        period="현재",
        endpoint_hint="SelectGeunRoJaGyIryeok 계열",
        description="사업장별 근로자 고용정보 현황",
    ),
)


def seven_years(reference_year: int | None = None) -> list[int]:
    year = int(reference_year or date.today().year)
    return list(range(year - 1, year - 8, -1))


def document_plan(reference_year: int | None = None) -> list[dict[str, object]]:
    years = seven_years(reference_year)
    rows: list[dict[str, object]] = []
    for spec in DOCUMENT_SPECS:
        if "7개년" in spec.period:
            for year in years:
                rows.append(
                    {
                        "document_code": spec.code,
                        "document_name": spec.name,
                        "source": spec.source,
                        "period_year": year,
                        "status": (
                            "인증 대기"
                            if automatic_collection_supported(spec.code)
                            else "연동 예정"
                        ),
                        "automatic_collection": automatic_collection_supported(
                            spec.code
                        ),
                    }
                )
        else:
            rows.append(
                {
                    "document_code": spec.code,
                    "document_name": spec.name,
                    "source": spec.source,
                    "period_year": None,
                    "status": (
                        "인증 대기"
                        if automatic_collection_supported(spec.code)
                        else "연동 예정"
                    ),
                    "automatic_collection": automatic_collection_supported(
                        spec.code
                    ),
                }
            )
    return rows
