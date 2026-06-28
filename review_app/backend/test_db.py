"""
Tests for the review-app data layer (stdlib unittest, no extra deps).

Run from review_app/backend:
    python -m unittest test_db -v

Every test redirects db's module globals to a fresh temp dir, so the real
matches.csv / matches.xlsx are never read or written.
"""
import os
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
