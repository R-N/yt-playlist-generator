"""
API-layer tests (FastAPI TestClient). Requires: fastapi, httpx.

    python -m unittest test_api -v

Redirects db paths AND main.MP3_FOLDERS to a temp dir, so no real data or
music files are touched. TestClient's context manager fires startup (which
seeds the DB and builds the read-only file index).
"""
import os
import tempfile
import unittest

import pandas as pd
from fastapi.testclient import TestClient

import db
import main


def write_matches(path, rows):
    df = pd.DataFrame(rows)
    if path.lower().endswith(".xlsx"):
        df.to_excel(path, index=False)
    else:
        df.to_csv(path, index=False)


class ApiTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self._orig_db = {k: getattr(db, k) for k in
                         ("DB_PATH", "MATCHES_CSV", "MATCHES_XLSX",
                          "MATCHES_SOURCE", "BACKUP_DIR")}
        self._orig_main = {k: getattr(main, k) for k in
                           ("MP3_FOLDERS", "AUTO_EXPORT_EVERY", "_FILE_INDEX",
                            "_decision_count")}
        db.DB_PATH = os.path.join(d, "review.db")
        db.MATCHES_CSV = os.path.join(d, "matches.csv")
        db.MATCHES_XLSX = os.path.join(d, "matches.xlsx")
        db.MATCHES_SOURCE = db.MATCHES_XLSX
        db.BACKUP_DIR = os.path.join(d, "backups")

        music = os.path.join(d, "music")
        os.makedirs(music)
        # A.mp3 has a real file; B.mp3 is in the DB but has no file on disk
        self.audio_bytes = b"ID3" + b"\x00" * 100
        with open(os.path.join(music, "A.mp3"), "wb") as f:
            f.write(self.audio_bytes)

        seed = [
            {"filename": "A.mp3", "yt_id": "a", "check": None},
            {"filename": "B.mp3", "yt_id": "b", "check": None},
        ]
        write_matches(db.MATCHES_CSV, seed)
        write_matches(db.MATCHES_XLSX, seed)

        main.MP3_FOLDERS = [music]
        main.AUTO_EXPORT_EVERY = 1000   # effectively off unless a test lowers it
        main._FILE_INDEX = {}
        main._decision_count = 0

        self.client = TestClient(main.app)
        self.client.__enter__()         # trigger startup
        self.ids = {r["filename"]: r["id"]
                    for r in self.client.get("/api/rows?status=all").json()["rows"]}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        for k, v in self._orig_db.items():
            setattr(db, k, v)
        for k, v in self._orig_main.items():
            setattr(main, k, v)
        self.tmp.cleanup()


class EndpointTest(ApiTestBase):
    def test_counts(self):
        c = self.client.get("/api/counts").json()
        self.assertEqual(c, {"total": 2, "unreviewed": 2, "approved": 0, "rejected": 0})

    def test_rows_status_filter(self):
        un = self.client.get("/api/rows?status=unreviewed").json()
        self.assertEqual(un["total"], 2)
        self.assertIn("has_local", un["rows"][0])   # endpoint annotates file presence

    def test_decision_updates_counts(self):
        r = self.client.post("/api/decision",
                             json={"track_id": self.ids["A.mp3"], "decision": True})
        self.assertEqual(r.json()["check"], 1)
        self.assertEqual(self.client.get("/api/counts").json()["approved"], 1)

    def test_decision_unknown_track_404(self):
        r = self.client.post("/api/decision",
                             json={"track_id": 999999, "decision": True})
        self.assertEqual(r.status_code, 404)


class AutoExportTest(ApiTestBase):
    def test_triggers_every_n(self):
        main.AUTO_EXPORT_EVERY = 2
        main._decision_count = 0
        r1 = self.client.post("/api/decision",
                             json={"track_id": self.ids["A.mp3"], "decision": True})
        self.assertNotIn("auto_exported", r1.json())     # 1st: no export yet
        r2 = self.client.post("/api/decision",
                             json={"track_id": self.ids["B.mp3"], "decision": True})
        self.assertTrue(r2.json().get("auto_exported"))   # 2nd: fired
        # and the marks were written to the csv
        out = pd.read_csv(db.MATCHES_CSV)
        self.assertEqual(int((out["check"] == 1).sum()), 2)


class AudioTest(ApiTestBase):
    def test_audio_ok_full(self):
        r = self.client.get(f"/api/audio/{self.ids['A.mp3']}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, self.audio_bytes)
        self.assertEqual(r.headers["content-type"], "audio/mpeg")

    def test_audio_range_206(self):
        # the scrubbing claim: FileResponse must honor Range
        r = self.client.get(f"/api/audio/{self.ids['A.mp3']}",
                            headers={"Range": "bytes=0-3"})
        self.assertEqual(r.status_code, 206)
        self.assertEqual(r.content, self.audio_bytes[:4])
        self.assertIn("content-range", r.headers)

    def test_audio_404_when_file_missing(self):
        # B.mp3 is in the DB but no file on disk -> 404, never a 500/leak
        r = self.client.get(f"/api/audio/{self.ids['B.mp3']}")
        self.assertEqual(r.status_code, 404)

    def test_audio_404_unknown_track(self):
        r = self.client.get("/api/audio/999999")
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
