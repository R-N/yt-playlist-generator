"""
Tests for the review-app data layer (stdlib unittest, no extra deps).

Run from review_app/backend:
    python -m unittest test_db -v

Every test redirects db's module globals to a fresh temp dir, so the real
matches.csv / matches.xlsx are never read or written.
"""
import json
import os
import sqlite3
import tempfile
import unittest

import pandas as pd

import db


def write_matches(path, rows):
    """rows: list of dicts -> csv or xlsx by extension."""
    df = pd.DataFrame(rows)
    if path.lower().endswith(".xlsx"):
        df.to_excel(path, index=False)
    else:
        df.to_csv(path, index=False)


class DbTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        # snapshot + redirect every path db touches
        self._orig = {k: getattr(db, k) for k in
                      ("DB_PATH", "MATCHES_CSV", "MATCHES_XLSX",
                       "MATCHES_SOURCE", "BACKUP_DIR")}
        db.DB_PATH = os.path.join(d, "review.db")
        db.MATCHES_CSV = os.path.join(d, "matches.csv")
        db.MATCHES_XLSX = os.path.join(d, "matches.xlsx")
        db.MATCHES_SOURCE = db.MATCHES_XLSX
        db.BACKUP_DIR = os.path.join(d, "backups")

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(db, k, v)
        self.tmp.cleanup()


class CoerceCheckTest(unittest.TestCase):
    def test_truthy_falsy_blank(self):
        for v in (1, "1", "True", "TRUE", True, 1.0):
            self.assertEqual(db._coerce_check(v), 1, v)
        for v in (0, "0", "False", "FALSE", False, 0.0):
            self.assertEqual(db._coerce_check(v), 0, v)
        for v in (None, float("nan"), "", "maybe"):
            self.assertIsNone(db._coerce_check(v), v)


class ReconcileTest(DbTestBase):
    def _seed(self):
        # CSV: new candidates land here (incl. a CSV-only row D)
        write_matches(db.MATCHES_CSV, [
            {"filename": "A.mp3", "yt_id": "a", "check": None},
            {"filename": "B.mp3", "yt_id": "b", "check": 1},     # only csv marked
            {"filename": "C.mp3", "yt_id": "c", "check": 0},     # conflicts w/ xlsx
            {"filename": "D.mp3", "yt_id": "d", "check": 1},     # CSV-only row
        ])
        # XLSX: human marks live here (authoritative)
        write_matches(db.MATCHES_XLSX, [
            {"filename": "A.mp3", "yt_id": "a", "check": 1},     # xlsx decides
            {"filename": "B.mp3", "yt_id": "b", "check": None},  # blank -> csv fills
            {"filename": "C.mp3", "yt_id": "c", "check": 1},     # wins conflict
            {"filename": "E.mp3", "yt_id": "e", "check": None},  # xlsx-only row
        ])

    def test_union_priority_fill_conflict(self):
        self._seed()
        df, info = db.reconcile_frames()
        marks = {r["filename"]: db._coerce_check(r["check"]) for _, r in df.iterrows()}

        self.assertEqual(info["total"], 5)                 # A B C D E
        self.assertEqual(info["csv_only_rows"], 1)         # D kept
        self.assertEqual(info["marks_filled_from_csv"], 1) # B filled from csv
        self.assertEqual(info["mark_conflicts_xlsx_won"], 1)  # C
        self.assertEqual(info["approved"], 4)              # A B C D

        self.assertEqual(marks["A.mp3"], 1)   # xlsx priority
        self.assertEqual(marks["B.mp3"], 1)   # csv fill
        self.assertEqual(marks["C.mp3"], 1)   # xlsx won conflict (not 0)
        self.assertEqual(marks["D.mp3"], 1)   # csv-only preserved
        self.assertIsNone(marks["E.mp3"])     # still unreviewed

    def test_no_marks_lost_either_direction(self):
        self._seed()
        df, _ = db.reconcile_frames()
        # every row that was approved in EITHER file is approved after merge
        marks = {r["filename"]: db._coerce_check(r["check"]) for _, r in df.iterrows()}
        for name in ("A.mp3", "B.mp3", "C.mp3", "D.mp3"):
            self.assertEqual(marks[name], 1, f"{name} lost its mark")


