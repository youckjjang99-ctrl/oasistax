from __future__ import annotations

import struct
import unittest

import utils


class WebAssetPerformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        utils.get_logo_path.cache_clear()
        utils.image_to_base64.cache_clear()
        utils.logo_html.cache_clear()

    def test_sidebar_prefers_small_web_logo(self):
        logo_path = utils.get_logo_path()

        self.assertIsNotNone(logo_path)
        self.assertEqual(logo_path.name, "logo_web.png")
        self.assertLess(logo_path.stat().st_size, 100_000)
        with logo_path.open("rb") as logo_file:
            self.assertEqual(logo_file.read(8), b"\x89PNG\r\n\x1a\n")
            chunk_length = struct.unpack(">I", logo_file.read(4))[0]
            self.assertEqual(logo_file.read(4), b"IHDR")
            width, height = struct.unpack(">II", logo_file.read(8))
        self.assertEqual(chunk_length, 13)
        self.assertLessEqual(width, 1200)
        self.assertLessEqual(height, 900)

    def test_logo_encoding_is_reused_between_reruns(self):
        logo_path = utils.get_logo_path()
        first = utils.logo_html(360)
        second = utils.logo_html(360)

        self.assertEqual(first, second)
        self.assertLess(len(first), 100_000)
        self.assertGreaterEqual(utils.logo_html.cache_info().hits, 1)
        self.assertEqual(utils.image_to_base64.cache_info().misses, 1)


if __name__ == "__main__":
    unittest.main()
