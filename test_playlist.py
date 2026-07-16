"""
Tests for playlist_generator.py pure logic (stdlib unittest).

    python -m unittest test_playlist -v
"""
import unittest

import playlist_generator as pg


class BuildPlaylistsTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(pg.build_playlists([]), [])

    def test_fewer_than_limit_still_makes_one(self):
        # the old len(ids)//limit gave range(0) here -> zero playlists (the bug)
        out = pg.build_playlists(["a"] * 30, limit=50)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].count(","), 29)          # 30 ids -> 29 commas

    def test_exact_multiple(self):
        self.assertEqual(len(pg.build_playlists(["a"] * 100, limit=50)), 2)

    def test_partial_tail_not_dropped(self):
        # 120 ids -> 3 chunks (50, 50, 20); the old code dropped the last 20
        out = pg.build_playlists([str(i) for i in range(120)], limit=50)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[-1].count(","), 19)          # last chunk has 20 ids
        self.assertTrue(out[-1].endswith("119"))          # tail preserved

    def test_url_shape(self):
        out = pg.build_playlists(["abcdefghijk", "lmnopqrstuv"], limit=50)
        self.assertEqual(out[0],
            "https://www.youtube.com/watch_videos?video_ids=abcdefghijk,lmnopqrstuv")


class ExtractIdsTest(unittest.TestCase):
    def test_common_url_forms_and_blanks(self):
        lines = [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/abcdefghijk",
            "https://www.youtube.com/shorts/12345678901",
            "",                                            # skipped
            "   ",                                         # skipped
            "ABCDEFGHIJK",                                 # bare id
        ]
        self.assertEqual(pg.extract_ids(lines),
            ["dQw4w9WgXcQ", "abcdefghijk", "12345678901", "ABCDEFGHIJK"])

    def test_url_with_extra_params(self):
        # id must come from v=, not the trailing &list=... (the old last-11 got this wrong)
        self.assertEqual(
            pg.extract_ids(["https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLxyz&t=30"]),
            ["dQw4w9WgXcQ"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