class ImportAndDecisionTest(DbTestBase):
    def setUp(self):
        super().setUp()
        write_matches(db.MATCHES_CSV, [
            {"filename": "A.mp3", "yt_id": "a", "check": 1},
            {"filename": "B.mp3", "yt_id": "b", "check": None},
        ])
        write_matches(db.MATCHES_XLSX, [
            {"filename": "A.mp3", "yt_id": "a", "check": 1},
            {"filename": "B.mp3", "yt_id": "b", "check": None},
        ])
        db.init_db()

    def test_counts_after_import(self):
        self.assertEqual(db.counts(),
                         {"total": 2, "unreviewed": 1, "approved": 1, "rejected": 0})

    def test_decision_updates_and_appends(self):
        rows, _ = db.get_rows(status="unreviewed")
        tid = rows[0]["id"]

        db.record_decision(tid, True)
        self.assertEqual(db.counts()["approved"], 2)

        # append-only: changing the mark adds a second decision, not an overwrite
        db.record_decision(tid, False)
        self.assertEqual(db.counts()["rejected"], 1)

        conn = db.connect()
        try:
            n = conn.execute("SELECT COUNT(*) FROM decisions WHERE track_id=?",
                             (tid,)).fetchone()[0]
            seq = [r[0] for r in conn.execute(
                "SELECT decision FROM decisions WHERE track_id=? ORDER BY id", (tid,))]
        finally:
            conn.close()
        self.assertEqual(n, 2)
        self.assertEqual(seq, [1, 0])   # full history preserved

    def test_decision_unknown_track_raises(self):
        with self.assertRaises(KeyError):
            db.record_decision(999999, True)


class CsvSyncTest(DbTestBase):
    def test_sync_preserves_ids_marks_history_and_absent_extras(self):
        write_matches(db.MATCHES_CSV, [{
            "filename": "A.mp3", "artist": "old", "yt_id": "old-id",
            "check": None, "mb_artist": "kept",
        }])
        write_matches(db.MATCHES_XLSX, [{"filename": "A.mp3", "check": None,
                                         "mb_artist": "kept"}])
        db.init_db()
        tid = db.get_rows()[0][0]["id"]
        db.record_decision(tid, True)

        write_matches(db.MATCHES_CSV, [
            {"filename": "A.mp3", "artist": "new", "yt_id": "new-id",
             "check": None, "mb_title": "incoming"},
            {"filename": "B.mp3", "artist": "added", "yt_id": "b"},
        ])
        db.sync_matches_csv()
        rows, _ = db.get_rows()
        by_name = {r["filename"]: r for r in rows}
        self.assertEqual(by_name["A.mp3"]["id"], tid)
        self.assertEqual(by_name["A.mp3"]["check"], 1)
        self.assertEqual(by_name["A.mp3"]["artist"], "new")
        self.assertEqual(by_name["A.mp3"]["mb_artist"], "kept")
        self.assertEqual(by_name["A.mp3"]["mb_title"], "incoming")
        self.assertEqual(db.counts()["total"], 2)
        conn = db.connect()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0], 1)
        finally:
            conn.close()

        db.sync_matches_csv()
        self.assertEqual(db.counts()["total"], 2)

    def test_synced_enrichment_fields_survive_canonical_exports(self):
        seed = [{"filename": "A.mp3", "yt_id": "a", "check": None}]
        write_matches(db.MATCHES_CSV, seed)
        write_matches(db.MATCHES_XLSX, seed)
        db.init_db()
        write_matches(db.MATCHES_CSV, [{
            "filename": "A.mp3", "yt_id": "a", "check": None,
            "mb_artist": "Artist", "mb_title": "Title", "ac_score": 0.91,
            "ac_done": True, "arbitrary_extra": "kept",
        }])
        db.sync_matches_csv()
        db.export_matches()
        for path in (db.MATCHES_CSV, db.MATCHES_XLSX):
            out = pd.read_csv(path) if path.endswith(".csv") else pd.read_excel(path)
            row = out.iloc[0]
            self.assertEqual(row["mb_artist"], "Artist")
            self.assertEqual(row["mb_title"], "Title")
            self.assertAlmostEqual(float(row["ac_score"]), 0.91)
            self.assertEqual(str(row["ac_done"]).lower(), "true")
            self.assertEqual(row["arbitrary_extra"], "kept")


