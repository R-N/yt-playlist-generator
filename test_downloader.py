import unittest
import os
import tempfile
from unittest.mock import mock_open, patch

import downloader
from yt_dlp.utils import DownloadError


class InputFileConfigTest(unittest.TestCase):
    def test_legacy_default(self):
        self.assertEqual(downloader.resolve_input_file(), "ids.txt")

    def test_valid_absolute_override(self):
        with tempfile.NamedTemporaryFile() as input_file:
            with patch.dict(os.environ, {"YT_INPUT_FILE": input_file.name}):
                self.assertEqual(
                    downloader.resolve_input_file(),
                    os.path.normcase(os.path.realpath(input_file.name)),
                )

    def test_invalid_override_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            for value in ("ids.txt", os.path.join(directory, "missing.txt"), directory):
                with self.subTest(value=value), patch.dict(os.environ, {"YT_INPUT_FILE": value}):
                    with self.assertRaisesRegex(ValueError, "YT_INPUT_FILE"):
                        downloader.resolve_input_file()


class AgeRetryTest(unittest.TestCase):
    def test_age_restriction_retries_through_download(self):
        error = DownloadError("Sign in to confirm your age")
        first = patch.object(downloader, "YoutubeDL")
        with first as ydl_class:
            ydl_class.return_value.__enter__.return_value.download.side_effect = [error, 0]
            with patch.object(downloader, "sign_in_only", False), patch.object(
                downloader, "SIGN_IN_FILE", "test-sign-in.txt"
            ), patch("builtins.open", mock_open()):
                self.assertTrue(downloader.download({}, "https://www.youtube.com/watch?v=abcdefghijk"))
        self.assertEqual(ydl_class.return_value.__enter__.return_value.download.call_count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
