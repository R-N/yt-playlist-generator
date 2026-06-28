"""
SQLite data layer for the review app.

Design rules (data-safety):
  - tracks      : current curated state, one row per local file.
  - decisions   : APPEND-ONLY audit log; every approve/reject is recorded here
                  and never updated or deleted. tracks.check is the rolled-up
                  current value, but decisions is the source of truth you can
                  replay if tracks is ever wrong.
  - A decision write updates both tables inside ONE transaction -> atomic.
  - Export to matches.csv/.xlsx always snapshots the old file into backups/
    first, then writes via a temp file + rename (never a half-written file).
  - The app never opens, writes, or deletes any audio file through this layer.
"""
import os
import json
import shutil
import sqlite3
from datetime import datetime

import pandas as pd

from config import (
    DB_PATH, MATCHES_CSV, MATCHES_XLSX, MATCHES_SOURCE, BACKUP_DIR, TRACK_COLUMNS,
)


def _read_matches(path):
    """Read matches from .xlsx or .csv by extension."""
    if path.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(path)
    return pd.read_csv(path)


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")       # safe concurrent read + write
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create tables if missing. Import matches.csv on first run only."""
    conn = connect()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tracks (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                filename      TEXT UNIQUE NOT NULL,
                artist        TEXT,
                title         TEXT,
                yt_id         TEXT,
                yt_channel    TEXT,
                yt_title      TEXT,
                yt_views      INTEGER,
                duration      REAL,
                audio_format  TEXT,
                audio_bitrate REAL,
                local_bitrate REAL,
                local_better  INTEGER DEFAULT 0,
                score         REAL,
                sim_artist        REAL,
                sim_artist_title  REAL,
                sim_title         REAL,
                sim_filename      REAL,
                "check"       INTEGER,        -- NULL=unreviewed, 1=approve, 0=reject
                extra_json    TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS decisions (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                track_id  INTEGER NOT NULL REFERENCES tracks(id),
                filename  TEXT NOT NULL,
                yt_id     TEXT,
                decision  INTEGER NOT NULL,    -- 1=approve, 0=reject
                ts        TEXT NOT NULL
            )
        """)
        conn.commit()

        empty = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 0
        if empty and os.path.exists(MATCHES_SOURCE):
            _import_source(conn)
    finally:
        conn.close()


def _coerce_check(v):
    if pd.isna(v):
        return None
    if v in (True, 1, "1", "True", "TRUE", "true"):
        return 1
    if v in (False, 0, "0", "False", "FALSE", "false"):
        return 0
    return None


_CORE_COLS = {
    "filename", "artist", "title", "yt_id", "yt_channel", "yt_title",
    "yt_views", "duration", "audio_format", "audio_bitrate",
    "local_bitrate", "local_better", "score", "sim_artist",
    "sim_artist_title", "sim_title", "sim_filename", "check",
}


def reconcile_frames():
    """Merge matches.csv + matches.xlsx into one baseline.

    Lifecycle that makes this necessary:
      searcher.py appends NEW candidate rows to matches.csv (no marks);
      the human edits marks in matches.xlsx. So CSV may hold rows the XLSX
      lacks, and XLSX holds the authoritative `check` marks.

    Rule: keep the union of rows (never drop a CSV-only candidate), and for
    `check` take the XLSX value, falling back to CSV only when XLSX is blank
    (never drop a human decision). Returns (dataframe, info-dict).
    """
    csv = _read_matches(MATCHES_CSV) if os.path.exists(MATCHES_CSV) else None
    xls = _read_matches(MATCHES_XLSX) if os.path.exists(MATCHES_XLSX) else None
    for f in (csv, xls):
        if f is not None:
            f.drop_duplicates(subset="filename", keep="last", inplace=True)

    if xls is None and csv is None:
        return None, {}
    if xls is None:
        return csv.where(pd.notna(csv), None), {"source": "csv-only", "rows": len(csv)}
    if csv is None:
        return xls.where(pd.notna(xls), None), {"source": "xlsx-only", "rows": len(xls)}

    csv_check = {r["filename"]: _coerce_check(r.get("check")) for _, r in csv.iterrows()}
    xls_check = {r["filename"]: _coerce_check(r.get("check")) for _, r in xls.iterrows()}

    xls_names = set(xls["filename"])
    csv_only = csv[~csv["filename"].isin(xls_names)]
    merged = pd.concat([xls, csv_only], ignore_index=True)
    merged = merged.where(pd.notna(merged), None)

    conflicts = 0
    filled = 0

    def resolve(row):
        nonlocal conflicts, filled
        name = row["filename"]
        x, c = xls_check.get(name), csv_check.get(name)
        shared = name in xls_names
        if x is not None and c is not None and x != c:
            conflicts += 1
        if x is not None:
            return x
        if c is not None and shared:   # rescued a blank xlsx row from the csv
            filled += 1
        return c

    merged["check"] = merged.apply(resolve, axis=1)
    info = {
        "csv_rows": len(csv), "xlsx_rows": len(xls),
        "csv_only_rows": int(len(csv_only)), "total": len(merged),
        "marks_filled_from_csv": filled, "mark_conflicts_xlsx_won": conflicts,
        "approved": int((merged["check"] == 1).sum()),
    }
    return merged, info