class WorkspaceSchemaTest(DbTestBase):
    def test_catalog_sync_handles_more_than_one_thousand_records(self):
        write_matches(db.MATCHES_CSV, [{"filename": "song.mp3", "yt_id": "a"}])
        write_matches(db.MATCHES_XLSX, [{"filename": "song.mp3", "yt_id": "a"}])
        db.init_db()
        conn = db.connect()
        try:
            conn.execute(
                "INSERT INTO track_file_links "
                "(track_id, folder_identity, relative_path, available) VALUES (1,?,?,1)",
                ("old", "song.mp3"),
            )
            conn.commit()
        finally:
            conn.close()
        records = [{"folder_identity": "catalog", "relative_path": f"{i}/song.mp3",
                    "basename": f"song-{i}.mp3", "file_size": i, "modified_at": str(i)}
                   for i in range(1201)]
        records[0]["basename"] = "song.mp3"
        db.sync_catalog_links(records)
        conn = db.connect()
        try:
            self.assertEqual(conn.execute(
                "SELECT available FROM track_file_links WHERE folder_identity='old'"
            ).fetchone()[0], 0)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM track_file_links WHERE folder_identity='catalog'"
            ).fetchone()[0], 1)
        finally:
            conn.close()

    def _tables(self):
        conn = db.connect()
        try:
            return {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )}
        finally:
            conn.close()

    def test_existing_curation_survives_repeated_init(self):
        write_matches(db.MATCHES_CSV, [{"filename": "A.mp3", "yt_id": "same"}])
        write_matches(db.MATCHES_XLSX, [{"filename": "A.mp3", "yt_id": "same"}])
        db.init_db()
        track_id = db.get_rows()[0][0]["id"]
        db.record_decision(track_id, True)
        before = self._tables()
        db.init_db()
        self.assertEqual(before, self._tables())
        conn = db.connect()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0], 1)
        finally:
            conn.close()

    def test_workspace_allows_shared_youtube_id_and_exact_file_links(self):
        youtube_id = "same1234567"
        write_matches(db.MATCHES_CSV, [
            {"filename": "A.mp3", "yt_id": youtube_id},
            {"filename": "B.mp3", "yt_id": youtube_id},
        ])
        write_matches(db.MATCHES_XLSX, [
            {"filename": "A.mp3", "yt_id": "same"},
            {"filename": "B.mp3", "yt_id": "same"},
        ])
        db.init_db()
        conn = db.connect()
        try:
            tracks = [r[0] for r in conn.execute("SELECT id FROM tracks ORDER BY id")]
            conn.execute(
                "INSERT INTO workspace_items "
                "(youtube_id, youtube_url, position, provenance, track_id) VALUES (?,?,?,?,?)",
                (youtube_id, "https://www.youtube.com/watch?v=" + youtube_id,
                 0, "library", tracks[0]),
            )
            conn.execute(
                "INSERT INTO workspace_items "
                "(youtube_id, youtube_url, position, provenance, track_id) VALUES (?,?,?,?,?)",
                (youtube_id, "https://www.youtube.com/watch?v=" + youtube_id,
                 1, "library", tracks[1]),
            )
            conn.execute(
                "INSERT INTO workspace_items "
                "(youtube_id, youtube_url, position, provenance) VALUES (?,?,?,?)",
                (youtube_id, "https://www.youtube.com/watch?v=" + youtube_id,
                 2, "generic"),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO workspace_items "
                    "(youtube_id, youtube_url, position, provenance) VALUES (?,?,?,?)",
                    (youtube_id, "https://www.youtube.com/watch?v=" + youtube_id,
                     3, "generic"),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO workspace_items "
                    "(youtube_id, youtube_url, position, provenance) VALUES (?,?,?,?)",
                    ("bad", "https://www.youtube.com/watch?v=bad", 4, "generic"),
                )
            conn.execute(
                "INSERT INTO track_file_links "
                "(track_id, folder_identity, relative_path) VALUES (?,?,?)",
                (tracks[0], "library-a", "disc/song.mp3"),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO track_file_links "
                    "(track_id, folder_identity, relative_path) VALUES (?,?,?)",
                    (tracks[1], "library-a", "disc/song.mp3"),
                )
            conn.commit()
        finally:
            conn.close()

    def test_saved_link_dedupe_and_run_snapshot(self):
        youtube_id = "abc12345678"
        write_matches(db.MATCHES_CSV, [{"filename": "A.mp3", "yt_id": "a"}])
        write_matches(db.MATCHES_XLSX, [{"filename": "A.mp3", "yt_id": "a"}])
        db.init_db()
        conn = db.connect()
        try:
            conn.execute(
                "INSERT INTO saved_links (youtube_id, youtube_url) VALUES (?,?)",
                (youtube_id, "https://www.youtube.com/watch?v=" + youtube_id),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO saved_links (youtube_id, youtube_url) VALUES (?,?)",
                    (youtube_id, "https://www.youtube.com/watch?v=" + youtube_id),
                )
            conn.execute(
                "INSERT INTO workspace_runs (operation, status) VALUES (?,?)",
                ("download", "queued"),
            )
            run = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            track_id = conn.execute("SELECT id FROM tracks").fetchone()[0]
            conn.execute(
                "INSERT INTO workspace_run_items "
                "(run_id, position, youtube_id, youtube_url, provenance, track_id) "
                "VALUES (?,?,?,?,?,?)",
                (run, 0, youtube_id,
                 "https://www.youtube.com/watch?v=" + youtube_id, "saved_links", track_id),
            )
            # Queued snapshots remain editable while being assembled.
            conn.execute(
                "UPDATE workspace_run_items SET provenance = ? WHERE run_id = ?",
                ("queued-edit", run),
            )
            conn.execute("UPDATE workspace_runs SET status = 'running' WHERE id = ?", (run,))
            other_id = "other123456"
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO workspace_run_items "
                    "(run_id, position, youtube_id, youtube_url) VALUES (?,?,?,?)",
                    (run, 1, other_id,
                     "https://www.youtube.com/watch?v=" + other_id),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE workspace_run_items SET provenance = ? WHERE run_id = ?",
                    ("late-edit", run),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM workspace_run_items WHERE run_id = ?", (run,))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
            conn.commit()
            db.init_db()
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM workspace_run_items WHERE run_id = ?", (run,)
            ).fetchone()[0], 1)
        finally:
            conn.close()

    def test_legacy_upgrade_preserves_tracks_decisions_and_adds_constraints(self):
        conn = db.connect()
        try:
            conn.executescript("""
                CREATE TABLE tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT UNIQUE NOT NULL,
                    "check" INTEGER,
                    extra_json TEXT
                );
                CREATE TABLE decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    track_id INTEGER NOT NULL REFERENCES tracks(id),
                    filename TEXT NOT NULL,
                    yt_id TEXT,
                    decision INTEGER NOT NULL,
                    ts TEXT NOT NULL
                );
                INSERT INTO tracks (filename, "check") VALUES ('legacy.mp3', 1);
                INSERT INTO decisions (track_id, filename, decision, ts)
                    VALUES (1, 'legacy.mp3', 1, '2026-01-01T00:00:00');
            """)
            conn.commit()
        finally:
            conn.close()

    def test_old_phase1_workspace_migrates_transactionally(self):
        youtube_id = "migrate1234"  # 11 canonical characters
        conn = db.connect()
        try:
            conn.executescript("""
                CREATE TABLE tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT UNIQUE NOT NULL,
                    "check" INTEGER,
                    extra_json TEXT
                );
                CREATE TABLE decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    track_id INTEGER NOT NULL REFERENCES tracks(id),
                    filename TEXT NOT NULL, yt_id TEXT,
                    decision INTEGER NOT NULL, ts TEXT NOT NULL
                );
                CREATE TABLE workspace_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, youtube_id TEXT NOT NULL,
                    youtube_url TEXT NOT NULL, title TEXT, channel TEXT,
                    position INTEGER NOT NULL, provenance TEXT NOT NULL,
                    track_id INTEGER REFERENCES tracks(id), metadata_json TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE saved_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, youtube_id TEXT NOT NULL,
                    youtube_url TEXT NOT NULL, track_id INTEGER,
                    metadata_json TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(youtube_id)
                );
                CREATE TABLE track_file_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, track_id INTEGER NOT NULL,
                    folder_identity TEXT NOT NULL, relative_path TEXT NOT NULL,
                    available INTEGER DEFAULT 1, file_size INTEGER, modified_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE workspace_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, operation TEXT NOT NULL,
                    status TEXT NOT NULL, started_at TEXT, finished_at TEXT,
                    error_text TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE workspace_run_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL,
                    position INTEGER NOT NULL, youtube_id TEXT NOT NULL,
                    youtube_url TEXT NOT NULL, title TEXT, channel TEXT,
                    provenance TEXT, track_id INTEGER, metadata_json TEXT,
                    snapshot_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO tracks (filename, "check") VALUES ('legacy.mp3', 1);
                INSERT INTO decisions (track_id, filename, decision, ts)
                    VALUES (1, 'legacy.mp3', 1, '2026-01-01T00:00:00');
                INSERT INTO workspace_items
                    (youtube_id, youtube_url, position, provenance, track_id)
                    VALUES ('migrate1234', 'https://www.youtube.com/watch?v=migrate1234', 0, 'library', 1);
                INSERT INTO workspace_runs (operation, status) VALUES ('download', 'queued');
                INSERT INTO workspace_run_items
                    (run_id, position, youtube_id, youtube_url, track_id)
                    VALUES (1, 0, 'migrate1234', 'https://www.youtube.com/watch?v=migrate1234', 1);
            """)
            conn.commit()
        finally:
            conn.close()

        db.init_db()
        db.init_db()
        conn = db.connect()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0], 1)
            self.assertEqual(conn.execute(
                "SELECT version FROM workspace_schema_meta WHERE id=1"
            ).fetchone()[0], db._WORKSPACE_SCHEMA_VERSION)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO workspace_run_items "
                    "(run_id, position, youtube_id, youtube_url) VALUES (?,?,?,?)",
                    (1, 1, youtube_id, "https://www.youtube.com/watch?v=" + youtube_id),
                )
            conn.execute("UPDATE workspace_runs SET status='running' WHERE id=1")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE workspace_runs SET status='queued' WHERE id=1")
        finally:
            conn.close()

        db.init_db()
        db.init_db()
        conn = db.connect()
        try:
            legacy = conn.execute("SELECT filename, \"check\" FROM tracks").fetchone()
            self.assertEqual((legacy[0], legacy[1]), ("legacy.mp3", 1))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0], 1)
            self.assertIn("workspace_items", self._tables())
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO saved_links (youtube_id, youtube_url) VALUES (?,?)",
                    ("invalid", "https://youtu.be/invalid"),
                )
        finally:
            conn.close()


    def test_startup_interrupts_leftover_runs_with_reason(self):
        db.init_db()
        conn = db.connect()
        try:
            conn.execute("INSERT INTO workspace_runs (operation, status) VALUES ('download', 'running')")
            conn.commit()
        finally:
            conn.close()
        db.init_db()
        run = db.list_workspace_runs()[0]
        self.assertEqual(run["status"], "interrupted")
        self.assertIn("restarted", run["error_text"])


