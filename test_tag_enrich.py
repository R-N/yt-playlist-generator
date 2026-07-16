"""
Tests for tag_enrich.py pure logic (stdlib unittest). No network, no disk, no tag writes.

    python -m unittest test_tag_enrich -v
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import mutagen

import tag_enrich as te


def _stub_urlopen(payload):
    m = mock.MagicMock()
    m.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode()
    return m


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


class MbSearchTest(unittest.TestCase):
    def test_returns_full_tag_candidates_from_stub(self):
        payload = {"recordings": [_RECORDING, {"id": "r2", "title": "Other"}]}
        with mock.patch("urllib.request.urlopen", _stub_urlopen(payload)):
            cands = te.mb_search("Radiohead", "Creep")
        self.assertEqual(len(cands), 2)
        self.assertEqual(cands[0]["tags"]["album"], "Pablo Honey")

    def test_blank_query_no_request(self):
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("no net")):
            self.assertEqual(te.mb_search("", ""), [])


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg needed to synthesize a real audio file")
class EmbedRoundtripTest(unittest.TestCase):
    """The tag/lyrics write path is container-specific; exercise the real mutagen writes
    on genuine files for the two most different code paths: Vorbis (opus) and ID3 (mp3)."""

    def _make(self, path, codec):
        subprocess.run(
            ["ffmpeg", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "1",
             "-c:a", codec, path, "-y"],
            capture_output=True, check=True,
        )

    def _roundtrip(self, name, codec):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, name)
            self._make(p, codec)
            self.assertTrue(te.embed_tags(p, {"title": "Creep", "artist": "Radiohead",
                                              "album": "Pablo Honey"}))
            self.assertTrue(te.embed_lyrics(p, "[00:01.00]line"))
            easy = mutagen.File(p, easy=True)
            self.assertEqual(easy.get("title"), ["Creep"])
            self.assertEqual(easy.get("artist"), ["Radiohead"])

    def test_opus_vorbis_path(self):
        self._roundtrip("t.opus", "libopus")

    def test_mp3_id3_path(self):
        self._roundtrip("t.mp3", "libmp3lame")


if __name__ == "__main__":
    unittest.main(verbosity=2)
