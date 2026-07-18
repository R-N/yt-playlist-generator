"""
Tests for cleanup_tracked.py — the DESTRUCTIVE script that deletes source mp3s
already verified in the matches CSV. The safety gate is load_processed_files()
(only check==1 rows count as "passing") and main() must delete nothing else.

    python -m unittest test_cleanup_tracked -v

Everything runs in a temp dir; no real music or CSV is touched.
"""
import os
import tempfile
import unittest

import pandas as pd

import cleanup_tracked as ct


class LoadProcessedFilesTest(unittest.TestCase):
    def test_default_source_is_live_matches_csv(self):
        self.assertEqual(ct.PROCESSED_FILE, "matches.csv")

    def _load(self, rows):
        with tempfile.TemporaryDirectory() as d:
            csv = os.path.join(d, "p.csv")
            pd.DataFrame(rows).to_csv(csv, index=False)
            orig = ct.PROCESSED_FILE
            ct.PROCESSED_FILE = csv
            try:
                return ct.load_processed_files()
            finally:
                ct.PROCESSED_FILE = orig

    def test_only_checked_rows_pass(self):
        passes = self._load([
            {"filename": "A.mp3", "yt_id": "a", "check": 1},
            {"filename": "B.mp3", "yt_id": "b", "check": 0},
            {"filename": "C.mp3", "yt_id": "c", "check": None},
        ])
        self.assertTrue(passes["A.mp3"])
        self.assertFalse(passes["B.mp3"])
        self.assertFalse(passes["C.mp3"])

    def test_string_checks_do_not_pass(self):
        passes = self._load([
            {"filename": "A.mp3", "yt_id": "a", "check": "1"},
            {"filename": "B.mp3", "yt_id": "b", "check": "True"},
        ])
        self.assertFalse(passes["A.mp3"])
        self.assertFalse(passes["B.mp3"])

    def test_blank_yt_id_rows_dropped(self):
        passes = self._load([
            {"filename": "A.mp3", "yt_id": None, "check": 1},   # no yt_id -> dropped entirely
            {"filename": "B.mp3", "yt_id": "b", "check": 1},
        ])
        self.assertNotIn("A.mp3", passes)
        self.assertTrue(passes["B.mp3"])

    def test_duplicate_filename_keeps_passing(self):
        # sort_values(check) then keep=last -> the checked row wins the dedupe
        passes = self._load([
            {"filename": "A.mp3", "yt_id": "a", "check": 0},
            {"filename": "A.mp3", "yt_id": "a", "check": 1},
        ])
        self.assertTrue(passes["A.mp3"])


class MainDeletionSafetyTest(unittest.TestCase):
    def test_empty_folder_config_is_safe_no_op(self):
        orig = ct.MP3_FOLDERS
        ct.MP3_FOLDERS = []
        try:
            ct.main()
        finally:
            ct.MP3_FOLDERS = orig

    def test_deletes_only_passing_files(self):
        with tempfile.TemporaryDirectory() as music, tempfile.TemporaryDirectory() as cfg:
            for name in ("A.mp3", "B.mp3", "C.mp3"):
                open(os.path.join(music, name), "w").close()
            csv = os.path.join(cfg, "p.csv")
            pd.DataFrame([
                {"filename": "A.mp3", "yt_id": "a", "check": 1},   # passing -> delete
                {"filename": "B.mp3", "yt_id": "b", "check": 0},   # rejected -> keep
            ]).to_csv(csv, index=False)                            # C.mp3 absent -> keep

            orig = (ct.MP3_FOLDERS, ct.PROCESSED_FILE)
            ct.MP3_FOLDERS = [music]
            ct.PROCESSED_FILE = csv
            try:
                ct.main()
            finally:
                ct.MP3_FOLDERS, ct.PROCESSED_FILE = orig

            self.assertFalse(os.path.exists(os.path.join(music, "A.mp3")))   # deleted
            self.assertTrue(os.path.exists(os.path.join(music, "B.mp3")))    # kept
            self.assertTrue(os.path.exists(os.path.join(music, "C.mp3")))    # kept

    def test_ambiguous_approved_basename_is_never_deleted(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second, tempfile.TemporaryDirectory() as cfg:
            for folder in (first, second):
                open(os.path.join(folder, "same.mp3"), "w").close()
            csv = os.path.join(cfg, "p.csv")
            pd.DataFrame([{"filename": "same.mp3", "yt_id": "a", "check": 1}]).to_csv(csv, index=False)

            orig = (ct.MP3_FOLDERS, ct.PROCESSED_FILE)
            ct.MP3_FOLDERS, ct.PROCESSED_FILE = [first, second], csv
            try:
                ct.main()
            finally:
                ct.MP3_FOLDERS, ct.PROCESSED_FILE = orig

            self.assertTrue(os.path.exists(os.path.join(first, "same.mp3")))
            self.assertTrue(os.path.exists(os.path.join(second, "same.mp3")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
