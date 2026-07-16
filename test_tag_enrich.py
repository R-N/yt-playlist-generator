"""
Tests for tag_enrich.py pure logic (stdlib unittest). No network, no disk, no tag writes.

    python -m unittest test_tag_enrich -v
"""
import unittest

import tag_enrich as te


_RECORDING = {
    "id": "rec-1",
    "title": "Creep",
    "score": 100,
    "artist-credit": [{"name": "Radiohead", "joinphrase": ""}],
    "releases": [{
        "title": "Pablo Honey",
        "date": "1993-02-22",
        "artist-credit": [{"name": "Radiohead", "joinphrase": ""}],
    }],
    "tags": [{"name": "alternative rock"}],
}


class ParseRecordingFullTest(unittest.TestCase):
    def test_pulls_album_date_genre(self):
        out = te.parse_recording_full(_RECORDING)
        self.assertEqual(out["id"], "rec-1")
        self.assertEqual(out["tags"]["title"], "Creep")
        self.assertEqual(out["tags"]["artist"], "Radiohead")
        self.assertEqual(out["tags"]["album"], "Pablo Honey")
        self.assertEqual(out["tags"]["date"], "1993-02-22")
        self.assertEqual(out["tags"]["genre"], "alternative rock")

    def test_empty_fields_dropped(self):
        out = te.parse_recording_full({"id": "x", "title": "T"})
        self.assertNotIn("album", out["tags"])   # no releases -> no album/date
        self.assertEqual(out["tags"]["title"], "T")


class ScoreAndPickTest(unittest.TestCase):
    def test_exact_match_scores_high(self):
        self.assertGreaterEqual(te.score_candidate("Radiohead", "Creep", "Radiohead", "Creep"), 0.95)

    def test_wrong_song_scores_low(self):
        self.assertLess(te.score_candidate("Metallica", "One", "Radiohead", "Creep"), te.CONFIDENCE_BAR)

    def test_pick_best_returns_top_and_confidence(self):
        cands = [
            {"tags": {"artist": "Wrong", "title": "Other"}},
            te.parse_recording_full(_RECORDING),
        ]
        best, conf = te.pick_best(cands, "Radiohead", "Creep")
        self.assertEqual(best["id"], "rec-1")
        self.assertGreaterEqual(conf, te.CONFIDENCE_BAR)

    def test_pick_best_empty(self):
        best, conf = te.pick_best([], "A", "B")
        self.assertIsNone(best)
        self.assertEqual(conf, 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
