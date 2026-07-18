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
import math
import re
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
        _init_workspace_schema(conn)
        # Health of the track's YouTube link (ok/dead/private/unknown), set by the
        # Library "Verify links" action. Added by migration for existing DBs.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tracks)")}
        if "yt_health" not in cols:
            conn.execute("ALTER TABLE tracks ADD COLUMN yt_health TEXT")
        conn.execute("""CREATE TABLE IF NOT EXISTS deletion_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id INTEGER,
            folder_identity TEXT,
            relative_path TEXT,
            outcome TEXT NOT NULL,
            detail TEXT,
            ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        # Background task log (verify sweeps etc.). Runtime state, not exported.
        conn.execute("""CREATE TABLE IF NOT EXISTS background_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL,          -- running | done | error | cancelled | interrupted
            total INTEGER NOT NULL DEFAULT 0,
            done INTEGER NOT NULL DEFAULT 0,
            found INTEGER NOT NULL DEFAULT 0,
            message TEXT,
            started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            finished_at TEXT
        )""")
        conn.commit()

        conn.execute(
            "UPDATE workspace_runs SET status='interrupted', "
            "error_text=COALESCE(error_text, 'app restarted before run completed') "
            "WHERE status IN ('queued','running','finalizing')"
        )
        # A running task's worker thread died with the old process — mark it so.
        conn.execute(
            "UPDATE background_tasks SET status='interrupted', "
            "message=COALESCE(message, 'app restarted before task completed'), "
            "finished_at=CURRENT_TIMESTAMP WHERE status IN ('running','cancelling')"
        )
        conn.commit()
        empty = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 0
        if empty and os.path.exists(MATCHES_SOURCE):
            _import_source(conn)
    finally:
        conn.close()


_WORKSPACE_SCHEMA_VERSION = 5
_YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _workspace_tables_exist(conn):
    names = {"workspace_items", "saved_links", "track_file_links",
             "workspace_runs", "workspace_run_items"}
    found = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    )}
    return bool(names & found)


def _canonical_workspace_row(row, table, index):
    d = dict(row)
    youtube_id = d.get("youtube_id")
    youtube_url = d.get("youtube_url")
    # A workspace item with a library track or a direct file ref legitimately has no YouTube.
    if table == "workspace_items" and youtube_id is None and (
            d.get("track_id") is not None or (d.get("folder_identity") and d.get("relative_path"))):
        return d
    if not isinstance(youtube_id, str) or not _YOUTUBE_ID_RE.fullmatch(youtube_id):
        raise RuntimeError(
            f"Workspace migration failed: {table} row {index} has non-canonical YouTube ID"
        )
    expected = "https://www.youtube.com/watch?v=" + youtube_id
    if youtube_url != expected:
        raise RuntimeError(
            f"Workspace migration failed: {table} row {index} has non-canonical YouTube URL"
        )
    return d


def _read_workspace_rows(conn, table):
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone():
        return []
    return [dict(row) for row in conn.execute(f'SELECT * FROM "{table}"')]


def _validate_workspace_data(conn, data):
    items = [_canonical_workspace_row(r, "workspace_items", i)
             for i, r in enumerate(data["workspace_items"])]
    links = [_canonical_workspace_row(r, "saved_links", i)
             for i, r in enumerate(data["saved_links"])]
    snapshots = [_canonical_workspace_row(r, "workspace_run_items", i)
                 for i, r in enumerate(data["workspace_run_items"])]
    positions = [r.get("position") for r in items]
    if any(not isinstance(p, int) or p < 0 for p in positions) or len(set(positions)) != len(items):
        raise RuntimeError("Workspace migration failed: workspace item positions conflict")
    if len({r.get("youtube_id") for r in items if r.get("track_id") is None}) != \
            len([r for r in items if r.get("track_id") is None]):
        raise RuntimeError("Workspace migration failed: generic YouTube IDs conflict")
    library_tracks = [r.get("track_id") for r in items if r.get("track_id") is not None]
    if len(set(library_tracks)) != len(library_tracks):
        raise RuntimeError("Workspace migration failed: library track IDs conflict")
    if len({r.get("youtube_id") for r in links}) != len(links):
        raise RuntimeError("Workspace migration failed: saved-link YouTube IDs conflict")
    if any(not isinstance(r.get("position"), int) or r.get("position") < 0
           for r in snapshots):
        raise RuntimeError("Workspace migration failed: run snapshot positions conflict")
    run_positions = {(r.get("run_id"), r.get("position")) for r in snapshots}
    run_ids = {(r.get("run_id"), r.get("youtube_id")) for r in snapshots}
    if len(run_positions) != len(snapshots) or len(run_ids) != len(snapshots):
        raise RuntimeError("Workspace migration failed: run snapshot ordering/IDs conflict")
    file_keys = [(r.get("folder_identity"), r.get("relative_path"))
                 for r in data["track_file_links"]]
    if len(set(file_keys)) != len(file_keys):
        raise RuntimeError("Workspace migration failed: file-link identities conflict")
    valid_statuses = {"queued", "running", "done", "failed", "stopped", "interrupted"}
    for row in data["workspace_runs"]:
        if row.get("status") not in valid_statuses:
            raise RuntimeError("Workspace migration failed: invalid workspace run status")


def _create_workspace_tables(conn, suffix=""):
    def t(name):
        return name + suffix
    # A Workspace item is its own entity: it may carry a YouTube id, a library
    # track_id (nullable FK), OR a direct local-file reference (folder+path) — so
    # a file can stage with no Library entry. At least one identity is required.
    conn.execute(f'''CREATE TABLE "{t("workspace_items")}" (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        youtube_id TEXT CHECK (youtube_id IS NULL OR (length(youtube_id)=11 AND youtube_id NOT GLOB '*[^A-Za-z0-9_-]*')),
        youtube_url TEXT CHECK (youtube_url IS NULL OR youtube_url='https://www.youtube.com/watch?v=' || youtube_id),
        title TEXT, channel TEXT, position INTEGER NOT NULL CHECK (position >= 0),
        provenance TEXT NOT NULL CHECK (length(trim(provenance)) > 0),
        track_id INTEGER REFERENCES tracks(id), metadata_json TEXT,
        folder_identity TEXT, relative_path TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK (youtube_id IS NOT NULL OR track_id IS NOT NULL
               OR (folder_identity IS NOT NULL AND relative_path IS NOT NULL)))''')
    conn.execute(f'''CREATE TABLE "{t("saved_links")}" (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        youtube_id TEXT NOT NULL CHECK (length(youtube_id)=11 AND youtube_id NOT GLOB '*[^A-Za-z0-9_-]*'),
        youtube_url TEXT NOT NULL CHECK (youtube_url='https://www.youtube.com/watch?v=' || youtube_id),
        track_id INTEGER REFERENCES tracks(id), metadata_json TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(youtube_id))''')
    conn.execute(f'''CREATE TABLE "{t("track_file_links")}" (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
        folder_identity TEXT NOT NULL CHECK (length(trim(folder_identity)) > 0),
        relative_path TEXT NOT NULL CHECK (length(trim(relative_path)) > 0),
        available INTEGER NOT NULL DEFAULT 1 CHECK (available IN (0,1)),
        file_size INTEGER, modified_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(folder_identity, relative_path),
        UNIQUE(track_id, folder_identity, relative_path))''')
    conn.execute(f'''CREATE TABLE "{t("workspace_runs")}" (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        operation TEXT NOT NULL CHECK (length(trim(operation)) > 0),
        input_path TEXT,
        input_reference TEXT,
        status TEXT NOT NULL DEFAULT 'queued' CHECK
          (status IN ('queued','running','done','failed','stopped','interrupted')),
        started_at TEXT, finished_at TEXT, error_text TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
    conn.execute(f'''CREATE TABLE "{t("workspace_run_items")}" (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL REFERENCES {t("workspace_runs")}(id),
        position INTEGER NOT NULL CHECK (position >= 0),
        youtube_id TEXT NOT NULL CHECK (length(youtube_id)=11 AND youtube_id NOT GLOB '*[^A-Za-z0-9_-]*'),
        youtube_url TEXT NOT NULL CHECK (youtube_url='https://www.youtube.com/watch?v=' || youtube_id),
        title TEXT, channel TEXT, provenance TEXT,
        track_id INTEGER REFERENCES tracks(id), metadata_json TEXT,
        snapshot_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(run_id, position))''')


def _install_workspace_constraints(conn):
    for sql in (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_workspace_generic_youtube ON workspace_items(youtube_id) WHERE track_id IS NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_workspace_library_track ON workspace_items(track_id) WHERE track_id IS NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_workspace_file ON workspace_items(folder_identity, relative_path) WHERE folder_identity IS NOT NULL AND track_id IS NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_workspace_position ON workspace_items(position)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_run_item_youtube ON workspace_run_items(run_id, youtube_id)",
        "CREATE INDEX IF NOT EXISTS idx_workspace_items_track ON workspace_items(track_id)",
        "CREATE INDEX IF NOT EXISTS idx_saved_links_track ON saved_links(track_id)",
        "CREATE INDEX IF NOT EXISTS idx_track_file_links_track ON track_file_links(track_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_track_file_link_identity ON track_file_links(track_id, folder_identity, relative_path)",
        "CREATE INDEX IF NOT EXISTS idx_workspace_run_items_run ON workspace_run_items(run_id)",
    ):
        conn.execute(sql)
    for sql in (
        """CREATE TRIGGER IF NOT EXISTS validate_workspace_item_youtube
        BEFORE INSERT ON workspace_items WHEN length(NEW.youtube_id) != 11
          OR NEW.youtube_id GLOB '*[^A-Za-z0-9_-]*'
          OR NEW.youtube_url != 'https://www.youtube.com/watch?v=' || NEW.youtube_id
        BEGIN SELECT RAISE(ABORT, 'workspace item requires canonical YouTube id and URL'); END""",
        """CREATE TRIGGER IF NOT EXISTS validate_workspace_item_youtube_update
        BEFORE UPDATE OF youtube_id, youtube_url ON workspace_items
        WHEN length(NEW.youtube_id) != 11 OR NEW.youtube_id GLOB '*[^A-Za-z0-9_-]*'
          OR NEW.youtube_url != 'https://www.youtube.com/watch?v=' || NEW.youtube_id
        BEGIN SELECT RAISE(ABORT, 'workspace item requires canonical YouTube id and URL'); END""",
        """CREATE TRIGGER IF NOT EXISTS validate_saved_link_youtube
        BEFORE INSERT ON saved_links WHEN length(NEW.youtube_id) != 11
          OR NEW.youtube_id GLOB '*[^A-Za-z0-9_-]*'
          OR NEW.youtube_url != 'https://www.youtube.com/watch?v=' || NEW.youtube_id
        BEGIN SELECT RAISE(ABORT, 'saved link requires canonical YouTube id and URL'); END""",
        """CREATE TRIGGER IF NOT EXISTS validate_saved_link_youtube_update
        BEFORE UPDATE OF youtube_id, youtube_url ON saved_links
        WHEN length(NEW.youtube_id) != 11 OR NEW.youtube_id GLOB '*[^A-Za-z0-9_-]*'
          OR NEW.youtube_url != 'https://www.youtube.com/watch?v=' || NEW.youtube_id
        BEGIN SELECT RAISE(ABORT, 'saved link requires canonical YouTube id and URL'); END""",
        """CREATE TRIGGER IF NOT EXISTS validate_run_item_youtube
        BEFORE INSERT ON workspace_run_items WHEN length(NEW.youtube_id) != 11
          OR NEW.youtube_id GLOB '*[^A-Za-z0-9_-]*'
          OR NEW.youtube_url != 'https://www.youtube.com/watch?v=' || NEW.youtube_id
        BEGIN SELECT RAISE(ABORT, 'run item requires canonical YouTube id and URL'); END""",
        """CREATE TRIGGER IF NOT EXISTS validate_run_item_youtube_update
        BEFORE UPDATE OF youtube_id, youtube_url ON workspace_run_items
        WHEN length(NEW.youtube_id) != 11 OR NEW.youtube_id GLOB '*[^A-Za-z0-9_-]*'
          OR NEW.youtube_url != 'https://www.youtube.com/watch?v=' || NEW.youtube_id
        BEGIN SELECT RAISE(ABORT, 'run item requires canonical YouTube id and URL'); END""",
        """CREATE TRIGGER IF NOT EXISTS protect_started_run_item_update
        BEFORE UPDATE ON workspace_run_items WHEN EXISTS (
            SELECT 1 FROM workspace_runs r WHERE r.id = OLD.run_id AND r.status != 'queued')
        BEGIN SELECT RAISE(ABORT, 'started run snapshots are immutable'); END""",
        """CREATE TRIGGER IF NOT EXISTS protect_started_run_item_delete
        BEFORE DELETE ON workspace_run_items WHEN EXISTS (
            SELECT 1 FROM workspace_runs r WHERE r.id = OLD.run_id AND r.status != 'queued')
        BEGIN SELECT RAISE(ABORT, 'started run snapshots are immutable'); END""",
        """CREATE TRIGGER IF NOT EXISTS protect_run_item_insert
        BEFORE INSERT ON workspace_run_items WHEN NOT EXISTS (
            SELECT 1 FROM workspace_runs r WHERE r.id = NEW.run_id AND r.status = 'queued')
        BEGIN SELECT RAISE(ABORT, 'run snapshots accept inserts only while queued'); END""",
        """CREATE TRIGGER IF NOT EXISTS protect_run_status_rollback
        BEFORE UPDATE OF status ON workspace_runs
        WHEN OLD.status != 'queued' AND NEW.status = 'queued'
        BEGIN SELECT RAISE(ABORT, 'started runs cannot return to queued'); END""",
        """CREATE TRIGGER IF NOT EXISTS protect_snapshot_track_delete
        BEFORE DELETE ON tracks WHEN EXISTS (
            SELECT 1 FROM workspace_run_items i WHERE i.track_id = OLD.id)
        BEGIN SELECT RAISE(ABORT, 'snapshot source track cannot be deleted'); END""",
    ):
        conn.execute(sql)


_WORKSPACE_TRIGGERS = (
    "validate_workspace_item_youtube", "validate_workspace_item_youtube_update",
    "validate_saved_link_youtube", "validate_saved_link_youtube_update",
    "validate_run_item_youtube", "validate_run_item_youtube_update",
    "protect_started_run_item_update", "protect_started_run_item_delete",
    "protect_run_item_insert", "protect_run_status_rollback",
    "protect_snapshot_track_delete",
)


def _migrate_workspace_schema(conn):
    data = {name: _read_workspace_rows(conn, name) for name in (
        "workspace_items", "saved_links", "track_file_links",
        "workspace_runs", "workspace_run_items")}
    _validate_workspace_data(conn, data)
    # Drop triggers first: some reference workspace_run_items, which would break
    # the drop/rename dance below (a trigger on the surviving tracks table can't
    # reference a table that's momentarily gone). Recreated by _install_* after.
    for trigger in _WORKSPACE_TRIGGERS:
        conn.execute(f'DROP TRIGGER IF EXISTS "{trigger}"')
    temp_suffix = "__v2"
    for name in data:
        conn.execute(f'DROP TABLE IF EXISTS "{name}{temp_suffix}"')
    _create_workspace_tables(conn, temp_suffix)
    # The helper creates all five tables in one pass; copy before replacing names.
    columns = {
        "workspace_items": ("id", "youtube_id", "youtube_url", "title", "channel", "position", "provenance", "track_id", "metadata_json", "folder_identity", "relative_path", "created_at"),
        "saved_links": ("id", "youtube_id", "youtube_url", "track_id", "metadata_json", "created_at"),
        "track_file_links": ("id", "track_id", "folder_identity", "relative_path", "available", "file_size", "modified_at", "created_at"),
        "workspace_runs": ("id", "operation", "input_path", "input_reference", "status", "started_at", "finished_at", "error_text", "created_at"),
        "workspace_run_items": ("id", "run_id", "position", "youtube_id", "youtube_url", "title", "channel", "provenance", "track_id", "metadata_json", "snapshot_at"),
    }
    for name, rows in data.items():
        if not rows:
            continue
        cols = [c for c in columns[name] if c in rows[0]]
        marks = ",".join("?" for _ in cols)
        for row in rows:
            conn.execute(f'INSERT INTO "{name}{temp_suffix}" ({",".join(cols)}) VALUES ({marks})',
                         tuple(row.get(c) for c in cols))
    for name in ("workspace_run_items", "workspace_items", "saved_links", "track_file_links", "workspace_runs"):
        conn.execute(f'DROP TABLE IF EXISTS "{name}"')
    for name in data:
        conn.execute(f'ALTER TABLE "{name}{temp_suffix}" RENAME TO "{name}"')
    _install_workspace_constraints(conn)


def _init_workspace_schema(conn):
    """Versioned transactional migration for Workspace-only tables."""
    conn.execute("SAVEPOINT workspace_schema_migration")
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS workspace_schema_meta (id INTEGER PRIMARY KEY CHECK (id=1), version INTEGER NOT NULL)")
        row = conn.execute("SELECT version FROM workspace_schema_meta WHERE id=1").fetchone()
        version = row[0] if row else 0
        if version < _WORKSPACE_SCHEMA_VERSION and _workspace_tables_exist(conn):
            _migrate_workspace_schema(conn)
        elif not _workspace_tables_exist(conn):
            _create_workspace_tables(conn)
            _install_workspace_constraints(conn)
        else:
            _install_workspace_constraints(conn)
        conn.execute("INSERT INTO workspace_schema_meta(id, version) VALUES (1, ?) "
                     "ON CONFLICT(id) DO UPDATE SET version=excluded.version",
                     (_WORKSPACE_SCHEMA_VERSION,))
        conn.execute("RELEASE SAVEPOINT workspace_schema_migration")
    except Exception as e:
        conn.execute("ROLLBACK TO SAVEPOINT workspace_schema_migration")
        conn.execute("RELEASE SAVEPOINT workspace_schema_migration")
        if isinstance(e, RuntimeError):
            raise
        raise RuntimeError(f"Workspace schema migration failed: {e}") from e


def _coerce_check(v):
    if pd.isna(v):
        return None
    if v in (True, 1, "1", "True", "TRUE", "true"):
        return 1
    if v in (False, 0, "0", "False", "FALSE", "false"):
        return 0
    return None


def _load_json(raw):
    """Parse a JSON object blob (or pass through a dict) to a dict; {} on anything else."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _link_file(conn, track_id, folder_identity, relative_path, file_size=None, modified_at=None):
    """Upsert an available track_file_link: insert, or re-activate and refresh
    size/mtime when the (track, folder, path) identity already exists."""
    conn.execute(
        "INSERT INTO track_file_links (track_id, folder_identity, relative_path, available, file_size, modified_at) "
        "VALUES (?,?,?,1,?,?) ON CONFLICT(track_id, folder_identity, relative_path) DO UPDATE SET "
        "available=1, file_size=excluded.file_size, modified_at=excluded.modified_at",
        (track_id, folder_identity, relative_path, file_size, modified_at))


