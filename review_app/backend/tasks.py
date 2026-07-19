"""
Background task registry + worker for the Activity log.

Verifying YouTube link health for thousands of tracks can't be a blocking,
back-to-back loop: YouTube rate-limits, and a big library would hammer it. So a
verify runs here as ONE background worker thread that paces itself with a small
randomized delay between fetches, is cancellable, and records a persisted task
row (db.background_tasks) the Activity tab polls — running now, or finished with
a result. Only one verify runs at a time (the rate limit is global, per-IP), so
a second request is refused while one is active.

This module stays generic: the caller passes the id list and a `do_one(id)->bool`
that does the actual yt-dlp fetch + DB write (those live in main.py). `bool` =
"flagged" (a dead/private link), surfaced as the task's `found` count.
"""
import random
import threading
import time

import db

# Randomized so requests don't land on a fixed cadence. Tests set this to (0, 0).
DELAY = (1.5, 4.0)
# Consecutive 'unknown' results usually mean the network/yt-dlp is down, not that
# every link died — stop and let the user resume rather than burn the whole list.
NETWORK_FAIL_CUTOFF = 8

_lock = threading.Lock()
_active = None            # task id of the running verify, or None
_cancel = set()           # task ids asked to cancel
_threads = {}             # task id -> Thread (tests join on these)


def snapshot(limit=100):
    return db.list_tasks(limit)


def request_cancel(task_id):
    """Ask the running worker to stop after its current item."""
    task = db.get_task(task_id)
    if not task or task["status"] != "running":
        return False
    _cancel.add(task_id)
    return True


def is_cancelled(task_id):
    return task_id in _cancel


def active():
    with _lock:
        return _active


def run(kind, title, ids, do_one, delay=None, noun="flagged"):
    """Start a background sweep over `ids`. Raises RuntimeError if one is already
    running. `noun` labels the `found` count in the finished-task message
    ('flagged' for verify, 'found' for auto link/file finding). Returns the task row."""
    global _active
    with _lock:
        if _active is not None:
            raise RuntimeError("a background task is already running")
        task_id = db.create_task(kind, title, len(ids))
        _active = task_id
    thread = threading.Thread(
        target=_worker, args=(task_id, list(ids), do_one, delay or DELAY, noun), daemon=True)
    _threads[task_id] = thread
    thread.start()
    return db.get_task(task_id)


def _worker(task_id, ids, do_one, delay, noun="flagged"):
    global _active
    fails = 0
    try:
        for _id in ids:
            if is_cancelled(task_id):
                break
            try:
                flagged = bool(do_one(_id))
                fails = 0
            except _NetworkDown:
                fails += 1
                if fails >= NETWORK_FAIL_CUTOFF:
                    db.finish_task(task_id, "error",
                                   "stopped after repeated network errors — resume later")
                    return
                continue
            except Exception:            # one bad item must not kill the sweep
                flagged = False
            db.bump_task(task_id, done=1, found=1 if flagged else 0)
            time.sleep(random.uniform(*delay))
        task = db.get_task(task_id)
        if is_cancelled(task_id):
            db.finish_task(task_id, "cancelled", f"{task['found']} {noun}")
        else:
            db.finish_task(task_id, "done", f"{task['found']} {noun}")
    finally:
        _cancel.discard(task_id)
        with _lock:
            if _active == task_id:
                _active = None


class _NetworkDown(Exception):
    """do_one raises this when a fetch failed in a way that looks like the network
    is down (health 'unknown'), so the worker can trip the cutoff."""


# Exported so callers (main.py) can signal a likely-network failure.
NetworkDown = _NetworkDown
