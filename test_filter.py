"""
Tests for filter_local_quality.py pure logic (stdlib unittest).

    python -m unittest test_filter -v

Uses temp folders + an injected bitrate reader, so no real music is touched.
"""
import os
import tempfile
import unittest

import pandas as pd

import filter_local_quality as flq


class BuildIndexTest(unittest.TestCase):
    def test_index_skips_missing_and_first_folder_wins(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            open(os.path.join(a, "song.mp3"), "w").close()
            open(os.path.join(a, "note.txt"), "w").close()   # non-mp3 ignored
            open(os.path.join(b, "song.mp3"), "w").close()   # dup -> first wins
            open(os.path.join(b, "other.mp3"), "w").close()

            idx = flq.build_file_index([a, "E:/does/not/exist", b])
            self.assertEqual(set(idx), {"song.mp3", "other.mp3"})
            self.assertEqual(idx["song.mp3"], os.path.join(a, "song.mp3"))
            self.assertNotIn("note.txt", idx)


class BitrateRowsTest(unittest.TestCase):
    def test_maps_bitrate_and_counts_missing(self):
        index = {"A.mp3": "/x/A.mp3", "B.mp3": "/x/B.mp3"}
        fake = {"/x/A.mp3": 320, "/x/B.mp3": 128}.get
        bitrates, missing = flq.bitrate_for_rows(
            ["A.mp3", "B.mp3", "GONE.mp3"], index, read_bitrate=fake)
        self.assertEqual(bitrates, [320, 128, None])
        self.assertEqual(missing, 1)


class AnnotateTest(unittest.TestCase):
    def test_local_better_threshold(self):
        df = pd.DataFrame({"filename": ["A", "B", "C", "D"]})
        out = flq.annotate_local_quality(df, [320, 192, 128, None], threshold=192)
        self.assertEqual(list(out["local_better"]), [True, True, False, False])
        #              >=192 True ; ==192 True (boundary) ; below False ; missing False


class DownloadIdsTest(unittest.TestCase):
    def _df(self):
        return pd.DataFrame({
            "yt_id": ["a", "b", "c", "c", "d"],
            "local_better": [False, True, False, False, False],
            "check": [True, True, False, False, True],
        })

    def test_excludes_local_better_and_dedups(self):
        ids = set(flq.download_ids(self._df(), only_checked=False))
        self.assertEqual(ids, {"a", "c", "d"})   # b dropped (local_better), c deduped

    def test_only_checked_keeps_verified(self):
        ids = set(flq.download_ids(self._df(), only_checked=True))
        self.assertEqual(ids, {"a", "d"})        # c dropped (check False), b local_better


class BitrateReadErrorTest(unittest.TestCase):
    def test_unreadable_returns_none(self):
        self.assertIsNone(flq.local_bitrate_kbps("/no/such/file.mp3"))
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(b"not really audio")
            name = f.name
        try:
            self.assertIsNone(flq.local_bitrate_kbps(name))   # non-audio -> None
        finally:
            os.unlink(name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
