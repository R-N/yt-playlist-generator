"""
Tests for the background task registry + worker (tasks.py) and its db layer.

    python -m unittest test_tasks -v

Redirects db to a temp DB so no real data is touched. Worker delay is forced to
(0, 0) so sweeps finish instantly; a threading gate parks the worker mid-item to
test cancel / single-run guard deterministically (no sleep-based races).
"""
import os
import tempfile
import threading
import unittest

import db
import tasks


class TasksTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = {k: getattr(db, k) for k in
                      ("DB_PATH", "MATCHES_CSV", "MATCHES_XLSX", "MATCHES_SOURCE", "BACKUP_DIR")}
        db.DB_PATH = os.path.join(self.tmp.name, "review.db")
        db.MATCHES_CSV = os.path.join(self.tmp.name, "matches.csv")
        db.MATCHES_XLSX = os.path.join(self.tmp.name, "matches.xlsx")
        db.MATCHES_SOURCE = db.MATCHES_XLSX
        db.BACKUP_DIR = os.path.join(self.tmp.name, "backups")
        db.init_db()
        # Reset the module's in-memory state between tests.
        tasks._active = None
        tasks._cancel.clear()
        tasks._threads.clear()

    def tearDown(self):
        for t in list(tasks._threads.values()):
            t.join(timeout=2)
        for k, v in self._orig.items():
            setattr(db, k, v)
        self.tmp.cleanup()

    def run_and_join(self, kind, title, ids, do_one, delay=(0, 0)):
        task = tasks.run(kind, title, ids, do_one, delay=delay)
        tasks._threads[task["id"]].join(timeout=3)
        return db.get_task(task["id"])


class TaskCrudTest(TasksTestBase):
    def test_create_bump_finish_and_list_ordering(self):
        a = db.create_task("k", "first", total=3)
        b = db.create_task("k", "second", total=5)
        db.bump_task(a, done=2, found=1)
        db.finish_task(a, "done", "ok")
        row = db.get_task(a)
        self.assertEqual((row["done"], row["found"], row["status"]), (2, 1, "done"))
        # b is still running -> must sort ahead of the finished a.
        self.assertEqual([t["id"] for t in db.list_tasks()][0], b)

    def test_list_decisions_newest_first(self):
        conn = db.connect()
        with conn:
            cur = conn.execute("INSERT INTO tracks (filename, yt_id) VALUES ('x.mp3','abc')")
            tid = cur.lastrowid
        conn.close()
        db.record_decision(tid, 1)
        db.record_decision(tid, 0)
        rows = db.list_decisions()
        self.assertEqual([r["decision"] for r in rows], [0, 1])   # newest first


class WorkerTest(TasksTestBase):
    def test_sweep_flags_and_finishes(self):
        seen = []
        result = self.run_and_join("library-verify", "v", [1, 2, 3, 4],
                                   lambda i: seen.append(i) or (i % 2 == 0))
        self.assertEqual(seen, [1, 2, 3, 4])
        self.assertEqual((result["status"], result["done"], result["found"]), ("done", 4, 2))
        self.assertIsNone(tasks.active())          # lock released

    def test_bad_item_does_not_kill_sweep(self):
        def do_one(i):
            if i == 2:
                raise ValueError("boom")
            return False
        result = self.run_and_join("k", "v", [1, 2, 3], do_one)
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["done"], 3)        # all counted, including the raiser

    def test_network_cutoff_stops_with_error(self):
        orig = tasks.NETWORK_FAIL_CUTOFF
        tasks.NETWORK_FAIL_CUTOFF = 3
        try:
            def do_one(_):
                raise tasks.NetworkDown()
            result = self.run_and_join("k", "v", list(range(10)), do_one)
        finally:
            tasks.NETWORK_FAIL_CUTOFF = orig
        self.assertEqual(result["status"], "error")
        self.assertIn("network", result["message"].lower())


class ConcurrencyTest(TasksTestBase):
    def _parked_sweep(self):
        """Start a sweep whose first item blocks on a gate, so the worker sits
        'running' until released. Returns (task, gate, entered_event)."""
        gate = threading.Event()
        entered = threading.Event()
        calls = []

        def do_one(_):
            calls.append(1)
            if len(calls) == 1:
                entered.set()
                gate.wait(2)
            return False

        task = tasks.run("k", "v", list(range(5)), do_one, delay=(0, 0))
        entered.wait(2)
        return task, gate

    def test_cancel_stops_after_current_item(self):
        task, gate = self._parked_sweep()
        self.assertTrue(tasks.request_cancel(task["id"]))
        gate.set()
        tasks._threads[task["id"]].join(timeout=3)
        row = db.get_task(task["id"])
        self.assertEqual(row["status"], "cancelled")
        self.assertEqual(row["done"], 1)           # only the parked first item finished

    def test_second_verify_refused_while_one_runs(self):
        task, gate = self._parked_sweep()
        with self.assertRaises(RuntimeError):
            tasks.run("k", "v2", [1, 2], lambda i: False)
        gate.set()
        tasks._threads[task["id"]].join(timeout=3)
        self.assertIsNone(tasks.active())


class OrphanTest(TasksTestBase):
    def test_restart_marks_running_task_interrupted(self):
        tid = db.create_task("k", "left running", total=9)
        db.init_db()                                # simulates an app restart
        self.assertEqual(db.get_task(tid)["status"], "interrupted")


if __name__ == "__main__":
    unittest.main(verbosity=2)