class ExportTest(DbTestBase):
    def setUp(self):
        super().setUp()
        seed = [
            {"filename": "A.mp3", "yt_id": "a", "check": 1},
            {"filename": "B.mp3", "yt_id": "b", "check": None},
        ]
        write_matches(db.MATCHES_CSV, seed)
        write_matches(db.MATCHES_XLSX, seed)
        db.init_db()

    def test_export_snapshots_then_writes_both(self):
        rows, _ = db.get_rows(status="unreviewed")
        db.record_decision(rows[0]["id"], True)   # now 2 approved

        res = db.export_matches()
        self.assertEqual(res["rows"], 2)

        # snapshot of the prior csv + xlsx exists
        baks = os.listdir(db.BACKUP_DIR)
        self.assertTrue(any(b.startswith("matches.csv") for b in baks))
        self.assertTrue(any(b.startswith("matches.xlsx") for b in baks))

        # written files reflect the new mark
        out = pd.read_csv(db.MATCHES_CSV)
        self.assertEqual(int((out["check"] == 1).sum()), 2)
        self.assertTrue(os.path.exists(db.MATCHES_XLSX))

    def test_export_csv_only_no_snapshot(self):
        res = db.export_csv_only()
        self.assertEqual(res["rows"], 2)
        self.assertTrue(os.path.exists(db.MATCHES_CSV))
        # csv-only path makes no backups
        self.assertFalse(os.path.isdir(db.BACKUP_DIR) and os.listdir(db.BACKUP_DIR))


