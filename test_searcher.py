"""
Tests for searcher.py's score() heuristic (stdlib unittest).

    python -m unittest test_searcher -v

Pure scoring logic — no network, no disk. (Runnable only because searcher.py now
guards main() behind __name__ == "__main__"; importing it used to scan MP3_FOLDERS.)
"""
import unittest
from unittest import mock

import searcher


def _entry(uploader="", uploader_id="", title="", views=1000, ext="opus"):
    return {
        "uploader": uploader,
        "uploader_id": uploader_id,
        "title": title,
        "view_count": views,
        "formats": [{"vcodec": "none", "abr": 160, "ext": ext, "acodec": ext}],
    }


class ScoreTest(unittest.TestCase):
    def test_topic_channel_beats_random_fan_channel(self):
        topic = _entry("Radiohead - Topic", "UCabcdefghij", "Creep")
        fan = _entry("Radiohead Fans Forever", "", "Creep")
        self.assertGreater(
            searcher.score(topic, "Radiohead", "Creep"),
            searcher.score(fan, "Radiohead", "Creep"),
        )

    def test_unwanted_version_penalized_when_absent_from_query(self):
        clean = _entry("Radiohead - Topic", "UCabcdefghij", "Creep")
        live = _entry("Radiohead - Topic", "UCabcdefghij", "Creep (Live)")
        self.assertGreater(
            searcher.score(clean, "Radiohead", "Creep"),
            searcher.score(live, "Radiohead", "Creep"),
        )

    def test_unwanted_term_not_penalized_when_sought(self):
        # Same 'Live' title, but the query asks for it — penalty must NOT apply.
        live = _entry("Radiohead - Topic", "UCabcdefghij", "Creep (Live)")
        penalized = searcher.score(live, "Radiohead", "Creep")
        sought = searcher.score(live, "Radiohead", "Creep Live")
        self.assertGreater(sought, penalized)

    def test_nightcore_penalized_only_when_unsought(self):
        nc = _entry("Nightcore Releases", "UCabcdefghij", "Creep (Nightcore)")
        # sought in title query -> no penalty; unsought -> penalty
        self.assertGreater(
            searcher.score(nc, "Radiohead", "Creep Nightcore"),
            searcher.score(nc, "Radiohead", "Creep"),
        )

    def test_prefers_better_audio_format(self):
        opus = _entry("Radiohead - Topic", "UCabcdefghij", "Creep", ext="opus")
        mp3 = _entry("Radiohead - Topic", "UCabcdefghij", "Creep", ext="mp3")
        self.assertGreater(
            searcher.score(opus, "Radiohead", "Creep"),
            searcher.score(mp3, "Radiohead", "Creep"),
        )


class GetMetadataFallbackTest(unittest.TestCase):
    @mock.patch("searcher.mutagen.File", side_effect=Exception("unreadable tags"))
    def test_falls_back_to_parse_title_on_basename(self, _mf):
        # tag read fails -> parse the basename (not the full path), drop [ytid]
        artist, title, composer = searcher.get_metadata(
            "E:/My-Music/Radiohead - Creep [dQw4w9WgXcQ].mp3")
        self.assertEqual((artist, title, composer), ("Radiohead", "Creep", ""))


class ParseTitleTest(unittest.TestCase):
    def test_splits_on_dash_and_drops_bracket_id(self):
        self.assertEqual(
            searcher.parse_title("Radiohead - Creep [dQw4w9WgXcQ]"),
            ("Radiohead", "Creep"),
        )

    def test_strips_decoration_noise(self):
        artist, title = searcher.parse_title("YOASOBI - Idol (Official Music Video)")
        self.assertEqual(artist, "YOASOBI")
        self.assertEqual(title, "Idol")

    def test_japanese_bracket_title_form(self):
        self.assertEqual(searcher.parse_title("米津玄師「Lemon」"), ("米津玄師", "Lemon"))

    def test_no_separator_uses_channel_as_artist(self):
        self.assertEqual(searcher.parse_title("Creep", channel="Radiohead"), ("Radiohead", "Creep"))

    def test_no_separator_no_channel_keeps_title(self):
        self.assertEqual(searcher.parse_title("Some Song"), ("", "Some Song"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
