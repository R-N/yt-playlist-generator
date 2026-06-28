"""Paths and settings for the review app. Edit here, not in code."""
import os

# Repo root (two levels up from this file: review_app/backend/ -> repo)
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Live store (SQLite) and the CSV/XLSX it imports from / exports to
DB_PATH = os.path.join(os.path.dirname(__file__), "review.db")
MATCHES_CSV = os.path.join(REPO_ROOT, "matches.csv")
MATCHES_XLSX = os.path.join(REPO_ROOT, "matches.xlsx")
BACKUP_DIR = os.path.join(os.path.dirname(__file__), "..", "backups")

# Where first-run import reads existing curation FROM. matches.xlsx is the
# file actually hand-edited in Excel and is the authoritative source of the
# `check` marks (matches.csv lags it). On first run the app reconciles BOTH
# csv+xlsx into SQLite; export then writes BOTH files.
MATCHES_SOURCE = MATCHES_XLSX

# Auto-save: after this many decisions, atomically rewrite matches.csv so marks
# flow back to the git-tracked file continuously. 0 disables. Manual "Export
# CSV" (full snapshot + xlsx) is always available regardless.
AUTO_EXPORT_EVERY = 25

# Folders holding the local mp3 files (read-only; the app never writes here)
MP3_FOLDERS = [
    "E:/Music/My Music",
    "E:/Music/My Music Out 2",
    "E:/Music/downloads",
]

# Columns carried from matches.csv into the tracks table, in order.
TRACK_COLUMNS = [
    "filename", "artist", "title", "acoustid_id", "yt_query", "yt_id",
    "yt_channel", "yt_title", "duration", "audio_format", "audio_codec",
    "audio_bitrate", "score", "yt_channel_id", "yt_views", "duplicated",
    "sim_artist", "sim_artist_title", "sim_title", "sim_filename", "pass",
    "check", "local_bitrate", "local_better",
]