def _insert_track(conn, d):
    extra = {k: v for k, v in d.items() if k not in _CORE_COLS}
    conn.execute(
        """INSERT OR IGNORE INTO tracks
           (filename, artist, title, yt_id, yt_channel, yt_title, yt_views,
            duration, audio_format, audio_bitrate, local_bitrate,
            local_better, score, sim_artist, sim_artist_title, sim_title,
            sim_filename, "check", extra_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            d.get("filename"), d.get("artist"), d.get("title"),
            d.get("yt_id"), d.get("yt_channel"), d.get("yt_title"),
            d.get("yt_views"), d.get("duration"), d.get("audio_format"),
            d.get("audio_bitrate"), d.get("local_bitrate"),
            1 if d.get("local_better") in (True, 1, "True", "TRUE") else 0,
            d.get("score"), d.get("sim_artist"), d.get("sim_artist_title"),
            d.get("sim_title"), d.get("sim_filename"),
            _coerce_check(d.get("check")),
            json.dumps(extra, default=str) if extra else None,
        ),
    )


def _import_source(conn):
    """Seed tracks from the reconciled csv+xlsx baseline (first run / re-seed)."""
    df, info = reconcile_frames()
    if df is None:
        print("No matches files to import.")
        return
    for _, r in df.iterrows():
        _insert_track(conn, r.to_dict())
    conn.commit()
    print(f"Imported {len(df)} reconciled tracks: {info}")


def get_rows(status="all", limit=200, offset=0):
    """status: all | unreviewed | approved | rejected"""
    where = {
        "unreviewed": 'WHERE "check" IS NULL',
        "approved": 'WHERE "check" = 1',
        "rejected": 'WHERE "check" = 0',
    }.get(status, "")
    conn = connect()
    try:
        rows = conn.execute(
            f'SELECT * FROM tracks {where} ORDER BY artist, title LIMIT ? OFFSET ?',
            (limit, offset),
        ).fetchall()
        total = conn.execute(f"SELECT COUNT(*) FROM tracks {where}").fetchone()[0]
        return [dict(r) for r in rows], total
    finally:
        conn.close()


def counts():
    conn = connect()
    try:
        q = lambda w: conn.execute(f"SELECT COUNT(*) FROM tracks {w}").fetchone()[0]
        return {
            "total": q(""),
            "unreviewed": q('WHERE "check" IS NULL'),
            "approved": q('WHERE "check" = 1'),
            "rejected": q('WHERE "check" = 0'),
        }
    finally:
        conn.close()


def record_decision(track_id, decision):
    """Append to decisions + update tracks.check, atomically. Returns new state."""
    decision = 1 if decision else 0
    conn = connect()
    try:
        track = conn.execute(
            "SELECT id, filename, yt_id FROM tracks WHERE id = ?", (track_id,)
        ).fetchone()
        if track is None:
            raise KeyError(f"No track with id {track_id}")
        with conn:  # transaction: both statements commit together or not at all
            conn.execute(
                "INSERT INTO decisions (track_id, filename, yt_id, decision, ts) "
                "VALUES (?,?,?,?,?)",
                (track["id"], track["filename"], track["yt_id"], decision,
                 datetime.now().isoformat(timespec="seconds")),
            )
            conn.execute(
                'UPDATE tracks SET "check" = ? WHERE id = ?', (decision, track_id)
            )
        return {"id": track_id, "check": decision}
    finally:
        conn.close()


def _tracks_dataframe():
    """Full tracks table rebuilt into the matches column layout."""
    conn = connect()
    try:
        df = pd.read_sql_query("SELECT * FROM tracks", conn)
    finally:
        conn.close()
    if "extra_json" in df.columns:
        extra = pd.json_normalize(
            df["extra_json"].apply(lambda s: json.loads(s) if isinstance(s, str) else {})
        )
        extra.index = df.index
        df = pd.concat([df.drop(columns=["extra_json"]), extra], axis=1)
    for col in TRACK_COLUMNS:
        if col not in df.columns:
            df[col] = None
    return df[TRACK_COLUMNS]


def export_matches(write_xlsx=True):
    """Snapshot existing files, then atomically write matches.csv (+ xlsx).
    Use for the explicit, user-triggered full export."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for path in (MATCHES_CSV, MATCHES_XLSX):
        if os.path.exists(path):
            shutil.copy2(path, os.path.join(BACKUP_DIR, f"{os.path.basename(path)}.{stamp}.bak"))

    out = _tracks_dataframe()
    _atomic_to_csv(out, MATCHES_CSV)
    if write_xlsx:
        _atomic_to_xlsx(out, MATCHES_XLSX)
    return {"rows": len(out), "snapshot": stamp}


def export_csv_only():
    """Lightweight auto-save: atomic write of matches.csv only, no snapshot,
    no xlsx. Called automatically every N decisions so marks flow to the
    git-tracked CSV continuously. The atomic write can't corrupt the file,
    and git history is the rollback path. Returns row count."""
    out = _tracks_dataframe()
    _atomic_to_csv(out, MATCHES_CSV)
    return {"rows": len(out)}


def _atomic_to_csv(df, path):
    tmp = path + ".tmp"
    df.to_csv(tmp, index=False)
    os.replace(tmp, path)


def _atomic_to_xlsx(df, path):
    tmp = path + ".tmp.xlsx"
    df.to_excel(tmp, index=False)
    os.replace(tmp, path)
