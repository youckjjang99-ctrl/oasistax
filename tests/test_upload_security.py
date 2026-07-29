import unittest
from unittest.mock import patch

from utils import make_upload_filename


class UploadSecurityTests(unittest.TestCase):
    @patch("utils.datetime")
    def test_upload_filename_removes_path_and_unsafe_characters(
        self,
        mocked_datetime,
    ):
        mocked_datetime.now.return_value.strftime.return_value = (
            "20260729_120000"
        )

        result = make_upload_filename(
            "../../outside/<script>alert(1)</script>.PDF"
        )

        self.assertNotIn("..", result)
        self.assertNotIn("/", result)
        self.assertNotIn("\\", result)
        self.assertNotIn("<", result)
        self.assertTrue(result.endswith("_script.pdf"))


if __name__ == "__main__":
    unittest.main()