class ExtraJsonRoundTripTest(DbTestBase):
    """Non-core columns survive import -> SQLite (extra_json) -> export."""

    def test_extra_columns_preserved(self):
        seed = [{
            "filename": "A.mp3", "artist": "Art", "title": "Tit", "yt_id": "a",
            "check": 1,
            # non-core columns -> stored in extra_json, must come back on export
            "acoustid_id": "fp-123", "yt_query": "art tit",
            "yt_channel_id": "@chan", "duplicated": False, "pass": True,
        }]
        write_matches(db.MATCHES_CSV, seed)
        write_matches(db.MATCHES_XLSX, seed)
        db.init_db()
        db.export_matches()

        out = pd.read_csv(db.MATCHES_CSV)
        row = out[out["filename"] == "A.mp3"].iloc[0]
        self.assertEqual(row["acoustid_id"], "fp-123")
        self.assertEqual(row["yt_query"], "art tit")
        self.assertEqual(row["yt_channel_id"], "@chan")
        self.assertEqual(str(row["pass"]), "True")
        # and the canonical columns are all present, in order
        self.assertEqual(list(out.columns), db.TRACK_COLUMNS)

    def test_extra_fields_surface_in_get_rows(self):
        # enrichment columns (mb_*) are non-core -> stored in extra_json; the
        # API must still expose them so the review UI can show the cross-check.
        seed = [{
            "filename": "A.mp3", "yt_id": "a", "check": None,
            "mb_artist": "Radiohead", "mb_title": "Creep",
            "mb_confidence": "strong", "mb_suggest": 1, "ac_score": 0.91,
        }]
        write_matches(db.MATCHES_CSV, seed)
        write_matches(db.MATCHES_XLSX, seed)
        db.init_db()
        rows, _ = db.get_rows(status="all")
        r = rows[0]
        self.assertEqual(r["mb_artist"], "Radiohead")
        self.assertEqual(r["mb_confidence"], "strong")
        self.assertEqual(r["mb_suggest"], 1)
        self.assertNotIn("extra_json", r)   # flattened, not leaked as a blob