def list_workspace():
    conn = connect()
    try:
        return [dict(row) for row in conn.execute(
            "SELECT w.*, t.artist AS track_artist, t.title AS track_title, "
            "t.filename AS track_filename, t.\"check\" AS track_check, "
            "(SELECT COUNT(*) FROM track_file_links l WHERE l.track_id = w.track_id "
            "AND l.available = 1) AS local_count "
            "FROM workspace_items w "
            "LEFT JOIN tracks t ON t.id = w.track_id ORDER BY w.position, w.id"
        )]
    finally:
        conn.close()


def list_saved_links():
    conn = connect()
    try:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM saved_links ORDER BY id"
        )]
    finally:
        conn.close()


def set_workspace_metadata(item_id, meta):
    """Store fetched YouTube metadata: fill the title/channel columns and merge the
    full blob (health, view_count, duration, ...) into metadata_json. Unknown title/
    channel (e.g. a dead video) leave existing column values intact."""
    conn = connect()
    try:
        with conn:
            row = conn.execute(
                "SELECT metadata_json FROM workspace_items WHERE id = ?", (item_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"No Workspace item id {item_id}")
            existing = _load_json(row["metadata_json"])
            existing.update(meta)
            conn.execute(
                "UPDATE workspace_items SET title = COALESCE(?, title), "
                "channel = COALESCE(?, channel), metadata_json = ? WHERE id = ?",
                (meta.get("title"), meta.get("channel"),
                 json.dumps(existing, default=str), item_id),
            )
    finally:
        conn.close()


def sync_catalog_links(records):
    """Refresh exact local links from current validated catalog.

    Legacy filename is usable only when basename resolves uniquely.
    """
    by_name = {}
    current = {(r["folder_identity"], r["relative_path"]): r for r in records}
    for record in records:
        by_name.setdefault(os.path.normcase(record["basename"]), []).append(record)
    conn = connect()
    try:
        with conn:
            conn.execute(
                "CREATE TEMP TABLE catalog_links (folder_identity TEXT NOT NULL, "
                "relative_path TEXT NOT NULL, PRIMARY KEY(folder_identity, relative_path))"
            )
            conn.executemany(
                "INSERT INTO catalog_links (folder_identity, relative_path) VALUES (?,?)",
                current,
            )
            conn.execute(
                "UPDATE track_file_links SET available=0 WHERE NOT EXISTS ("
                "SELECT 1 FROM catalog_links c WHERE c.folder_identity=track_file_links.folder_identity "
                "AND c.relative_path=track_file_links.relative_path)"
            )
            for (folder, relative), record in current.items():
                conn.execute(
                    "UPDATE track_file_links SET available=1, file_size=?, modified_at=? "
                    "WHERE folder_identity=? AND relative_path=?",
                    (record.get("file_size"), record.get("modified_at"), folder, relative),
                )
            claimed = {row[0] for row in conn.execute(
                "SELECT DISTINCT track_id FROM track_file_links WHERE available=1")}
            for track in conn.execute("SELECT id, filename FROM tracks"):
                candidates = by_name.get(os.path.normcase(track["filename"] or ""), [])
                if len(candidates) != 1 or track["id"] in claimed:
                    continue
                record = candidates[0]
                owner = conn.execute(
                    "SELECT track_id FROM track_file_links WHERE folder_identity=? AND relative_path=? LIMIT 1",
                    (record["folder_identity"], record["relative_path"]),
                ).fetchone()
                if owner is None:
                    conn.execute(
                        "INSERT INTO track_file_links "
                        "(track_id, folder_identity, relative_path, available, file_size, modified_at) "
                        "VALUES (?,?,?,?,?,?)",
                        (track["id"], record["folder_identity"], record["relative_path"],
                         1, record.get("file_size"), record.get("modified_at")),
                    )
                    claimed.add(track["id"])
    finally:
        conn.close()


def local_file_classifications(records):
    by_identity = {(r["folder_identity"], r["relative_path"]): r for r in records}
    names = {}
    for record in records:
        names.setdefault(os.path.normcase(record["basename"]), 0)
        names[os.path.normcase(record["basename"])] += 1
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT l.folder_identity, l.relative_path, l.track_id, t.filename, t.yt_id, t.artist, "
            "t.title, t.\"check\" FROM track_file_links l JOIN tracks t ON t.id=l.track_id "
            "WHERE l.available=1"
        ).fetchall()
    finally:
        conn.close()
    links = {}
    for row in rows:
        links.setdefault((row["folder_identity"], row["relative_path"]), []).append(dict(row))
    result = []
    for key, record in by_identity.items():
        linked = links.get(key, [])
        if linked:
            checks = {row["check"] for row in linked}
            category = ("verified" if checks == {1} else "rejected" if checks == {0}
                        else "unreviewed" if checks == {None} else "ambiguous")
        else:
            category = "ambiguous" if names[os.path.normcase(record["basename"])] > 1 else "unmatched"
        result.append({k: record.get(k) for k in
                       ("folder_identity", "relative_path", "basename", "file_size", "modified_at", "media_type")}
                      | {"category": category, "tracks": linked})
    return result


