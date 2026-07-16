"""
Tests for mb_enrich.py pure logic (stdlib unittest). No network, no disk.

    python -m unittest test_mb_enrich -v
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import pandas as pd

import mb_enrich as mb


def _stub_urlopen(payload):
    """A urlopen replacement whose context-manager .read() returns json(payload)."""
    m = mock.MagicMock()
    m.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode()
    return m


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


class SearchRecordingTest(unittest.TestCase):
    def test_parses_top_recording_from_stubbed_response(self):
        payload = {"recordings": [
            {"id": "r1", "title": "Creep", "score": 99,
             "artist-credit": [{"name": "Radiohead", "joinphrase": ""}]},
        ]}
        with mock.patch("urllib.request.urlopen", _stub_urlopen(payload)):
            out = mb.search_recording("Radiohead", "Creep")
        self.assertEqual(out["mb_recording_id"], "r1")
        self.assertEqual(out["mb_artist"], "Radiohead")
        self.assertEqual(out["mb_text_score"], 99)

    def test_no_recordings_returns_empty(self):
        with mock.patch("urllib.request.urlopen", _stub_urlopen({"recordings": []})):
            self.assertEqual(mb.search_recording("Nobody", "Nothing"), {})

    def test_blank_query_makes_no_request(self):
        # both terms empty -> must return {} without touching the network
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("should not call")):
            self.assertEqual(mb.search_recording("", ""), {})


class MainInvariantTest(unittest.TestCase):
    """main() must fill blank mb_recording_id rows, NEVER overwrite an existing one
    (fingerprint results from acoustid_enrich win), and resume via mbt_done."""

    def setUp(self):
        self._orig = {k: getattr(mb, k) for k in
                      ("MATCHES_CSV", "MATCHES_XLSX", "RATE_LIMIT_S", "SAVE_EVERY")}
        self.d = tempfile.mkdtemp()
        mb.MATCHES_CSV = os.path.join(self.d, "matches.csv")
        mb.MATCHES_XLSX = os.path.join(self.d, "matches.xlsx")
        mb.RATE_LIMIT_S = 0
        mb.SAVE_EVERY = 1000
        pd.DataFrame([
            {"filename": "a.mp3", "artist": "Radiohead", "title": "Creep",
             "mb_recording_id": None, "yt_channel": "Radiohead - Topic", "yt_title": "Creep"},
            {"filename": "b.mp3", "artist": "X", "title": "Y",
             "mb_recording_id": "OLD", "yt_channel": "", "yt_title": ""},
        ]).to_csv(mb.MATCHES_CSV, index=False)

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(mb, k, v)
        shutil.rmtree(self.d, ignore_errors=True)

    def test_fills_blank_preserves_existing_and_resumes(self):
        hit = {"mb_recording_id": "NEW", "mb_title": "Creep",
               "mb_artist": "Radiohead", "mb_text_score": 95}
        with mock.patch.object(mb, "search_recording", return_value=hit) as sr:
            mb.main()
            self.assertEqual(sr.call_count, 1)          # only the blank row searched

        out = pd.read_csv(mb.MATCHES_CSV)
        a = out[out.filename == "a.mp3"].iloc[0]
        b = out[out.filename == "b.mp3"].iloc[0]
        self.assertEqual(a.mb_recording_id, "NEW")
        self.assertEqual(a.mb_source, "text")
        self.assertEqual(b.mb_recording_id, "OLD")      # never overwritten

        # rerun: every row is mbt_done -> no search happens at all
        with mock.patch.object(mb, "search_recording",
                               side_effect=AssertionError("resume must skip")):
            mb.main()


if __name__ == "__main__":
    unittest.main(verbosity=2)
