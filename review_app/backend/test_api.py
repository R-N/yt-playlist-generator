"""
API-layer tests (FastAPI TestClient). Requires: fastapi, httpx.

    python -m unittest test_api -v

Redirects db paths AND main.MP3_FOLDERS to a temp dir, so no real data or
music files are touched. TestClient's context manager fires startup (which
seeds the DB and builds the read-only file index).
"""
import json
import os
import tempfile
import types
import unittest
from unittest import mock

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


class RowsNanApiTest(ApiTestBase):
    """End-to-end guard for the NaN-500 bug: a NaN in the DB must serialize through
    the real Starlette JSON encoder (allow_nan=False), not just db._expand_extra.
    This is the layer the unit test can't reach — it 500'd in production before."""

    def test_rows_serializes_with_nan_in_db(self):
        conn = db.connect()
        try:
            conn.execute(
                'UPDATE tracks SET score = ?, extra_json = ? WHERE filename = ?',
                (float("nan"), json.dumps({"ac_score": float("nan"), "mb_artist": "X"}), "A.mp3"),
            )
            conn.commit()
        finally:
            conn.close()
        r = self.client.get("/api/rows?status=all")
        self.assertEqual(r.status_code, 200)          # was 500 before the scrub
        row = next(x for x in r.json()["rows"] if x["filename"] == "A.mp3")
        self.assertIsNone(row["score"])
        self.assertIsNone(row["ac_score"])
        self.assertEqual(row["mb_artist"], "X")


class PlaylistApiTest(ApiTestBase):
    def test_generates_chunked_playlists_from_pasted_urls(self):
        # 51 ids -> 2 playlists (50 + 1); the tail must not be dropped
        lines = [f"https://www.youtube.com/watch?v={'a'*10}{i%10}" for i in range(51)]
        r = self.client.post("/api/playlists", json={"text": "\n".join(lines)})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["id_count"], 51)
        self.assertEqual(len(body["playlists"]), 2)
        self.assertTrue(body["playlists"][0].startswith(
            "https://www.youtube.com/watch_videos?video_ids="))

    def test_empty_input(self):
        r = self.client.post("/api/playlists", json={"text": "  \n \n"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"id_count": 0, "playlists": []})


class YtAudioTest(ApiTestBase):
    """The /api/yt_audio redirect. The resolver is stubbed so no test hits yt-dlp."""

    def test_invalid_id_rejected_without_calling_yt_dlp(self):
        r = self.client.get("/api/yt_audio/too-short")   # not 11 valid chars
        self.assertEqual(r.status_code, 400)

    def test_valid_id_redirects_to_resolved_url(self):
        orig = main._resolve_yt_audio
        main._resolve_yt_audio = lambda yid: "https://example.com/s?v=" + yid
        try:
            r = self.client.get("/api/yt_audio/dQw4w9WgXcQ", follow_redirects=False)
            self.assertIn(r.status_code, (302, 307))
            self.assertEqual(r.headers["location"], "https://example.com/s?v=dQw4w9WgXcQ")
        finally:
            main._resolve_yt_audio = orig

    def test_unresolvable_returns_502(self):
        orig = main._resolve_yt_audio
        main._resolve_yt_audio = lambda yid: None
        try:
            r = self.client.get("/api/yt_audio/dQw4w9WgXcQ")
            self.assertEqual(r.status_code, 502)
        finally:
            main._resolve_yt_audio = orig


class ResolveYtAudioTest(unittest.TestCase):
    """_resolve_yt_audio's subprocess handling (yt-dlp stubbed — no network)."""

    def _run(self, returncode, stdout):
        return types.SimpleNamespace(returncode=returncode, stdout=stdout)

    def test_returns_first_stdout_line(self):
        with mock.patch("main.subprocess.run",
                        return_value=self._run(0, "http://a/stream\nhttp://b\n")):
            self.assertEqual(main._resolve_yt_audio("dQw4w9WgXcQ"), "http://a/stream")

    def test_none_on_nonzero_returncode(self):
        with mock.patch("main.subprocess.run", return_value=self._run(1, "")):
            self.assertIsNone(main._resolve_yt_audio("dQw4w9WgXcQ"))

    def test_none_on_subprocess_error(self):
        with mock.patch("main.subprocess.run", side_effect=OSError("yt-dlp missing")):
            self.assertIsNone(main._resolve_yt_audio("dQw4w9WgXcQ"))

    def test_cmd_targets_the_requested_id(self):
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return self._run(0, "u\n")

        with mock.patch("main.subprocess.run", side_effect=fake_run):
            main._resolve_yt_audio("dQw4w9WgXcQ")
        self.assertIn("https://www.youtube.com/watch?v=dQw4w9WgXcQ", captured["cmd"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