def local_deletion_links(track_ids):
    conn = connect()
    try:
        return [dict(row) for row in conn.execute(
            "SELECT l.track_id, l.folder_identity, l.relative_path, l.file_size, l.modified_at, "
            "t.\"check\" FROM track_file_links l JOIN tracks t ON t.id=l.track_id "
            "WHERE l.available=1 AND l.track_id IN (%s)" % ",".join("?" for _ in track_ids),
            tuple(track_ids),
        )] if track_ids else []
    finally:
        conn.close()


def finish_local_deletions(entries):
    conn = connect()
    try:
        with conn:
            for entry in entries:
                conn.execute(
                    "INSERT INTO deletion_audit (track_id, folder_identity, relative_path, outcome, detail) "
                    "VALUES (?,?,?,?,?)",
                    (entry.get("track_id"), entry.get("folder_identity"), entry.get("relative_path"),
                     entry["outcome"], entry.get("detail")),
                )
                if entry["outcome"] == "deleted":
                    conn.execute(
                        "UPDATE track_file_links SET available=0 WHERE track_id=? AND folder_identity=? "
                        "AND relative_path=?",
                        (entry["track_id"], entry["folder_identity"], entry["relative_path"]),
                    )
    finally:
        conn.close()


def list_deletion_audit():
    conn = connect()
    try:
        return [dict(row) for row in conn.execute("SELECT * FROM deletion_audit ORDER BY id")]
    finally:
        conn.close()


