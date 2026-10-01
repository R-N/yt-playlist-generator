"""
Background task registry + worker for the Activity log.

Verifying YouTube link health for thousands of tracks can't be a blocking,
back-to-back loop: YouTube rate-limits, and a big library would hammer it. So a
verify runs here as ONE background worker thread that paces itself with a small
randomized delay between fetches, is cancellable, and records a persisted task
row (db.background_tasks) the Activity tab polls — running now, or finished with
a result. Only one verify runs at a time (the rate limit is global, per-IP), so
a second request is refused while one is active.

Every sweep is resumable. The task row stores its ordered id list, and `done`
(bumped once per finished item, after that item's own DB write) is the cursor
into it. A sweep that was interrupted by a restart, cancelled, or stopped by the
network cutoff continues at its first unfinished item. The item in flight when
the process died is simply run again, so each `do_one` must be idempotent.

This module stays generic: main.py registers each kind with a `build(ids)` that
returns `do_one(id)->bool` (the yt-dlp fetch + DB write). `build` derives
everything from current DB state, so the same call starts a fresh sweep and
rebuilds one in a new process. `bool` = "flagged" (a dead/private link),
surfaced as the task's `found` count.
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
# Stopped states a task can be resumed from ('done' has nothing left to do).
RESUMABLE = ("interrupted", "error", "cancelled")

_lock = threading.Lock()
_active = None            # task id of the running verify, or None
_cancel = set()           # task ids asked to cancel
_threads = {}             # task id -> Thread (tests join on these)
_kinds = {}               # kind -> (title, build, delay, noun)


def register(kind, title, build, delay=None, noun="flagged"):
    """Declare a sweep kind. `delay` is a zero-arg callable returning (min, max)
    seconds, read when the sweep starts; None = DELAY. `noun` labels the `found`
    count in the finished-task message."""
    _kinds[kind] = (title, build, delay, noun)


def resumable(task):
    return bool(task["status"] in RESUMABLE and task["kind"] in _kinds
                and task["has_ids"] and task["done"] < task["total"])


def snapshot(limit=100):
    return [dict(t, resumable=resumable(t)) for t in db.list_tasks(limit)]


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


def run(kind, ids):
    """Start a background sweep of a registered kind over `ids`. Raises RuntimeError
    if one is already running. Returns the task row."""
    title = _kinds[kind][0]
    ids = list(ids)
    with _lock:
        if _active is not None:
            raise RuntimeError("a background task is already running")
        task_id = db.create_task(kind, title, ids)
        _launch(task_id, kind, ids)
    return db.get_task(task_id)


def resume(task_id):
    """Continue a stopped sweep at its first unfinished item. Returns the task row,
    None if it isn't resumable; RuntimeError if another sweep is running."""
    with _lock:
        if _active is not None:
            raise RuntimeError("a background task is already running")
        task = db.get_task(task_id)
        if not task or not resumable(task):
            return None
        remaining = db.task_ids(task_id)[task["done"]:]
        db.reopen_task(task_id)
        _launch(task_id, task["kind"], remaining)
    return db.get_task(task_id)


def recover():
    """App startup: continue the sweep the previous process died in the middle of
    (init_db marked it 'interrupted'). One sweep runs at a time, so only the oldest
    resumable one continues; anything else stays interrupted with a Resume action."""
    for task in db.interrupted_tasks():
        if resumable(task):
            return resume(task["id"])
    return None


def _launch(task_id, kind, ids):
    """Start the worker thread. Caller holds _lock."""
    global _active
    _, build, delay, noun = _kinds[kind]
    _active = task_id
    thread = threading.Thread(
        target=_worker, args=(task_id, ids, build, delay() if delay else DELAY, noun), daemon=True)
    _threads[task_id] = thread
    thread.start()


def _worker(task_id, ids, build, delay, noun):
    global _active
    fails = 0
    try:
        try:
            do_one = build(ids)
        except Exception as e:
            db.finish_task(task_id, "error", f"could not start: {e}")
            return
        for _id in ids:
            if is_cancelled(task_id):
                break
            try:
                flagged = bool(do_one(_id))
                fails = 0
                db.bump_task(task_id, done=1, found=1 if flagged else 0, ok=1)
            except _NetworkDown:
                fails += 1
                db.bump_task(task_id, done=1, skipped=1)
                if fails >= NETWORK_FAIL_CUTOFF:
                    # Un-count the failing streak: `done` is the resume cursor, so
                    # Resume retries these items instead of skipping past them.
                    db.bump_task(task_id, done=-fails, skipped=-fails)
                    db.finish_task(task_id, "error",
                                   "stopped after repeated network errors — resume to retry")
                    return
                continue
            except Exception:            # one bad item must not kill the sweep
                db.bump_task(task_id, done=1, failed=1)
            time.sleep(random.uniform(*delay))
        task = db.get_task(task_id)
        summary = _summary(task, noun)
        db.finish_task(task_id, "cancelled" if is_cancelled(task_id) else "done", summary)
    finally:
        _cancel.discard(task_id)
        with _lock:
            if _active == task_id:
                _active = None


def _summary(task, noun):
    """Finished-task message: found count + per-outcome tallies."""
    return (f"{task['found']} {noun} · {task['ok']} ok, "
            f"{task['failed']} failed, {task['skipped']} skipped")


class _NetworkDown(Exception):
    """do_one raises this when a fetch failed in a way that looks like the network
    is down (health 'unknown'), so the worker can trip the cutoff."""


# Exported so callers (main.py) can signal a likely-network failure.
NetworkDown = _NetworkDown