class ReconcileEdgeTest(DbTestBase):
    def test_csv_only(self):
        write_matches(db.MATCHES_CSV, [{"filename": "A.mp3", "check": 1}])
        df, info = db.reconcile_frames()
        self.assertEqual(info["source"], "csv-only")
        self.assertEqual(len(df), 1)

    def test_xlsx_only(self):
        write_matches(db.MATCHES_XLSX, [{"filename": "A.mp3", "check": 1}])
        df, info = db.reconcile_frames()
        self.assertEqual(info["source"], "xlsx-only")
        self.assertEqual(len(df), 1)

    def test_both_missing(self):
        df, info = db.reconcile_frames()
        self.assertIsNone(df)
        self.assertEqual(info, {})

    def test_duplicate_filename_keeps_last(self):
        # same filename twice in csv -> dedup keeps the last (latest) decision
        write_matches(db.MATCHES_CSV, [
            {"filename": "A.mp3", "yt_id": "old", "check": 0},
            {"filename": "A.mp3", "yt_id": "new", "check": 1},
        ])
        write_matches(db.MATCHES_XLSX, [{"filename": "B.mp3", "check": None}])
        df, info = db.reconcile_frames()
        a = df[df["filename"] == "A.mp3"]
        self.assertEqual(len(a), 1)
        self.assertEqual(a.iloc[0]["yt_id"], "new")
        self.assertEqual(db._coerce_check(a.iloc[0]["check"]), 1)


class PaginationTest(DbTestBase):
    def setUp(self):
        super().setUp()
        rows = [{"filename": f"{i:02d}.mp3", "artist": f"{i:02d}", "check": None}
                for i in range(10)]
        write_matches(db.MATCHES_CSV, rows)
        write_matches(db.MATCHES_XLSX, rows)
        db.init_db()

    def test_limit_offset_and_total(self):
        page1, total = db.get_rows(status="all", limit=4, offset=0)
        page2, _ = db.get_rows(status="all", limit=4, offset=4)
        self.assertEqual(total, 10)             # total ignores the page window
        self.assertEqual(len(page1), 4)
        self.assertEqual([r["filename"] for r in page1],
                         ["00.mp3", "01.mp3", "02.mp3", "03.mp3"])  # ordered
        self.assertEqual(page2[0]["filename"], "04.mp3")           # offset applied

    def test_offset_past_end(self):
        rows, total = db.get_rows(status="all", limit=5, offset=100)
        self.assertEqual(rows, [])
        self.assertEqual(total, 10)