def match_saved_link(saved_link_id, track_id, folder_identity, relative_path,
                     file_size=None, modified_at=None):
    conn = connect()
    try:
        with conn:
            saved = conn.execute("SELECT * FROM saved_links WHERE id=?", (saved_link_id,)).fetchone()
            if saved is None:
                raise KeyError("saved link not found")
            if saved["track_id"] not in (None, track_id):
                raise ValueError("saved link already belongs to another track")
            track = conn.execute("SELECT id, yt_id, \"check\" FROM tracks WHERE id=?", (track_id,)).fetchone()
            if track is None:
                raise KeyError("track not found")
            if track["check"] is not None:
                raise ValueError("decided track cannot receive a saved-link candidate")
            if (track["yt_id"] is not None and
                    _YOUTUBE_ID_RE.fullmatch(track["yt_id"] or "") and
                    track["yt_id"] != saved["youtube_id"]):
                raise ValueError("track already has a conflicting candidate")
            other = conn.execute(
                "SELECT track_id FROM track_file_links WHERE folder_identity=? AND relative_path=? "
                "AND track_id != ? LIMIT 1",
                (folder_identity, relative_path, track_id),
            ).fetchone()
            if other is not None:
                raise ValueError("local file already belongs to another track")
            _link_file(conn, track_id, folder_identity, relative_path, file_size, modified_at)
            conn.execute("UPDATE saved_links SET track_id=? WHERE id=?", (track_id, saved_link_id))
            metadata = _load_json(saved["metadata_json"])
            conn.execute(
                "UPDATE tracks SET yt_id=?, yt_title=COALESCE(?, yt_title), "
                "yt_channel=COALESCE(?, yt_channel) WHERE id=?",
                (saved["youtube_id"], metadata.get("title"), metadata.get("channel"), track_id),
            )
            return dict(conn.execute("SELECT * FROM saved_links WHERE id=?", (saved_link_id,)).fetchone())
    finally:
        conn.close()


