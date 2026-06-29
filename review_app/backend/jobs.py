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
    }
