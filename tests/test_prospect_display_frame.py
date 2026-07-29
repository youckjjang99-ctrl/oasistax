import unittest

from prospect_db_center import _display_frame


class ProspectDisplayFrameTests(unittest.TestCase):
    def test_business_number_status_is_calculated_without_name_error(self):
        frame = _display_frame(
            [
                {
                    "사업장명": "테스트 사업장",
                    "사업자등록번호": "123-45-67890",
                }
            ]
        )

        self.assertEqual(frame.loc[0, "사업자번호상태"], "확인")


if __name__ == "__main__":
    unittest.main()
