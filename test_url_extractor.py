"""
Tests for url_extractor.py — harvest YouTube ids from a chat dump, filtered by author.

    python -m unittest test_url_extractor -v
"""
import os
import tempfile
import unittest

import pandas as pd

import url_extractor as ux


class ExtractYoutubeIdTest(unittest.TestCase):
    def test_watch_and_shortlink_forms(self):
        self.assertEqual(ux.extract_youtube_id("see https://www.youtube.com/watch?v=dQw4w9WgXcQ now"),
                         "dQw4w9WgXcQ")
        self.assertEqual(ux.extract_youtube_id("https://youtu.be/abcdefghijk"), "abcdefghijk")

    def test_no_id_and_non_string(self):
        self.assertIsNone(ux.extract_youtube_id("just some text"))
        self.assertIsNone(ux.extract_youtube_id(None))
        self.assertIsNone(ux.extract_youtube_id(float("nan")))


class MainTest(unittest.TestCase):
    def test_author_filter_dedupe_and_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            dump = os.path.join(d, "dump.csv")
            pd.DataFrame([
                {"Author": "linearch", "Content": "https://youtu.be/aaaaaaaaaaa"},
                {"Author": "linearch", "Content": "dup https://www.youtube.com/watch?v=aaaaaaaaaaa"},
                {"Author": "linearch", "Content": "https://youtu.be/bbbbbbbbbbb"},
                {"Author": "someone_else", "Content": "https://youtu.be/ccccccccccc"},  # filtered out
                {"Author": "linearch", "Content": "no link here"},
            ]).to_csv(dump, index=False)

            orig = (ux.dump_file_name, ux.id_file_name, ux.url_file_name,
                    ux.playlist_file_name, ux.username)
            ux.dump_file_name = dump
            ux.id_file_name = os.path.join(d, "ids1.txt")
            ux.url_file_name = os.path.join(d, "urls.txt")
            ux.playlist_file_name = os.path.join(d, "playlists.txt")
            ux.username = "linearch"
            try:
                ux.main()
            finally:
                (ux.dump_file_name, ux.id_file_name, ux.url_file_name,
                 ux.playlist_file_name, ux.username) = orig

            with open(os.path.join(d, "ids1.txt")) as f:
                ids = [l.strip() for l in f if l.strip()]
            self.assertEqual(ids, ["aaaaaaaaaaa", "bbbbbbbbbbb"])   # deduped, author-filtered
            with open(os.path.join(d, "playlists.txt")) as f:
                playlists = f.read()
            self.assertIn("video_ids=aaaaaaaaaaa,bbbbbbbbbbb", playlists)


if __name__ == "__main__":
    unittest.main(verbosity=2)