def match_local_file_by_name(folder_identity, relative_path, basename,
                             file_size=None, modified_at=None):
    """Link an untracked local file to the one existing track whose stored
    filename shares the same basename. Returns {'matched': bool, ...}. Only
    links when exactly one unlinked candidate matches (no guessing on ties)."""
    conn = connect()
    try:
        with conn:
            existing = conn.execute(
                "SELECT track_id FROM track_file_links WHERE folder_identity=? AND relative_path=? "
                "AND available=1 LIMIT 1", (folder_identity, relative_path)).fetchone()
            if existing is not None:
                raise ValueError("file already linked to a track")
            target = os.path.normcase(basename)
            rows = conn.execute(
                "SELECT id, filename FROM tracks WHERE filename IS NOT NULL AND id NOT IN "
                "(SELECT track_id FROM track_file_links WHERE available=1)").fetchall()
            candidates = [row["id"] for row in rows
                          if os.path.normcase(os.path.basename(row["filename"])) == target]
            if not candidates:
                return {"matched": False, "reason": "no database entry with this filename"}
            if len(candidates) > 1:
                return {"matched": False, "reason": "multiple database entries share this filename"}
            track_id = candidates[0]
            _link_file(conn, track_id, folder_identity, relative_path, file_size, modified_at)
            return {"matched": True, "track_id": track_id}
    finally:
        conn.close()


def reset_track_review(track_ids):
    """Clear the review decision (check -> NULL) so a track returns to unreviewed.
    Decision history rows are kept; only the current mark is cleared."""
    conn = connect()
    try:
        with conn:
            conn.executemany('UPDATE tracks SET "check"=NULL WHERE id=?', ((i,) for i in track_ids))
    finally:
        conn.close()


def set_track_health(track_id, health):
    conn = connect()
    try:
        with conn:
            conn.execute("UPDATE tracks SET yt_health=? WHERE id=?", (health, track_id))
    finally:
        conn.close()


def remove_tracks(track_ids):
    """Delete Library track rows and their dependents (links, decisions; null out
    saved-link/workspace refs). Returns the removed tracks' youtube ids so the
    caller can also drop their downloaded files. Does NOT touch mp3-folder files."""
    conn = connect()
    try:
        with conn:
            removed = []
            for track_id in track_ids:
                row = conn.execute("SELECT yt_id FROM tracks WHERE id=?", (track_id,)).fetchone()
                if row is None:
                    continue
                conn.execute("DELETE FROM decisions WHERE track_id=?", (track_id,))
                conn.execute("DELETE FROM track_file_links WHERE track_id=?", (track_id,))
                conn.execute("UPDATE saved_links SET track_id=NULL WHERE track_id=?", (track_id,))
                conn.execute("UPDATE workspace_items SET track_id=NULL WHERE track_id=?", (track_id,))
                conn.execute("DELETE FROM tracks WHERE id=?", (track_id,))
                removed.append(row["yt_id"])
            return removed
    finally:
        conn.close()


