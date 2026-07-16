"""
Tests for lyrics_fetch.py pure logic (stdlib unittest). No network, no disk.

    python -m unittest test_lyrics -v
"""
import unittest
from unittest import mock

import lyrics_fetch as lf


class IsSyncedTest(unittest.TestCase):
    def test_detects_lrc_timestamps(self):
        self.assertTrue(lf.is_synced("[00:12.34]hello\n[00:15.00]world"))
        self.assertFalse(lf.is_synced("just plain lines\nno timing"))
        self.assertFalse(lf.is_synced(""))


class PickLyricsTest(unittest.TestCase):
    def test_prefers_synced_over_plain(self):
        rec = {"syncedLyrics": "[00:01.00]a", "plainLyrics": "a"}
        self.assertEqual(lf.pick_lyrics(rec), "[00:01.00]a")

    def test_falls_back_to_plain(self):
        self.assertEqual(lf.pick_lyrics({"syncedLyrics": "", "plainLyrics": "just words"}), "just words")

    def test_instrumental_and_empty_yield_nothing(self):
        self.assertEqual(lf.pick_lyrics({"instrumental": True, "plainLyrics": "x"}), "")
        self.assertEqual(lf.pick_lyrics({}), "")
        self.assertEqual(lf.pick_lyrics(None), "")


class HtmlToTextTest(unittest.TestCase):
    def test_br_becomes_newline_tags_stripped_entities_unescaped(self):
        out = lf._html_to_text("line one<br>line &amp; two<br/><b>three</b>")
        self.assertEqual(out, "line one\nline & two\nthree")


class FetchLyricsTest(unittest.TestCase):
    """fetch_lyrics logic + fallback order, with the network stubbed at _get_json."""

    @mock.patch("lyrics_fetch._get_json")
    def test_lrclib_get_hit_returns_synced(self, gj):
        gj.return_value = {"syncedLyrics": "[00:01.00]a", "plainLyrics": "a"}
        self.assertEqual(lf.fetch_lyrics("Radiohead", "Creep", 240), "[00:01.00]a")

    @mock.patch("lyrics_fetch._get_json")
    def test_falls_back_to_search_when_get_empty(self, gj):
        # /api/get -> instrumental (no usable lyrics); /api/search -> a plain hit
        gj.side_effect = [{"instrumental": True}, [{"plainLyrics": "the words"}]]
        self.assertEqual(lf.fetch_lyrics("A", "B"), "the words")

    def test_no_artist_or_title_returns_empty_without_network(self):
        with mock.patch("lyrics_fetch._get_json", side_effect=AssertionError("no net")):
            self.assertEqual(lf.fetch_lyrics("", ""), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
