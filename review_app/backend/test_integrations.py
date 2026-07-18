"""Tests for the app's script-integration layer: settings (.env), the Discord
service, and the job catalog. Network and subprocess work is stubbed out."""
import os
import subprocess
import tempfile
import time
import unittest
from unittest import mock

import settings
import jobs
import discord_service
import discord_fetch


class TestSettings(unittest.TestCase):
    def setUp(self):
        self._fd, self._path = tempfile.mkstemp(suffix=".env")
        os.close(self._fd)
        self._orig = settings.ENV_PATH
        settings.ENV_PATH = self._path
        for k in settings.MANAGED_KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        settings.ENV_PATH = self._orig
        os.remove(self._path)
        for k in settings.MANAGED_KEYS:
            os.environ.pop(k, None)

    def test_save_roundtrip_and_environ(self):
        settings.save({"DISCORD_BOT_TOKEN": "topsecret1234", "DISCORD_CHANNEL_ID": "999"})
        self.assertEqual(os.environ["DISCORD_BOT_TOKEN"], "topsecret1234")
        self.assertEqual(settings.load_env()["DISCORD_CHANNEL_ID"], "999")

    def test_public_view_masks_secret(self):
        settings.save({"DISCORD_BOT_TOKEN": "topsecret1234"})
        view = settings.public_view()
        self.assertTrue(view["DISCORD_BOT_TOKEN"]["set"])
        self.assertEqual(view["DISCORD_BOT_TOKEN"]["preview"], "••••1234")
        # channel id is non-secret -> shown in full
        self.assertFalse(view["DISCORD_CHANNEL_ID"]["set"])

    def test_blank_clears(self):
        settings.save({"ACOUSTID_API_KEY": "abc"})
        settings.save({"ACOUSTID_API_KEY": ""})
        self.assertNotIn("ACOUSTID_API_KEY", settings.load_env())
        self.assertIsNone(settings.get("ACOUSTID_API_KEY"))

    def test_mp3_folders_normalize_validate_and_allow_empty(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            settings.save({"MP3_FOLDERS_JSON": [first, os.path.join(first, "."), second]})
            folders = settings.configured_mp3_folders()
            self.assertEqual(folders, [os.path.normcase(os.path.realpath(first)),
                                       os.path.normcase(os.path.realpath(second))])
            settings.save({"MP3_FOLDERS_JSON": []})
            self.assertEqual(settings.configured_mp3_folders(), [])

    def test_mp3_folder_invalid_save_is_atomic_and_env_wins(self):
        with tempfile.TemporaryDirectory() as good:
            settings.save({"MP3_FOLDERS_JSON": [good]})
            before = settings.load_env()["MP3_FOLDERS_JSON"]
            with self.assertRaises(ValueError):
                settings.save({"MP3_FOLDERS_JSON": [os.path.join(good, "missing")]})
            self.assertEqual(settings.load_env()["MP3_FOLDERS_JSON"], before)
            with tempfile.TemporaryDirectory() as override:
                os.environ["MP3_FOLDERS_JSON"] = str([override]).replace("'", '"')
                self.assertEqual(settings.configured_mp3_folders(),
                                 [os.path.normcase(os.path.realpath(override))])
                with self.assertRaises(ValueError):
                    settings.save({"MP3_FOLDERS_JSON": [good]})

    def test_missing_folder_setting_is_empty_and_env_file_is_editable_after_restart(self):
        settings.save({"MP3_FOLDERS_JSON": ""})
        self.assertEqual(settings.configured_mp3_folders(), [])
        with tempfile.TemporaryDirectory() as folder:
            settings.save({"MP3_FOLDERS_JSON": [folder]})
            os.environ.pop("MP3_FOLDERS_JSON", None)
            settings._APP_OWNED_ENV.clear()
            settings.apply_to_environ()
            with tempfile.TemporaryDirectory() as replacement:
                settings.save({"MP3_FOLDERS_JSON": [replacement]})
                self.assertEqual(
                    settings.configured_mp3_folders(),
                    [os.path.normcase(os.path.realpath(replacement))],
                )

    def test_preexisting_shell_folder_value_stays_external(self):
        with tempfile.TemporaryDirectory() as shell_folder, tempfile.TemporaryDirectory() as other:
            os.environ["MP3_FOLDERS_JSON"] = str([shell_folder]).replace("'", '"')
            settings.apply_to_environ()
            with self.assertRaises(ValueError):
                settings.save({"MP3_FOLDERS_JSON": [other]})


class TestDiscordService(unittest.TestCase):
    def setUp(self):
        # newest-first raw messages, as the Discord API returns them
        self._fake_raw = [
            {"author": {"username": "bob", "global_name": "Bob"},
             "content": "later https://youtu.be/oHg5SJYRHA0", "embeds": []},
            {"author": {"username": "lin", "global_name": "Lin"},
             "content": "first https://www.youtube.com/watch?v=dQw4w9WgXcQ",
             "embeds": [{"url": "https://youtu.be/abcdefghijk", "description": ""}]},
        ]
        self._orig = discord_fetch.fetch_channel
        discord_fetch.fetch_channel = lambda cid, token: list(self._fake_raw)

    def tearDown(self):
        discord_fetch.fetch_channel = self._orig

    def test_extract_chronological_and_embeds(self):
        out = discord_service.fetch_and_extract("123", token="x", write_files=False)
        # chronological: Lin's message (with embed) first, then Bob's
        self.assertEqual(out["ids"], ["dQw4w9WgXcQ", "abcdefghijk", "oHg5SJYRHA0"])
        self.assertEqual(out["count"], 3)
        self.assertEqual(out["messages"], 2)

    def test_author_filter(self):
        out = discord_service.fetch_and_extract("123", token="x", author="Bob", write_files=False)
        self.assertEqual(out["ids"], ["oHg5SJYRHA0"])

    def test_missing_token_raises(self):
        os.environ.pop("DISCORD_BOT_TOKEN", None)
        with self.assertRaises(ValueError):
            discord_service.fetch_and_extract("123", token=None, write_files=False)


class TestJobs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_root = jobs.REPO_ROOT
        self.old_script = jobs.SCRIPTS.get("job_test")
        jobs.REPO_ROOT = self.tmp.name
        path = os.path.join(self.tmp.name, "job_test.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write("import time\nprint('started', flush=True)\ntime.sleep(30)\n")
        jobs.SCRIPTS["job_test"] = ("job_test.py", "test", False)
        with open(os.path.join(self.tmp.name, "tree_test.py"), "w", encoding="utf-8") as f:
            f.write(
                "import os, pathlib, signal, subprocess, sys, time\n"
                "child = subprocess.Popen([sys.executable, '-c', \"import os,signal,time; os.name == 'nt' and signal.signal(signal.SIGBREAK, signal.SIG_IGN); time.sleep(30)\"])\n"
                "pathlib.Path('tree_child.pid').write_text(str(child.pid))\n"
                "if os.name != 'nt': signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "else: signal.signal(signal.SIGBREAK, signal.SIG_IGN)\n"
                "while True: time.sleep(1)\n"
            )
        jobs.SCRIPTS["tree_test"] = ("tree_test.py", "tree test", False)
        jobs._jobs.pop("job_test", None)
        jobs._jobs.pop("tree_test", None)

    def tearDown(self):
        for name in ("job_test", "tree_test"):
            if jobs.state(name)["status"] in ("running", "stopping", "finalizing"):
                jobs.stop(name)
        if self.old_script is None:
            jobs.SCRIPTS.pop("job_test", None)
        else:
            jobs.SCRIPTS["job_test"] = self.old_script
        jobs.REPO_ROOT = self.old_root
        jobs._jobs.pop("job_test", None)
        jobs._jobs.pop("tree_test", None)
        jobs.SCRIPTS.pop("tree_test", None)
        self.tmp.cleanup()

    def test_catalog_marks_destructive(self):
        cat = {c["name"]: c for c in jobs.catalog()}
        self.assertTrue(cat["cleanup_tracked"]["destructive"])
        self.assertTrue(cat["cleanup_downloads"]["destructive"])
        self.assertFalse(cat["downloader"]["destructive"])
        self.assertEqual(cat["cleanup_tracked"]["curation"], "reader")
        self.assertEqual(cat["searcher"]["curation"], "writer")
        self.assertIn("ids.txt", cat["url_extractor"]["desc"])
        self.assertEqual(
            jobs.ARTIFACTS["downloader"][0][1], "completed download IDs"
        )
        self.assertEqual(
            jobs.ARTIFACTS["cleanup_downloads"][0][1], "completed download IDs"
        )

    def test_state_idle_for_unstarted(self):
        st = jobs.state("searcher")
        self.assertEqual(st["status"], "idle")
        self.assertEqual(st["lines"], [])

    def test_global_reservation_and_stop_completion(self):
        jobs.start("job_test")
        for _ in range(50):
            if jobs.state("job_test")["status"] == "running":
                break
            time.sleep(.01)
        with self.assertRaises(RuntimeError):
            jobs.start("searcher")
        stopped = jobs.stop("job_test")
        self.assertEqual(stopped["status"], "stopped")
        jobs.start("job_test")
        jobs.stop("job_test")

    def test_finalize_hook_runs_before_done(self):
        seen = []
        jobs.start("job_test", finalize=lambda name: seen.append(jobs.state(name)["status"]))
        jobs.stop("job_test")
        self.assertEqual(seen, ["finalizing"])

    def test_curation_lease_spans_prepare_and_finalize(self):
        events = []
        jobs.start(
            "job_test", curation=True,
            prepare=lambda name: events.append(("prepare", jobs.curation_active())),
            finalize=lambda name: events.append(("finalize", jobs.curation_active())),
        )
        jobs.stop("job_test")
        self.assertEqual(events, [("prepare", True), ("finalize", True)])
        self.assertFalse(jobs.curation_active())

    def test_prepare_failure_skips_finalizer_and_releases(self):
        finalized = []

        def fail_prepare(name):
            raise ValueError("prepare failed")

        jobs.start(
            "job_test", curation=True, prepare=fail_prepare,
            finalize=lambda name: finalized.append(name),
        )
        self.assertEqual(jobs.state("job_test")["status"], "failed")
        self.assertEqual(finalized, [])
        self.assertFalse(jobs.curation_active())

    def test_popen_failure_still_finalizes_and_releases(self):
        seen = []
        with mock.patch.object(jobs.subprocess, "Popen", side_effect=OSError("no launch")):
            jobs.start("job_test", finalize=lambda name: seen.append(jobs.state(name)["status"]))
            jobs._jobs["job_test"].done.wait(1)
        self.assertEqual(seen, ["finalizing"])
        self.assertEqual(jobs.state("job_test")["status"], "failed")
        jobs.start("job_test")
        jobs.stop("job_test")

    def test_aborted_run_finalizes_after_exit_and_releases(self):
        seen = []
        jobs.start("job_test", finalize=lambda name: seen.append(jobs.state(name)["status"]))
        for _ in range(100):
            if jobs._jobs["job_test"].proc is not None:
                break
            time.sleep(.01)
        jobs._jobs["job_test"].lifecycle_aborted = True
        stopped = jobs.stop("job_test")
        self.assertEqual(stopped["status"], "failed")
        self.assertEqual(seen, ["finalizing"])
        jobs.start("job_test")
        jobs.stop("job_test")

    def test_forced_stop_kills_spawned_child(self):
        jobs.start("tree_test")
        child_path = os.path.join(self.tmp.name, "tree_child.pid")
        parent_proc = None
        for _ in range(100):
            parent_proc = jobs._jobs["tree_test"].proc
            if os.path.exists(child_path) and parent_proc is not None:
                break
            time.sleep(.01)
        self.assertTrue(os.path.exists(child_path), jobs.state("tree_test"))
        self.assertIsNotNone(parent_proc)
        parent_pid = parent_proc.pid
        with open(child_path, encoding="utf-8") as f:
            child_pid = int(f.read())
        stopped = jobs.stop("tree_test")
        self.assertEqual(stopped["status"], "stopped")
        if os.name == "nt":
            tasklist = subprocess.run(
                ["tasklist", "/FI", f"PID eq {child_pid}"],
                capture_output=True, text=True, check=False,
            ).stdout
            self.assertNotIn(str(child_pid), tasklist)
            tasklist = subprocess.run(
                ["tasklist", "/FI", f"PID eq {parent_pid}"],
                capture_output=True, text=True, check=False,
            ).stdout
            self.assertNotIn(str(parent_pid), tasklist)
        else:
            for pid in (child_pid, parent_pid):
                with self.assertRaises(OSError):
                    os.kill(pid, 0)
        self.assertIsNone(jobs._jobs["tree_test"].proc)
        self.assertNotEqual(parent_pid, child_pid)


if __name__ == "__main__":
    unittest.main()