def add_local_file_to_library(folder_identity, relative_path, basename,
                              file_size=None, modified_at=None):
    """Make an untracked local file a Library entry: find-or-create a track by
    filename, then link the file. Returns {'track_id', 'created'}."""
    conn = connect()
    try:
        with conn:
            existing = conn.execute(
                "SELECT track_id FROM track_file_links WHERE folder_identity=? AND relative_path=? "
                "AND available=1 LIMIT 1", (folder_identity, relative_path)).fetchone()
            if existing is not None:
                raise ValueError("file already linked to a track")
            track = conn.execute("SELECT id FROM tracks WHERE filename=?", (basename,)).fetchone()
            if track is not None:
                track_id, created = track["id"], False
            else:
                cur = conn.execute('INSERT INTO tracks (filename, "check") VALUES (?, NULL)', (basename,))
                track_id, created = cur.lastrowid, True
            _link_file(conn, track_id, folder_identity, relative_path, file_size, modified_at)
            return {"track_id": track_id, "created": created}
    finally:
        conn.close()


def workspace_selection(ids):
    """Return ordered immutable selection and de-duplicated output view."""
    if not isinstance(ids, list) or not ids:
        raise ValueError("selection requires at least one Workspace item ID")
    if any(isinstance(item_id, bool) or not isinstance(item_id, int) or item_id <= 0
           for item_id in ids):
        raise ValueError("Workspace item IDs must be positive integers")
    if len(ids) != len(set(ids)):
        raise ValueError("selection contains duplicate Workspace item IDs")
    conn = connect()
    try:
        conn.execute("BEGIN")
        rows = []
        # SQLite commonly caps bound parameters at 999; keep headroom for
        # drivers/builds with a lower limit while preserving caller order later.
        for start in range(0, len(ids), 900):
            chunk = ids[start:start + 900]
            rows.extend(conn.execute(
                "SELECT * FROM workspace_items WHERE id IN (%s)" %
                ",".join("?" for _ in chunk), chunk
            ).fetchall())
    finally:
        conn.rollback()
        conn.close()
    by_id = {row["id"]: dict(row) for row in rows}
    missing = [item_id for item_id in ids if item_id not in by_id]
    if missing:
        raise KeyError(f"Unknown Workspace item id(s): {missing}")
    items = tuple(dict(by_id[item_id]) for item_id in ids)
    seen = set()
    unique_items = []
    skipped = []
    skipped_no_link = []
    for item in items:
        if item["youtube_id"] is None:   # link-less local item: no URL for YouTube ops
            skipped_no_link.append(item["id"])
        elif item["youtube_id"] in seen:
            skipped.append(item["id"])
        else:
            seen.add(item["youtube_id"])
            unique_items.append(item)
    return {
        "items": items,
        "unique_items": tuple(unique_items),
        "skipped_duplicate_item_ids": tuple(skipped),
        "skipped_no_link_item_ids": tuple(skipped_no_link),
    }


def create_workspace_run(operation, input_path, input_reference, items):
    conn = connect()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO workspace_runs "
                "(operation, input_path, input_reference, status) VALUES (?,?,?,'queued')",
                (operation, input_path, input_reference),
            )
            run_id = cur.lastrowid
            for position, item in enumerate(items):
                conn.execute(
                    "INSERT INTO workspace_run_items "
                    "(run_id, position, youtube_id, youtube_url, title, channel, "
                    "provenance, track_id, metadata_json) VALUES (?,?,?,?,?,?,?,?,?)",
                    (run_id, position, item["youtube_id"], item["youtube_url"],
                     item.get("title"), item.get("channel"), item.get("provenance"),
                     item.get("track_id"), item.get("metadata_json")),
                )
            return run_id
    finally:
        conn.close()


def update_workspace_run(run_id, status, error_text=None):
    conn = connect()
    try:
        with conn:
            if status in ("done", "failed", "stopped", "interrupted"):
                conn.execute(
                    "UPDATE workspace_runs SET status=?, finished_at=CURRENT_TIMESTAMP, "
                    "error_text=? WHERE id=?", (status, error_text, run_id)
                )
            elif status == "running":
                conn.execute(
                    "UPDATE workspace_runs SET status=?, started_at=CURRENT_TIMESTAMP, "
                    "error_text=? WHERE id=?", (status, error_text, run_id)
                )
            else:
                conn.execute("UPDATE workspace_runs SET status=?, error_text=? WHERE id=?",
                             (status, error_text, run_id))
    finally:
        conn.close()


def list_workspace_runs():
    conn = connect()
    try:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM workspace_runs ORDER BY id DESC"
        )]
    finally:
        conn.close()


# ── background task log (verify sweeps) ─────────────────────────────────────
def create_task(kind, title, total):
    conn = connect()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO background_tasks (kind, title, status, total) "
                "VALUES (?,?, 'running', ?)", (kind, title, total))
            return cur.lastrowid
    finally:
        conn.close()


def bump_task(task_id, done=0, found=0):
    conn = connect()
    try:
        with conn:
            conn.execute("UPDATE background_tasks SET done=done+?, found=found+? "
                         "WHERE id=?", (done, found, task_id))
    finally:
        conn.close()


def finish_task(task_id, status, message=""):
    conn = connect()
    try:
        with conn:
            conn.execute("UPDATE background_tasks SET status=?, message=?, "
                         "finished_at=CURRENT_TIMESTAMP WHERE id=?",
                         (status, message, task_id))
    finally:
        conn.close()


def get_task(task_id):
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM background_tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_tasks(limit=100):
    """Task log: running first, then most-recent finished."""
    conn = connect()
    try:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM background_tasks "
            "ORDER BY (status='running') DESC, id DESC LIMIT ?", (limit,))]
    finally:
        conn.close()


def list_decisions(limit=200):
    """The append-only approve/reject log, newest first, with each track's title."""
    conn = connect()
    try:
        return [dict(row) for row in conn.execute(
            "SELECT d.id, d.track_id, d.filename, d.yt_id, d.decision, d.ts, "
            "       t.title, t.artist, t.yt_title "
            "FROM decisions d LEFT JOIN tracks t ON t.id = d.track_id "
            "ORDER BY d.id DESC LIMIT ?", (limit,))]
    finally:
        conn.close()


