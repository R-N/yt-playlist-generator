"""
Tests for cleanup_downloads.py pure/IO helpers (id parsing from yt-dlp filenames,
resume-log rewrite, zero-byte detection).

    python -m unittest test_cleanup_downloads -v
"""
import os
import tempfile
import unittest

import cleanup_downloads as cd


class ExtractIdTest(unittest.TestCase):
    def test_with_extension(self):
        self.assertEqual(cd.extract_id("Song [dQw4w9WgXcQ].webm", ".webm"), "dQw4w9WgXcQ")

    def test_wrong_extension_no_match(self):
        self.assertIsNone(cd.extract_id("Song [dQw4w9WgXcQ].webm", ".m4a"))

    def test_without_extension_arg(self):
        self.assertEqual(cd.extract_id("Song [dQw4w9WgXcQ].part"), "dQw4w9WgXcQ")

    def test_no_bracketed_id(self):
        self.assertIsNone(cd.extract_id("no id here.webm", ".webm"))


class FilterIdsTest(unittest.TestCase):
    def test_drops_files_without_ids_and_aligns(self):
        files = ["a [dQw4w9WgXcQ].webm", "junk.webm", "b [abcdefghijk].webm"]
        ids, kept = cd.filter_ids(files, ".webm")
        self.assertEqual(ids, ["dQw4w9WgXcQ", "abcdefghijk"])
        self.assertEqual(kept, ["a [dQw4w9WgXcQ].webm", "b [abcdefghijk].webm"])

    def test_empty(self):
        self.assertEqual(cd.filter_ids(["junk.webm"], ".webm"), ([], []))


class RemoveIdsFromFileTest(unittest.TestCase):
    def test_keeps_ids_not_in_remove_set(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "downloaded_ids.txt")
            with open(p, "w") as f:
                f.write("keepme1____\nremoveme1__\nkeepme2____\n")
            cd.remove_ids_from_file({"removeme1__"}, p)
            with open(p) as f:
                remaining = {line.strip() for line in f if line.strip()}
            self.assertEqual(remaining, {"keepme1____", "keepme2____"})

    def test_missing_file_is_noop(self):
        cd.remove_ids_from_file({"x"}, os.path.join(tempfile.gettempdir(), "does_not_exist_xyz.txt"))


class ZeroByteTest(unittest.TestCase):
    def test_finds_only_empty_files(self):
        with tempfile.TemporaryDirectory() as d:
            empty = os.path.join(d, "empty [dQw4w9WgXcQ].webm")
            full = os.path.join(d, "full [abcdefghijk].webm")
            open(empty, "w").close()
            with open(full, "w") as f:
                f.write("data")
            found = cd.get_zero_byte_files(d)
            self.assertEqual(found, [empty])


if __name__ == "__main__":
    unittest.main(verbosity=2)
