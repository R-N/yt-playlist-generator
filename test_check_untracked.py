"""
Tests for check_untracked.py — lists library files NOT yet verified in matches.csv
(into untracked.txt). Inverse of the cleanup_tracked gate: a file is untracked when
it is absent from the CSV or its check is not passing.

    python -m unittest test_check_untracked -v
"""
import os
import tempfile
import unittest

import pandas as pd

import check_untracked as cu


class MainUntrackedListTest(unittest.TestCase):
    def test_lists_only_unverified_files(self):
        with tempfile.TemporaryDirectory() as music, tempfile.TemporaryDirectory() as cfg:
            for name in ("A.mp3", "B.mp3", "C.mp3"):
                open(os.path.join(music, name), "w").close()
            csv = os.path.join(cfg, "p.csv")
            out = os.path.join(cfg, "untracked.txt")
            pd.DataFrame([
                {"filename": "A.mp3", "yt_id": "a", "check": 1},   # verified -> NOT untracked
                {"filename": "B.mp3", "yt_id": "b", "check": 0},   # rejected -> untracked
            ]).to_csv(csv, index=False)                            # C.mp3 absent -> untracked

            orig = (cu.MP3_FOLDERS, cu.PROCESSED_FILE, cu.OUTPUT_FILE)
            cu.MP3_FOLDERS = [music]
            cu.PROCESSED_FILE = csv
            cu.OUTPUT_FILE = out
            try:
                cu.main()
            finally:
                cu.MP3_FOLDERS, cu.PROCESSED_FILE, cu.OUTPUT_FILE = orig

            with open(out, encoding="utf-8") as f:
                listed = {line.strip() for line in f if line.strip()}
            self.assertEqual(listed, {"B.mp3", "C.mp3"})           # A (verified) excluded


if __name__ == "__main__":
    unittest.main(verbosity=2)