def get_workspace_run(run_id):
    conn = connect()
    try:
        run = conn.execute("SELECT * FROM workspace_runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            raise KeyError(f"No workspace run with id {run_id}")
        items = conn.execute(
            "SELECT * FROM workspace_run_items WHERE run_id = ? ORDER BY position",
            (run_id,),
        ).fetchall()
        result = dict(run)
        result["items"] = [dict(item) for item in items]
        return result
    finally:
        conn.close()


def _next_workspace_position(conn):
    return conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM workspace_items").fetchone()[0]


def import_workspace_items(items):
    """Insert ordered generic items and report duplicates without dropping input."""
    results = []
    conn = connect()
    try:
        with conn:
            position = _next_workspace_position(conn)
            for item in items:
                existing = conn.execute(
                    "SELECT id FROM workspace_items "
                    "WHERE youtube_id = ? AND track_id IS NULL LIMIT 1",
                    (item["youtube_id"],),
                ).fetchone()
                if existing is not None:
                    results.append({"status": "duplicate", "id": existing["id"], **item})
                    continue
                cur = conn.execute(
                    "INSERT INTO workspace_items "
                    "(youtube_id, youtube_url, position, provenance, metadata_json) "
                    "VALUES (?,?,?,?,?)",
                    (item["youtube_id"], item["youtube_url"], position,
                     item.get("provenance", "paste"), item.get("metadata_json")),
                )
                results.append({"status": "added", "id": cur.lastrowid, **item})
                position += 1
    finally:
        conn.close()
    return results


def _merge_json(old, new):
    result = {}
    for value in (old, new):
        result.update(_load_json(value))
    return json.dumps(result, default=str) if result else None


def _is_library_provenance(value):
    return "library" in {part.strip() for part in (value or "").replace("+", ",").split(",")}


def promote_library_track(track_id):
    """Create/promote one exact track relation without touching curation state."""
    conn = connect()
    try:
        with conn:
            track = conn.execute(
                "SELECT id, filename, yt_id, title, artist FROM tracks WHERE id = ?",
                (track_id,),
            ).fetchone()
            if track is None:
                raise KeyError(f"No track with id {track_id}")
            youtube_id = track["yt_id"]
            has_link = isinstance(youtube_id, str) and bool(_YOUTUBE_ID_RE.fullmatch(youtube_id))
            if not has_link:
                # Link-less local track: stage with null YouTube; Find-link fills it later.
                existing = conn.execute(
                    "SELECT * FROM workspace_items WHERE track_id = ?", (track_id,)).fetchone()
                if existing is not None:
                    return dict(existing)
                cur = conn.execute(
                    "INSERT INTO workspace_items (title, provenance, position, track_id) VALUES (?,?,?,?)",
                    (track["title"], "library", _next_workspace_position(conn), track_id))
                return dict(conn.execute(
                    "SELECT * FROM workspace_items WHERE id = ?", (cur.lastrowid,)).fetchone())
            exact = conn.execute(
                "SELECT * FROM workspace_items WHERE track_id = ?", (track_id,)
            ).fetchone()
            if exact is not None:
                conn.execute(
                    "UPDATE workspace_items SET youtube_id = ?, youtube_url = ?, "
                    "title = COALESCE(?, title) WHERE id = ?",
                    (youtube_id, "https://www.youtube.com/watch?v=" + youtube_id,
                     track["title"], exact["id"]),
                )
            generic = conn.execute(
                "SELECT * FROM workspace_items WHERE youtube_id = ? AND track_id IS NULL",
                (youtube_id,),
            ).fetchall()
            if exact is None:
                if generic:
                    source = generic[0]
                    conn.execute(
                        "UPDATE workspace_items SET track_id = ?, provenance = ?, "
                        "metadata_json = ? WHERE id = ?",
                        (track_id, (source["provenance"] + ",library"),
                         _merge_json(source["metadata_json"], None), source["id"]),
                    )
                    exact = conn.execute(
                        "SELECT * FROM workspace_items WHERE id = ?", (source["id"],)
                    ).fetchone()
                else:
                    cur = conn.execute(
                        "INSERT INTO workspace_items "
                        "(youtube_id, youtube_url, title, provenance, position, track_id) "
                        "VALUES (?,?,?,?,?,?)",
                        (youtube_id, "https://www.youtube.com/watch?v=" + youtube_id,
                         track["title"], "library", _next_workspace_position(conn), track_id),
                    )
                    exact = conn.execute(
                        "SELECT * FROM workspace_items WHERE id = ?", (cur.lastrowid,)
                    ).fetchone()
            if generic:
                source = generic[0]
                current = conn.execute(
                    "SELECT metadata_json, provenance FROM workspace_items WHERE id = ?",
                    (exact["id"],),
                ).fetchone()
                if source["id"] != exact["id"]:
                    conn.execute(
                        "UPDATE workspace_items SET metadata_json = ?, provenance = ? WHERE id = ?",
                        (_merge_json(current["metadata_json"], source["metadata_json"]),
                         current["provenance"] + "," + source["provenance"], exact["id"]),
                    )
                    conn.execute("DELETE FROM workspace_items WHERE id = ?", (source["id"],))
            exact = conn.execute(
                "SELECT * FROM workspace_items WHERE track_id = ?", (track_id,)
            ).fetchone()
            return dict(exact)
    finally:
        conn.close()


def add_workspace_file(folder_identity, relative_path, title=None):
    """Stage a local file directly in the Workspace (no Library track)."""
    conn = connect()
    try:
        with conn:
            existing = conn.execute(
                "SELECT * FROM workspace_items WHERE folder_identity=? AND relative_path=? AND track_id IS NULL",
                (folder_identity, relative_path)).fetchone()
            if existing is not None:
                return dict(existing)
            cur = conn.execute(
                "INSERT INTO workspace_items (title, provenance, position, folder_identity, relative_path) "
                "VALUES (?,?,?,?,?)",
                (title, "local-file", _next_workspace_position(conn), folder_identity, relative_path))
            return dict(conn.execute("SELECT * FROM workspace_items WHERE id=?", (cur.lastrowid,)).fetchone())
    finally:
        conn.close()


def remove_workspace_items(ids):
    conn = connect()
    try:
        with conn:
            existing = {r[0] for r in conn.execute("SELECT id FROM workspace_items")}
            missing = set(ids) - existing
            if missing:
                raise KeyError(f"Unknown Workspace item id(s): {sorted(missing)}")
            conn.executemany("DELETE FROM workspace_items WHERE id = ?", ((i,) for i in ids))
            for position, item_id in enumerate(
                r[0] for r in conn.execute("SELECT id FROM workspace_items ORDER BY position, id")
            ):
                conn.execute("UPDATE workspace_items SET position = ? WHERE id = ?",
                             (position, item_id))
    finally:
        conn.close()
    return list_workspace()


def reorder_workspace(ids):
    conn = connect()
    try:
        with conn:
            current = [r[0] for r in conn.execute("SELECT id FROM workspace_items")]
            if len(ids) != len(set(ids)) or set(ids) != set(current):
                raise ValueError("reorder requires every Workspace item ID exactly once")
            offset = len(ids) + 1
            conn.execute("UPDATE workspace_items SET position = position + ?", (offset,))
            for position, item_id in enumerate(ids):
                conn.execute("UPDATE workspace_items SET position = ? WHERE id = ?",
                             (position, item_id))
    finally:
        conn.close()
    return list_workspace()


def save_workspace_links(ids):
    conn = connect()
    added, duplicates = [], []
    try:
        with conn:
            for item_id in ids:
                item = conn.execute("SELECT * FROM workspace_items WHERE id = ?", (item_id,)).fetchone()
                if item is None:
                    raise KeyError(f"Unknown Workspace item id {item_id}")
                library = _is_library_provenance(item["provenance"]) and item["track_id"] is not None
                existing = conn.execute(
                    "SELECT * FROM saved_links WHERE youtube_id = ?", (item["youtube_id"],)
                ).fetchone()
                if existing is None:
                    cur = conn.execute(
                        "INSERT INTO saved_links "
                        "(youtube_id, youtube_url, track_id, metadata_json) VALUES (?,?,?,?)",
                        (item["youtube_id"], item["youtube_url"],
                         item["track_id"] if library else None, item["metadata_json"]),
                    )
                    added.append(cur.lastrowid)
                else:
                    if library and existing["track_id"] is None:
                        conn.execute("UPDATE saved_links SET track_id = ? WHERE id = ?",
                                     (item["track_id"], existing["id"]))
                    elif library and existing["track_id"] != item["track_id"]:
                        raise sqlite3.IntegrityError(
                            "saved link already belongs to another track"
                        )
                    duplicates.append(existing["id"])
    finally:
        conn.close()
    return {"added": added, "duplicates": duplicates, "links": list_saved_links()}


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


def _clean_value(value):
    """Convert pandas missing/numpy scalar values to SQLite-safe values."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value.item() if hasattr(value, "item") else value


def sync_matches_csv():
    """Merge fresh matches.csv into SQLite, preserving curation history.

    CSV is intentionally used directly here: this is a pipeline checkpoint,
    not the XLSX-priority startup reconciliation.
    """
    if not os.path.exists(MATCHES_CSV):
        raise ValueError("matches.csv is missing")
    try:
        frame = _read_matches(MATCHES_CSV)
    except Exception as e:
        raise ValueError(f"invalid matches.csv: {e}") from e
    if "filename" not in frame.columns or frame.empty:
        raise ValueError("matches.csv must contain non-empty filename rows")
    frame = frame.where(pd.notna(frame), None)
    if frame["filename"].isna().any() or (frame["filename"].astype(str).str.strip() == "").any():
        raise ValueError("matches.csv contains blank filename")
    if frame["filename"].duplicated().any():
        raise ValueError("matches.csv contains duplicate filename rows")

    conn = connect()
    try:
        with conn:
            for raw in frame.to_dict(orient="records"):
                d = {k: _clean_value(v) for k, v in raw.items()}
                filename = d["filename"]
                old = conn.execute(
                    "SELECT id, extra_json FROM tracks WHERE filename = ?", (filename,)
                ).fetchone()
                if old is None:
                    _insert_track(conn, d)
                    continue
                old_extra = {}
                if old["extra_json"]:
                    old_extra = json.loads(old["extra_json"])
                incoming_extra = {k: v for k, v in d.items() if k not in _CORE_COLS}
                old_extra.update(incoming_extra)
                updates = []
                values = []
                for col in sorted(_CORE_COLS - {"filename", "check"}):
                    if col not in d:
                        continue
                    value = d[col]
                    if col == "local_better":
                        value = 1 if value in (True, 1, "True", "TRUE") else 0
                    updates.append(f'"{col}" = ?')
                    values.append(value)
                updates.append("extra_json = ?")
                values.append(json.dumps(old_extra, default=str) if old_extra else None)
                values.append(old["id"])
                conn.execute(
                    f'UPDATE tracks SET {", ".join(updates)} WHERE id = ?', values
                )
    finally:
        conn.close()
    return {"rows": len(frame)}


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
        return [_expand_extra(dict(r)) for r in rows], total
    finally:
        conn.close()


def _expand_extra(row):
    """Flatten the extra_json blob back into the row so preserved fields
    (mb_artist, mb_confidence, acoustid_id, ...) are visible to the API.

    Also scrub non-finite floats (NaN/Inf) to None: blank numeric cells arrive as
    NaN (both from REAL columns and from extra_json, which is seeded with the default
    allow_nan=True and reads "NaN" back as a float), and Starlette's JSON encoder uses
    allow_nan=False -- an unscrubbed NaN 500s the whole /api/rows response."""
    extra = row.pop("extra_json", None)
    if extra:
        try:
            for k, v in json.loads(extra).items():
                row.setdefault(k, v)
        except (ValueError, TypeError):
            pass
    return {k: (None if isinstance(v, float) and not math.isfinite(v) else v)
            for k, v in row.items()}


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
    # yt_health is a runtime link-health cache, not part of the matches schema.
    extra_cols = [col for col in df.columns if col not in TRACK_COLUMNS and col not in ("id", "yt_health")]
    return df[TRACK_COLUMNS + extra_cols]


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
