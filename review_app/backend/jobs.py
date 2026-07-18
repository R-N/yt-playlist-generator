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
import signal
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager

from config import REPO_ROOT

# name -> (script filename, one-line description, destructive). Only scripts
# safe to launch with no required CLI args are listed; their behavior is set by
# the constants at the top of each file (and, for downloader/acoustid, env vars).
# `destructive` scripts DELETE files on disk — the UI gates them behind a typed
# confirmation so a stray click can't wipe the library.
SCRIPTS = {
    "url_extractor":        ("url_extractor.py",        "dump.csv -> ids.txt / urls.txt / playlists.txt", False),
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
    "url_extractor":        [("ids.txt", "video ids", "lines"), ("urls.txt", "urls", "lines"), ("playlists.txt", "playlist links", "links")],
    "playlist_generator":   [("playlists.txt", "playlist links", "links")],
    "downloader":           [("downloaded_ids.txt", "completed download IDs", "lines"), ("error_ids.txt", "failed", "lines")],
    "searcher":             [("matches.csv", "matched rows", "csv")],
    "filter_local_quality": [("ids2.txt", "flagged for redownload", "lines")],
    "acoustid_enrich":      [("matches.csv", "rows", "csv")],
    "check_untracked":      [("untracked.txt", "untracked files", "lines")],
    "cleanup_downloads":    [("downloaded_ids.txt", "completed download IDs", "lines")],
    "cleanup_tracked":      [("matches.csv", "tracked rows", "csv")],
}

CURATION_READERS = {"check_untracked", "cleanup_tracked"}
CURATION_WRITERS = {"searcher", "filter_local_quality", "acoustid_enrich"}


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
            item["links"] = lines
        elif kind == "csv":
            item["count"] = max(0, _count_lines(path) - 1) if exists else 0
        else:
            item["count"] = _count_lines(path) if exists else 0
        out.append(item)
    return out


class _Job:
    def __init__(self, run_id=None):
        self.proc = None
        self.lines = collections.deque(maxlen=4000)
        self.status = "idle"          # idle | running | stopping | finalizing | done | failed | stopped
        self.returncode = None
        self.started = None
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.proc_exited = threading.Event()
        self.stop_requested = False
        self.lifecycle_aborted = False
        self.run_id = run_id


_jobs = {}      # name -> _Job
_coordinator_lock = threading.Lock()
_active_name = None
_curation_lease = None
STOP_GRACE = 1
FORCE_WAIT = 1


def _job(name):
    return _jobs.setdefault(name, _Job())


def catalog():
    return [
        {"name": n, "script": s, "desc": d, "destructive": x,
         "curation": ("reader" if n in CURATION_READERS else
                       "writer" if n in CURATION_WRITERS else None)}
        for n, (s, d, x) in SCRIPTS.items()
    ]


def _release(name):
    global _active_name, _curation_lease
    with _coordinator_lock:
        if _active_name == name:
            _active_name = None
        if _curation_lease == name:
            _curation_lease = None


def reserve_pipeline(name):
    """Atomically hold global coordinator for non-subprocess pipeline work."""
    global _active_name
    with _coordinator_lock:
        if _active_name is not None:
            raise RuntimeError(f"pipeline job {_active_name} is already active")
        _active_name = name


def release_pipeline(name):
    _release(name)


def curation_active():
    with _coordinator_lock:
        return _curation_lease is not None


def pipeline_active():
    with _coordinator_lock:
        return _active_name is not None


@contextmanager
def curation_write_guard():
    """Serialize API curation writes against a leased pipeline job."""
    with _coordinator_lock:
        if _curation_lease is not None:
            raise RuntimeError("curation pipeline job is active")
        yield


def _stop_process(proc, force=False):
    """Signal whole process group; fall back to child-only for test doubles."""
    try:
        if os.name == "nt":
            if not force:
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                result = subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    check=False,
                )
                if result.returncode:
                    proc.kill()
        else:
            os.killpg(proc.pid, signal.SIGKILL if force else signal.SIGTERM)
    except (AttributeError, OSError, ProcessLookupError):
        try:
            (proc.kill if force else proc.terminate)()
        except (AttributeError, OSError, ProcessLookupError):
            pass