class RollbackTest(DbTestBase):
    """A failure between the two writes of record_decision must roll back
    BOTH -- no decisions row, no changed mark."""

    def setUp(self):
        super().setUp()
        rows = [{"filename": "A.mp3", "yt_id": "a", "check": None}]
        write_matches(db.MATCHES_CSV, rows)
        write_matches(db.MATCHES_XLSX, rows)
        db.init_db()

    def test_update_failure_rolls_back_insert(self):
        import sqlite3

        class FailingConn:
            def __init__(self, real):
                self._real = real

            def execute(self, sql, *a, **k):
                if sql.strip().upper().startswith("UPDATE"):
                    raise sqlite3.OperationalError("boom")
                return self._real.execute(sql, *a, **k)

            def __enter__(self):
                return self._real.__enter__()

            def __exit__(self, *a):
                return self._real.__exit__(*a)

            def __getattr__(self, n):
                return getattr(self._real, n)

        rows, _ = db.get_rows(status="all")
        tid = rows[0]["id"]

        orig_connect = db.connect
        db.connect = lambda: FailingConn(orig_connect())
        try:
            with self.assertRaises(sqlite3.OperationalError):
                db.record_decision(tid, True)
        finally:
            db.connect = orig_connect

        # nothing persisted: decisions empty, mark still NULL
        conn = db.connect()
        try:
            n = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
            chk = conn.execute('SELECT "check" FROM tracks WHERE id=?', (tid,)).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(n, 0)
        self.assertIsNone(chk)


class ExpandExtraNanTest(unittest.TestCase):
    """Blank numeric cells arrive as float NaN (core columns and inside extra_json,
    which json-loads "NaN" back as a float). Starlette's encoder rejects NaN, so
    _expand_extra must scrub non-finite floats to None or /api/rows 500s."""

    def test_scrubs_nan_from_core_and_extra(self):
        import json
        row = {
            "filename": "x.mp3",
            "score": float("nan"),                 # core REAL column, blank -> NaN
            "extra_json": json.dumps({"ac_score": float("nan"), "mb_artist": "Radiohead"}),
        }
        out = db._expand_extra(row)
        self.assertIsNone(out["score"])
        self.assertIsNone(out["ac_score"])         # NaN from the expanded blob too
        self.assertEqual(out["mb_artist"], "Radiohead")
        self.assertNotIn("extra_json", out)

    def test_keeps_finite_values(self):
        out = db._expand_extra({"score": 42.0, "yt_views": 1000})
        self.assertEqual(out["score"], 42.0)
        self.assertEqual(out["yt_views"], 1000)


