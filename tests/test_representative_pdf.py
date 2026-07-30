from __future__ import annotations

import unittest
from io import BytesIO

from pypdf import PdfReader

from pdf_report import build_representative_pdf


class RepresentativePdfTests(unittest.TestCase):
    def _analysis(self) -> dict:
        return {
            "company_name": "오아시스 & 파트너 <테스트>",
            "business_no": "123-45-67890",
            "industry": "산업용 자동화 장비 제조 및 소프트웨어 개발",
            "completeness": "88%",
            "completeness_status": "핵심자료 확보",
            "sales": 3_850_000_000,
            "operating_profit": 286_000_000,
            "net_income": 214_000_000,
            "assets": 4_950_000_000,
            "liabilities": 2_180_000_000,
            "equity": 2_770_000_000,
            "operating_margin": 7.4,
            "net_margin": 5.6,
            "debt_ratio": 78.7,
            "strengths": "매출과 영업이익이 함께 증가했습니다.",
            "cautions": [
                "매출채권 회수조건과 운전자금 소요를 확인해야 합니다.",
                "연구개발비 증빙을 추가 확보해야 합니다.",
            ],
            "strategy": [
                "시설투자 견적서를 확보해 정책자금 가능성을 검토합니다.",
                "고용증가 인원을 확인해 고용지원금 대상 여부를 판정합니다.",
            ],
            "preferences": {
                "저장정책자금": [
                    {
                        "score": 92,
                        "category": "시설자금",
                        "title": "혁신성장 시설투자 지원자금",
                        "agency": "중소벤처기업진흥공단",
                        "end_date": "예산 소진 시",
                    }
                ]
            },
            "tax_diagnosis": {
                "items": [
                    {
                        "name": "연구인력개발비 세액공제",
                        "status": "추가 확인",
                        "rate_range": "비용의 25~50%",
                        "confidence": 72,
                    }
                ]
            },
        }

    def test_report_uses_readable_structure_and_accepts_special_text(self):
        pdf_bytes = build_representative_pdf(
            self._analysis(),
            consultant_name="오아시스 컨설턴트",
        )

        self.assertGreater(len(pdf_bytes), 5_000)
        reader = PdfReader(BytesIO(pdf_bytes))
        self.assertGreaterEqual(len(reader.pages), 3)
        extracted = "\n".join(
            page.extract_text() or "" for page in reader.pages
        )
        self.assertIn("핵심 요약", extracted)
        self.assertIn("재무 현황", extracted)
        self.assertIn("오아시스 & 파트너 <테스트>", extracted)
        self.assertIn("38.5억원", extracted)

    def test_minimal_report_is_still_generated(self):
        pdf_bytes = build_representative_pdf(
            {"company_name": "샘플기업"},
            consultant_name="",
        )
        reader = PdfReader(BytesIO(pdf_bytes))

        self.assertGreater(len(pdf_bytes), 3_000)
        self.assertGreaterEqual(len(reader.pages), 2)


if __name__ == "__main__":
    unittest.main()
