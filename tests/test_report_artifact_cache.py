from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import articles_editor
import temporary_advance_ui
from artifact_cache import content_digest, file_revision


ROOT = Path(__file__).resolve().parents[1]


class ReportArtifactCacheTests(unittest.TestCase):
    def tearDown(self) -> None:
        articles_editor._cached_revised_articles_pdf.clear()
        temporary_advance_ui._cached_temporary_advance_pdf.clear()

    def test_content_digest_is_stable_and_input_sensitive(self) -> None:
        first = content_digest({"company": "A", "rows": [1, 2]})
        same = content_digest({"company": "A", "rows": [1, 2]})
        changed = content_digest({"company": "A", "rows": [1, 3]})

        self.assertEqual(first, same)
        self.assertNotEqual(first, changed)
        self.assertEqual(len(first), 64)

    def test_file_revision_changes_without_reading_file_bytes(self) -> None:
        missing = ROOT / "tests" / "does-not-exist-logo.png"
        self.assertEqual(file_revision(missing)[1:], (0, 0))

        source = ROOT / "artifact_cache.py"
        revision = file_revision(source)
        self.assertEqual(revision[0], str(source))
        self.assertGreater(revision[1], 0)
        self.assertGreater(revision[2], 0)

    def test_articles_pdf_reuses_same_owner_and_content_only(self) -> None:
        articles_editor._cached_revised_articles_pdf.clear()
        digest = content_digest("A", "123", "article text", "v1")

        with patch.object(
            articles_editor,
            "build_revised_articles_pdf",
            return_value=b"pdf-v1",
        ) as builder:
            args = ("owner-a", digest, "A", "123", "article text", "v1")
            self.assertEqual(
                articles_editor._cached_revised_articles_pdf(*args),
                b"pdf-v1",
            )
            self.assertEqual(
                articles_editor._cached_revised_articles_pdf(*args),
                b"pdf-v1",
            )
            self.assertEqual(builder.call_count, 1)

            articles_editor._cached_revised_articles_pdf(
                "owner-b",
                digest,
                "A",
                "123",
                "article text",
                "v1",
            )
            self.assertEqual(builder.call_count, 2)

            articles_editor._cached_revised_articles_pdf(
                "owner-a",
                content_digest("A", "123", "changed", "v1"),
                "A",
                "123",
                "changed",
                "v1",
            )
            self.assertEqual(builder.call_count, 3)

    def test_temporary_advance_pdf_is_not_rebuilt_on_rerun(self) -> None:
        temporary_advance_ui._cached_temporary_advance_pdf.clear()
        result = {"balance": 100_000_000, "tax": 12_000_000}
        digest = content_digest("A", "123", result, "consultant")

        with patch.object(
            temporary_advance_ui,
            "build_temporary_advance_pdf",
            return_value=b"advance-pdf",
        ) as builder:
            kwargs = {
                "company_name": "A",
                "business_no": "123",
                "consultant_name": "consultant",
                "_result": result,
            }
            first = temporary_advance_ui._cached_temporary_advance_pdf(
                "owner-a",
                digest,
                **kwargs,
            )
            second = temporary_advance_ui._cached_temporary_advance_pdf(
                "owner-a",
                digest,
                **kwargs,
            )

            self.assertEqual(first, b"advance-pdf")
            self.assertEqual(second, b"advance-pdf")
            self.assertEqual(builder.call_count, 1)

    def test_consulting_downloads_use_bounded_content_cache(self) -> None:
        source = (ROOT / "consulting_report.py").read_text(encoding="utf-8")

        self.assertIn("def _cached_representative_pdf(", source)
        self.assertIn("def _cached_consulting_excel(", source)
        self.assertIn("max_entries=32", source)
        self.assertIn("ttl=REPORT_CACHE_TTL_SECONDS", source)
        self.assertIn("pdf_digest = content_digest(", source)
        self.assertIn("excel_digest = content_digest(analysis)", source)
        self.assertNotIn("excel_bytes = build_consulting_excel_report(analysis)", source)


if __name__ == "__main__":
    unittest.main()