class WorkspaceMetadataTest(DbTestBase):
    def _seed_item(self):
        write_matches(db.MATCHES_CSV, [{"filename": "song.mp3", "yt_id": "a"}])
        write_matches(db.MATCHES_XLSX, [{"filename": "song.mp3", "yt_id": "a"}])
        db.init_db()
        return db.import_workspace_items([{
            "youtube_id": "dQw4w9WgXcQ",
            "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "provenance": "paste"}])[0]

    def test_fills_columns_and_merges_blob(self):
        item = self._seed_item()
        db.set_workspace_metadata(item["id"], {"health": "ok", "title": "Never Gonna",
                                               "channel": "Rick", "view_count": 10})
        row = next(w for w in db.list_workspace() if w["id"] == item["id"])
        self.assertEqual(row["title"], "Never Gonna")
        self.assertEqual(row["channel"], "Rick")
        self.assertEqual(json.loads(row["metadata_json"])["view_count"], 10)
        # a later dead re-check updates health but COALESCE keeps the known title
        db.set_workspace_metadata(item["id"], {"health": "dead"})
        row = next(w for w in db.list_workspace() if w["id"] == item["id"])
        self.assertEqual(row["title"], "Never Gonna")
        self.assertEqual(json.loads(row["metadata_json"])["health"], "dead")

    def test_unknown_item_raises(self):
        self._seed_item()
        with self.assertRaises(KeyError):
            db.set_workspace_metadata(999999, {"health": "ok"})


class MatchLocalFileByNameTest(DbTestBase):
    def setUp(self):
        super().setUp()
        write_matches(db.MATCHES_CSV, [{"filename": "song.mp3", "yt_id": "a", "check": None}])
        write_matches(db.MATCHES_XLSX, [{"filename": "song.mp3", "yt_id": "a", "check": None}])
        db.init_db()

    def _add_track(self, filename):
        conn = db.connect()
        try:
            with conn:
                conn.execute('INSERT INTO tracks (filename, "check") VALUES (?, NULL)', (filename,))
        finally:
            conn.close()

    def test_links_single_match(self):
        result = db.match_local_file_by_name("folder1", "sub/song.mp3", "song.mp3", 100, "1")
        self.assertTrue(result["matched"])
        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT track_id, available FROM track_file_links WHERE folder_identity='folder1'").fetchone()
        finally:
            conn.close()
        self.assertEqual(row["available"], 1)
        self.assertEqual(row["track_id"], result["track_id"])

    def test_no_database_entry(self):
        result = db.match_local_file_by_name("folder1", "sub/other.mp3", "other.mp3")
        self.assertFalse(result["matched"])
        self.assertIn("no database entry", result["reason"])

    def test_ambiguous_same_basename(self):
        self._add_track("one/dup.mp3")
        self._add_track("two/dup.mp3")
        result = db.match_local_file_by_name("folder1", "sub/dup.mp3", "dup.mp3")
        self.assertFalse(result["matched"])
        self.assertIn("multiple", result["reason"])

    def test_already_linked_rejected(self):
        db.match_local_file_by_name("folder1", "sub/song.mp3", "song.mp3", 100, "1")
        with self.assertRaises(ValueError):
            db.match_local_file_by_name("folder1", "sub/song.mp3", "song.mp3")

    def test_matched_track_no_longer_candidate(self):
        # linking the only track consumes it; a second file finds nothing
        db.match_local_file_by_name("folder1", "sub/song.mp3", "song.mp3", 100, "1")
        result = db.match_local_file_by_name("folder2", "other/song.mp3", "song.mp3")
        self.assertFalse(result["matched"])


class AddLocalFileToLibraryTest(DbTestBase):
    def setUp(self):
        super().setUp()
        write_matches(db.MATCHES_CSV, [{"filename": "exist.mp3", "yt_id": "a", "check": None}])
        write_matches(db.MATCHES_XLSX, [{"filename": "exist.mp3", "yt_id": "a", "check": None}])
        db.init_db()

    def test_creates_new_track_for_unknown_file(self):
        r = db.add_local_file_to_library("f", "sub/new.mp3", "new.mp3", 10, "1")
        self.assertTrue(r["created"])
        self.assertTrue(r["track_id"])

    def test_links_to_existing_track_by_filename(self):
        r = db.add_local_file_to_library("f", "sub/exist.mp3", "exist.mp3", 10, "1")
        self.assertFalse(r["created"])

    def test_already_linked_raises(self):
        db.add_local_file_to_library("f", "sub/new.mp3", "new.mp3")
        with self.assertRaises(ValueError):
            db.add_local_file_to_library("f", "sub/new.mp3", "new.mp3")


class WorkspaceMigrationTest(DbTestBase):
    def test_migration_reruns_without_trigger_errors(self):
        write_matches(db.MATCHES_CSV, [{"filename": "a.mp3", "yt_id": "vid12345678", "check": 1}])
        write_matches(db.MATCHES_XLSX, [{"filename": "a.mp3", "yt_id": "vid12345678", "check": 1}])
        db.init_db()
        tid = db.get_rows(status="all")[0][0]["id"]
        db.promote_library_track(tid)
        conn = db.connect()
        try:
            with conn:
                conn.execute("UPDATE workspace_schema_meta SET version=0")   # force migration path
        finally:
            conn.close()
        db.init_db()   # must not raise: triggers are dropped before the drop/rename
        self.assertEqual(len(db.list_workspace()), 1)
        conn = db.connect()
        try:
            self.assertEqual(conn.execute("SELECT version FROM workspace_schema_meta").fetchone()[0],
                             db._WORKSPACE_SCHEMA_VERSION)
        finally:
            conn.close()


class RemoveTracksTest(DbTestBase):
    def setUp(self):
        super().setUp()
        write_matches(db.MATCHES_CSV, [{"filename": "gone.mp3", "yt_id": "vid12345678", "check": 1}])
        write_matches(db.MATCHES_XLSX, [{"filename": "gone.mp3", "yt_id": "vid12345678", "check": 1}])
        db.init_db()

    def test_removes_track_and_dependents_returns_ytid(self):
        rows, _ = db.get_rows(status="all")
        tid = rows[0]["id"]
        db.add_local_file_to_library("f", "sub/gone.mp3", "gone.mp3", 5, "1")
        removed = db.remove_tracks([tid])
        self.assertEqual(removed, ["vid12345678"])
        conn = db.connect()
        try:
            self.assertIsNone(conn.execute("SELECT id FROM tracks WHERE id=?", (tid,)).fetchone())
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM track_file_links WHERE track_id=?", (tid,)).fetchone()[0], 0)
        finally:
            conn.close()

    def test_unknown_id_noop(self):
        self.assertEqual(db.remove_tracks([999999]), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
