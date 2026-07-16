"""
Background job runner: launches the repo's standalone scripts as subprocesses
and captures their output for the Pipeline tab.

Why subprocess and not import-and-call: each root script hardcodes its config
as module-level constants and is built to be run as `python <script>.py`,
sharing plain files (ids.txt, matches.csv, ...) as its interface. Running them
unchanged as subprocesses preserves that contract, keeps a long download/scan
isolated from the web server, and lets the user Stop one. The app orchestrates;
the scripts stay the source of truth.
"""
import collections
import os
import subprocess
import sys
import threading
import time

from config import REPO_ROOT

# name -> (script filename, one-line description, destructive). Only scripts
# safe to launch with no required CLI args are listed; their behavior is set by
# the constants at the top of each file (and, for downloader/acoustid, env vars).
# `destructive` scripts DELETE files on disk — the UI gates them behind a typed
# confirmation so a stray click can't wipe the library.
SCRIPTS = {
    "url_extractor":        ("url_extractor.py",        "dump.csv -> ids1.txt / urls.txt / playlists.txt", False),
    "playlist_generator":   ("playlist_generator.py",   "urls.txt -> playlists.txt (50-id chunks)", False),
    "downloader":           ("downloader.py",           "ids.txt -> downloads/ (audio via yt-dlp)", False),
    "searcher":             ("searcher.py",             "scan MP3_FOLDERS -> matches.csv (reverse match)", False),
    "filter_local_quality": ("filter_local_quality.py", "flag local mp3 >= 192kbps -> ids2.txt", False),
    "acoustid_enrich":      ("acoustid_enrich.py",      "AcoustID/MusicBrainz cross-check -> mb_* columns", False),
    "check_untracked":      ("check_untracked.py",      "matches.csv -> untracked.txt (unverified files)", False),
    "cleanup_downloads":    ("cleanup_downloads.py",    "DELETE failed/partial/zero-byte downloads", True),
    "cleanup_tracked":      ("cleanup_tracked.py",      "DELETE source mp3s already verified in matches.csv", True),
}


# What each script produces, so the UI can show a human result (counts + links)
# instead of raw stdout. (file, label, kind) — kind: "lines" | "csv" | "links".
# ponytail: shows current file totals, not this-run deltas; the append-only logs
# (downloaded_ids.txt, matches.csv) make a true delta costlier than it's worth.
ARTIFACTS = {
    "url_extractor":        [("ids1.txt", "video ids", "lines"), ("urls.txt", "urls", "lines"), ("playlists.txt", "playlist links", "links")],
    "playlist_generator":   [("playlists.txt", "playlist links", "links")],
    "downloader":           [("downloaded_ids.txt", "downloaded", "lines"), ("error_ids.txt", "failed", "lines")],
    "searcher":             [("matches.csv", "matched rows", "csv")],
    "filter_local_quality": [("ids2.txt", "flagged for redownload", "lines")],
    "acoustid_enrich":      [("matches.csv", "rows", "csv")],
    "check_untracked":      [("untracked.txt", "untracked files", "lines")],
    "cleanup_downloads":    [("downloaded_ids.txt", "downloads remaining", "lines")],
    "cleanup_tracked":      [("matches.csv", "tracked rows", "csv")],
}


def _count_lines(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return sum(1 for ln in f if ln.strip())
    except OSError:
        return 0


def artifacts(name):
    """Summarize a script's output files for human display (counts + playlist links)."""
    out = []
    for fname, label, kind in ARTIFACTS.get(name, []):
        path = os.path.join(REPO_ROOT, fname)
        exists = os.path.isfile(path)
        item = {"file": fname, "label": label, "kind": kind, "exists": exists}
        if kind == "links":
            lines = []
            if exists:
                with open(path, encoding="utf-8", errors="replace") as f:
                    lines = [ln.strip() for ln in f if ln.strip()]
            item["count"] = len(lines)
            item["links"] = lines[:20]          # cap payload; a few playlist urls is plenty
        elif kind == "csv":
            item["count"] = max(0, _count_lines(path) - 1) if exists else 0
        else:
            item["count"] = _count_lines(path) if exists else 0
        out.append(item)
    return out


class _Job:
    def __init__(self):
        self.proc = None
        self.lines = collections.deque(maxlen=4000)
        self.status = "idle"          # idle | running | done | failed | stopped
        self.returncode = None
        self.started = None
        self.lock = threading.Lock()


_jobs = {}      # name -> _Job


def _job(name):
    return _jobs.setdefault(name, _Job())


def catalog():
    return [
        {"name": n, "script": s, "desc": d, "destructive": x}
        for n, (s, d, x) in SCRIPTS.items()
    ]


def start(name, args=None):
    if name not in SCRIPTS:
        raise KeyError(name)
    job = _job(name)
    with job.lock:
        if job.status == "running":
            raise RuntimeError(f"{name} is already running")
        job.lines.clear()
        job.status = "running"
        job.returncode = None
        job.started = time.time()

    script_path = os.path.join(REPO_ROOT, SCRIPTS[name][0])
    argv = [sys.executable, "-u", script_path] + list(args or [])

    def run():
        try:
            job.proc = subprocess.Popen(
                argv, cwd=REPO_ROOT, env=dict(os.environ),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            for line in job.proc.stdout:
                job.lines.append(line.rstrip("\n"))
            job.proc.wait()
            job.returncode = job.proc.returncode
            if job.status != "stopped":
                job.status = "done" if job.returncode == 0 else "failed"
        except Exception as e:            # surface launcher errors in the log
            job.lines.append(f"[runner error] {e}")
            job.status = "failed"

    threading.Thread(target=run, daemon=True).start()
    return state(name)


def stop(name):
    job = _job(name)
    if job.proc and job.status == "running":
        job.status = "stopped"
        job.proc.terminate()
    return state(name)


def state(name, tail=None):
    job = _job(name)
    lines = list(job.lines)
    if tail is not None:
        lines = lines[-tail:]
    return {
        "name": name,
        "status": job.status,
        "returncode": job.returncode,
        "started": job.started,
        "lines": lines,
        "artifacts": artifacts(name),
    }
