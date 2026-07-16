"""
Tests for mb_enrich.py pure logic (stdlib unittest). No network, no disk.

    python -m unittest test_mb_enrich -v
"""
import unittest

import mb_enrich as mb


# One recording object shaped like a real MusicBrainz /ws/2/recording response.
_RECORDING = {
    "id": "abc-123",
    "title": "Creep",
    "score": 100,
    "artist-credit": [
        {"name": "Radiohead", "joinphrase": " feat. "},
        {"artist": {"name": "Someone"}, "joinphrase": ""},
    ],
}


class ArtistNameTest(unittest.TestCase):
    def test_joins_credit_with_joinphrases(self):
        self.assertEqual(mb._artist_name(_RECORDING["artist-credit"]), "Radiohead feat. Someone")

    def test_empty_credit(self):
        self.assertEqual(mb._artist_name(None), "")
        self.assertEqual(mb._artist_name([]), "")


class ParseRecordingTest(unittest.TestCase):
    def test_flattens_to_mb_columns(self):
        out = mb.parse_recording(_RECORDING)
        self.assertEqual(out["mb_recording_id"], "abc-123")
        self.assertEqual(out["mb_title"], "Creep")
        self.assertEqual(out["mb_artist"], "Radiohead feat. Someone")
        self.assertEqual(out["mb_text_score"], 100)

    def test_missing_fields_default(self):
        out = mb.parse_recording({})
        self.assertEqual(out["mb_recording_id"], "")
        self.assertEqual(out["mb_text_score"], 0)


class BlankTest(unittest.TestCase):
    def test_blank_values(self):
        self.assertTrue(mb._blank(None))
        self.assertTrue(mb._blank(""))
        self.assertTrue(mb._blank(float("nan")))
        self.assertFalse(mb._blank("Creep"))
        self.assertFalse(mb._blank(0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
