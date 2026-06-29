"""Tests for the Discord YouTube-link extractor regex (discord_extractor.py)."""
import unittest

from discord_extractor import extract_youtube_ids


class TestExtractYoutubeIds(unittest.TestCase):
    def test_all_url_forms(self):
        text = (
            "watch https://www.youtube.com/watch?v=dQw4w9WgXcQ\n"
            "short https://youtu.be/oHg5SJYRHA0\n"
            "shorts https://youtube.com/shorts/abcdefghijk\n"
            "embed https://www.youtube.com/embed/12345678901\n"
            "live https://www.youtube.com/live/ABCDEFGHIJK\n"
        )
        self.assertEqual(
            extract_youtube_ids(text),
            ["dQw4w9WgXcQ", "oHg5SJYRHA0", "abcdefghijk",
             "12345678901", "ABCDEFGHIJK"],
        )

    def test_extra_query_params(self):
        text = "https://www.youtube.com/watch?list=PL123&v=dQw4w9WgXcQ&t=42s"
        self.assertEqual(extract_youtube_ids(text), ["dQw4w9WgXcQ"])

    def test_multiple_per_line_and_non_string(self):
        text = "a https://youtu.be/dQw4w9WgXcQ b https://youtu.be/oHg5SJYRHA0"
        self.assertEqual(extract_youtube_ids(text), ["dQw4w9WgXcQ", "oHg5SJYRHA0"])
        self.assertEqual(extract_youtube_ids(None), [])
        self.assertEqual(extract_youtube_ids(42), [])

    def test_ignores_non_youtube(self):
        self.assertEqual(extract_youtube_ids("https://example.com/watch?v=nope"), [])


if __name__ == "__main__":
    unittest.main()