def start(name, args=None, prepare=None, finalize=None, curation=False,
          env_overrides=None, reservation_name=None, run_id=None):
    """Start one job. Hooks are generic; jobs never imports the database."""
    global _active_name, _curation_lease
    if name not in SCRIPTS:
        raise KeyError(name)
    lease_name = reservation_name or name
    if reservation_name is None:
        reserve_pipeline(name)
    else:
        with _coordinator_lock:
            if _active_name != reservation_name:
                raise RuntimeError("provided pipeline reservation is not active")
    with _coordinator_lock:
        # New object prevents a just-released run from racing an immediate restart.
        job = _Job(run_id)
        _jobs[name] = job
        if curation:
            _curation_lease = name
    with job.lock:
        job.lines.clear()
        job.status = "running"
        job.returncode = None
        job.started = time.time()
        job.stop_requested = False
        job.lifecycle_aborted = False
        job.proc_exited.clear()
        job.done.clear()

    script_path = os.path.join(REPO_ROOT, SCRIPTS[name][0])
    argv = [sys.executable, "-u", script_path] + list(args or [])

    try:
        if prepare is not None:
            prepare(name)
    except Exception as e:
        with job.lock:
            job.lines.append(f"[prepare error] {e}")
            job.status = "failed"
        # Prepare failed before child launch. Do not run post-run hooks: stale
        # CSV must never be imported as a substitute for a failed preparation.
        _release(lease_name)
        job.done.set()
        return state(name)

    def run():
        failed = False
        stopped = False
        exited = False
        try:
            popen_options = {"start_new_session": True} if os.name != "nt" else {
                "creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            }
            environment = dict(os.environ)
            environment.update(env_overrides or {})
            job.proc = subprocess.Popen(  # type: ignore[call-overload]
                argv, cwd=REPO_ROOT, env=environment,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
                **popen_options,
            )
            with job.lock:
                should_stop = job.stop_requested
            if should_stop and job.proc.poll() is None:
                _stop_process(job.proc)
            reader_failed = False
            try:
                for line in job.proc.stdout or ():
                    with job.lock:
                        job.lines.append(line.rstrip("\n"))
            except Exception as e:
                with job.lock:
                    job.lines.append(f"[reader error] {e}")
                reader_failed = True
            try:
                job.proc.wait()
                exited = job.proc.poll() is not None
            except Exception as e:
                with job.lock:
                    job.lines.append(f"[wait error] {e}")
                failed = True
            with job.lock:
                if exited:
                    job.returncode = job.proc.returncode
                stopped = job.stop_requested
                failed = failed or reader_failed
        except Exception as e:            # surface launcher errors in the log
            with job.lock:
                job.lines.append(f"[runner error] {e}")
            failed = True
        finally:
            # A failed wait still gets process cleanup before finalization.
            proc = job.proc
            # Recompute from child handle: wait() may fail after child exit,
            # while Popen failure leaves no child at all.
            if proc is None:
                exited = True
            elif not exited and proc.poll() is None:
                _stop_process(proc, force=True)
                try:
                    proc.wait(timeout=FORCE_WAIT)
                except Exception:
                    failed = True
                exited = proc.poll() is not None
            elif not exited:
                exited = proc.poll() is not None
            if exited:
                with job.lock:
                    job.returncode = proc.returncode if proc is not None else job.returncode
                job.proc_exited.set()
            with job.lock:
                stopped = job.stop_requested
                aborted = job.lifecycle_aborted
            if exited:
                with job.lock:
                    job.status = "finalizing"
                try:
                    if finalize is not None:
                        finalize(name)
                except Exception as e:
                    with job.lock:
                        job.lines.append(f"[finalizer error] {e}")
                    failed = True
                with job.lock:
                    job.status = "failed" if failed or aborted else (
                        "stopped" if stopped else
                        ("done" if job.returncode == 0 else "failed"))
            else:
                with job.lock:
                    job.status = "failed"   # reached only when the child never exited
            stdout = getattr(job.proc, "stdout", None)
            if stdout is not None:
                try:
                    stdout.close()
                except Exception:
                    pass
            with job.lock:
                job.proc = None
            if exited:
                _release(lease_name)
            job.done.set()

    try:
        threading.Thread(target=run, daemon=True).start()
    except Exception:
        _release(lease_name)
        with job.lock:
            job.status = "failed"
        raise
    return state(name)


def finalization_result(name, run_id=None):
    job = _job(name)
    with job.lock:
        if run_id is not None and job.run_id != run_id:
            return {"returncode": None, "stopped": False}
        return {"returncode": job.returncode, "stopped": job.stop_requested}


def stop(name):
    job = _job(name)
    with job.lock:
        proc = job.proc
        active = job.status in ("running", "stopping")
        if active:
            job.stop_requested = True
            job.status = "stopping"
    if active:
        if proc and proc.poll() is None:
            _stop_process(proc)
        if not job.proc_exited.wait(STOP_GRACE):
            with job.lock:
                proc = job.proc
            if proc and proc.poll() is None:
                _stop_process(proc, force=True)
            if not job.proc_exited.wait(FORCE_WAIT):
                with job.lock:
                    job.lifecycle_aborted = True
                    job.status = "failed"
                    job.lines.append("[stop error] process exit not confirmed")
        if job.proc_exited.is_set():
            job.done.wait(FORCE_WAIT)
    return state(name)


def state(name, tail=None):
    job = _job(name)
    with job.lock:
        lines = list(job.lines)
        status = job.status
        returncode = job.returncode
        started = job.started
        run_id = job.run_id
    if tail is not None:
        lines = lines[-tail:]
    return {
        "name": name,
        "status": status,
        "returncode": returncode,
        "started": started,
        "run_id": run_id,
        "lines": lines,
        "artifacts": artifacts(name),
    }
