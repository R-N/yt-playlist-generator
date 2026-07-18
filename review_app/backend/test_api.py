"""
API-layer tests (FastAPI TestClient). Requires: fastapi, httpx.

    python -m unittest test_api -v

Redirects db paths AND main.MP3_FOLDERS to a temp dir, so no real data or
music files are touched. TestClient's context manager fires startup (which
seeds the DB and builds the read-only file index).
"""
import json
import os
import tempfile
import types
import unittest
from unittest import mock

import pandas as pd
from fastapi.testclient import TestClient

import db
import main
import jobs
import settings


def write_matches(path, rows):
    df = pd.DataFrame(rows)
    if path.lower().endswith(".xlsx"):
        df.to_excel(path, index=False)
    else:
        df.to_csv(path, index=False)


class ApiTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self._orig_settings_env_path = settings.ENV_PATH
        self._orig_folders_env = os.environ.get("MP3_FOLDERS_JSON")
        settings.ENV_PATH = os.path.join(d, ".env")
        self._orig_db = {k: getattr(db, k) for k in
                         ("DB_PATH", "MATCHES_CSV", "MATCHES_XLSX",
                          "MATCHES_SOURCE", "BACKUP_DIR")}
        self._orig_main = {k: getattr(main, k) for k in
                           ("MP3_FOLDERS", "AUTO_EXPORT_EVERY", "_CATALOG",
                            "_decision_count")}
        db.DB_PATH = os.path.join(d, "review.db")
        db.MATCHES_CSV = os.path.join(d, "matches.csv")
        db.MATCHES_XLSX = os.path.join(d, "matches.xlsx")
        db.MATCHES_SOURCE = db.MATCHES_XLSX
        db.BACKUP_DIR = os.path.join(d, "backups")

        music = os.path.join(d, "music")
        os.makedirs(music)
        # A.mp3 has a real file; B.mp3 is in the DB but has no file on disk
        self.audio_bytes = b"ID3" + b"\x00" * 100
        with open(os.path.join(music, "A.mp3"), "wb") as f:
            f.write(self.audio_bytes)

        seed = [
            {"filename": "A.mp3", "yt_id": "a", "check": None},
            {"filename": "B.mp3", "yt_id": "b", "check": None},
        ]
        write_matches(db.MATCHES_CSV, seed)
        write_matches(db.MATCHES_XLSX, seed)

        main.MP3_FOLDERS = [music]
        main.AUTO_EXPORT_EVERY = 1000   # effectively off unless a test lowers it
        main._CATALOG = main.FileCatalog((), {})
        main._decision_count = 0
        settings.save({"MP3_FOLDERS_JSON": [music]})

        self.client = TestClient(main.app)
        self.client.__enter__()         # trigger startup
        self.ids = {r["filename"]: r["id"]
                    for r in self.client.get("/api/rows?status=all").json()["rows"]}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        for k, v in self._orig_db.items():
            setattr(db, k, v)
        for k, v in self._orig_main.items():
            setattr(main, k, v)
        settings.ENV_PATH = self._orig_settings_env_path
        if self._orig_folders_env is None:
            os.environ.pop("MP3_FOLDERS_JSON", None)
        else:
            os.environ["MP3_FOLDERS_JSON"] = self._orig_folders_env
        settings._APP_OWNED_ENV.pop("MP3_FOLDERS_JSON", None)
        self.tmp.cleanup()


class EndpointTest(ApiTestBase):
    def test_cleanup_preview_empty_when_downloads_root_missing_or_file(self):
        original_root = main.REPO_ROOT
        root = tempfile.TemporaryDirectory()
        main.REPO_ROOT = root.name
        try:
            self.assertEqual(main._cleanup_download_targets(), [])
            downloads = os.path.join(root.name, "downloads")
            with open(downloads, "w", encoding="utf-8") as stream:
                stream.write("not a directory")
            self.assertEqual(main._cleanup_download_targets(), [])
        finally:
            main.REPO_ROOT = original_root
            root.cleanup()

    def test_cleanup_preview_then_root_removed_rejects_manifest(self):
        original_root = main.REPO_ROOT
        root = tempfile.TemporaryDirectory()
        main.REPO_ROOT = root.name
        downloads = os.path.join(root.name, "downloads")
        os.makedirs(downloads)
        target = os.path.join(downloads, "clip.mp4")
        with open(target, "wb") as stream:
            stream.write(b"clip")
        try:
            preview = self.client.post("/api/settings/cleanup-downloads/preview").json()
            import shutil
            shutil.rmtree(downloads)
            response = self.client.post("/api/settings/cleanup-downloads", json={
                "token": preview["token"], "confirm": "DELETE"})
            self.assertEqual(response.status_code, 409)
            self.assertTrue(all(row["outcome"] == "rejected"
                                for row in self.client.get("/api/library/delete/audit").json()["audit"]))
        finally:
            main.REPO_ROOT = original_root
            root.cleanup()

    def test_library_delete_token_and_audit_preserve_track_decision(self):
        self.assertEqual(self.client.post("/api/decision", json={
            "track_id": self.ids["A.mp3"], "decision": True}).status_code, 200)
        preview = self.client.post("/api/library/delete/preview", json={
            "track_ids": [self.ids["A.mp3"]]}).json()
        self.assertEqual(self.client.post("/api/library/delete", json={
            "track_ids": [self.ids["A.mp3"]], "token": preview["token"],
            "confirm": "DELETE"}).status_code, 200)
        self.assertFalse(os.path.exists(os.path.join(main.MP3_FOLDERS[0], "A.mp3")))
        self.assertEqual(self.client.get(f"/api/track/{self.ids['A.mp3']}").json()["check"], 1)
        self.assertEqual(self.client.get("/api/library/delete/audit").json()["audit"][0]["outcome"], "deleted")

    def test_library_delete_rejects_confirmation_and_token_reuse(self):
        self.client.post("/api/decision", json={
            "track_id": self.ids["A.mp3"], "decision": True})
        preview = self.client.post("/api/library/delete/preview", json={
            "track_ids": [self.ids["A.mp3"]]}).json()
        bad = self.client.post("/api/library/delete", json={
            "track_ids": [self.ids["A.mp3"]], "token": preview["token"], "confirm": "delete"})
        self.assertEqual(bad.status_code, 400)
        good = self.client.post("/api/library/delete", json={
            "track_ids": [self.ids["A.mp3"]], "token": preview["token"], "confirm": "DELETE"})
        self.assertEqual(good.status_code, 200)
        reused = self.client.post("/api/library/delete", json={
            "track_ids": [self.ids["A.mp3"]], "token": preview["token"], "confirm": "DELETE"})
        self.assertEqual(reused.status_code, 409)

    def test_cleanup_downloads_requires_dedicated_route_and_coordinator(self):
        preview = self.client.post("/api/settings/cleanup-downloads/preview").json()
        generic = self.client.post("/api/scripts/cleanup_downloads/run")
        self.assertEqual(generic.status_code, 409)
        jobs.reserve_pipeline("phase7-block")
        try:
            response = self.client.post("/api/settings/cleanup-downloads", json={
                "token": preview["token"], "confirm": "DELETE"})
            self.assertEqual(response.status_code, 409)
        finally:
            jobs.release_pipeline("phase7-block")

    def test_local_files_and_live_untracked_ignore_text_artifact(self):
        local = self.client.get("/api/local-files").json()
        self.assertEqual(local["total"], 1)
        self.assertEqual(local["files"][0]["category"], "unreviewed")
        self.assertNotIn("path", local["files"][0])
        with open(os.path.join(os.path.dirname(db.DB_PATH), "untracked.txt"), "w") as f:
            f.write("not-a-current-file\n")
        untracked = self.client.get("/api/untracked").json()
        self.assertEqual(untracked["total"], 1)
        self.assertEqual(untracked["files"][0]["basename"], "A.mp3")

    def test_saved_link_explicit_exact_match_and_conflicts(self):
        item = self.client.post("/api/workspace/import", json={
            "text": "match123456",
        }).json()["added"][0]
        self.client.post("/api/workspace/save-links", json={"ids": [item["id"]]})
        saved = self.client.get("/api/saved-links").json()["links"][0]
        local = self.client.get("/api/local-files").json()["files"][0]
        payload = {"saved_link_id": saved["id"], "track_id": self.ids["A.mp3"],
                   "folder_identity": local["folder_identity"],
                   "relative_path": local["relative_path"]}
        self.assertEqual(self.client.post("/api/saved-links/match", json=payload).status_code, 200)
        self.assertEqual(self.client.get(f"/api/track/{self.ids['A.mp3']}").json()["yt_id"],
                         "match123456")
        self.assertEqual(self.client.post("/api/decision", json={
            "track_id": self.ids["A.mp3"], "decision": True}).json()["check"], 1)
        conflict = dict(payload, track_id=self.ids["B.mp3"])
        self.assertEqual(self.client.post("/api/saved-links/match", json=conflict).status_code, 409)

    def test_saved_link_match_respects_curation_lease(self):
        item = self.client.post("/api/workspace/import", json={
            "text": "lease123456",
        }).json()["added"][0]
        self.client.post("/api/workspace/save-links", json={"ids": [item["id"]]})
        saved = self.client.get("/api/saved-links").json()["links"][0]
        local = self.client.get("/api/local-files").json()["files"][0]
        jobs._curation_lease = "phase6-test"
        try:
            response = self.client.post("/api/saved-links/match", json={
                "saved_link_id": saved["id"], "track_id": self.ids["A.mp3"],
                "folder_identity": local["folder_identity"],
                "relative_path": local["relative_path"]})
            self.assertEqual(response.status_code, 409)
        finally:
            jobs._curation_lease = None

    def test_counts(self):
        c = self.client.get("/api/counts").json()
        self.assertEqual(c, {"total": 2, "unreviewed": 2, "approved": 0, "rejected": 0})

    def test_rows_status_filter(self):
        un = self.client.get("/api/rows?status=unreviewed").json()
        self.assertEqual(un["total"], 2)
        self.assertIn("has_local", un["rows"][0])   # endpoint annotates file presence

    def test_decision_updates_counts(self):
        r = self.client.post("/api/decision",
                             json={"track_id": self.ids["A.mp3"], "decision": True})
        self.assertEqual(r.json()["check"], 1)
        self.assertEqual(self.client.get("/api/counts").json()["approved"], 1)

    def test_decision_unknown_track_404(self):
        r = self.client.post("/api/decision",
                             json={"track_id": 999999, "decision": True})
        self.assertEqual(r.status_code, 404)


class WorkspaceApiTest(ApiTestBase):
    def setUp(self):
        super().setUp()
        self.shared_id = "shared12345"
        conn = db.connect()
        try:
            conn.execute("UPDATE tracks SET yt_id = ? WHERE filename IN ('A.mp3', 'B.mp3')",
                         (self.shared_id,))
            conn.commit()
        finally:
            conn.close()

    def test_import_reports_invalid_duplicates_and_preserves_order(self):
        first = "first123456"  # 11 chars
        second = "second12345"  # 11 chars
        response = self.client.post("/api/workspace/import", json={
            "text": "https://youtu.be/" + first + "\n" + first +
                    "\nnot-a-link\nhttps://www.youtube.com/watch?v=" + second,
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["added"]), 2)
        self.assertEqual(len(body["duplicates"]), 1)
        self.assertEqual(len(body["invalid"]), 1)
        items = self.client.get("/api/workspace").json()["items"]
        self.assertEqual([i["youtube_id"] for i in items], [first, second])
        self.assertEqual(items[0]["youtube_url"], "https://www.youtube.com/watch?v=" + first)

    def test_reorder_remove_and_library_promotion_preserve_decisions(self):
        imported = self.client.post("/api/workspace/import", json={
            "text": "first123456\nsecond12345",
        }).json()["added"]
        ids = [item["id"] for item in imported]
        reordered = self.client.post("/api/workspace/reorder", json={"ids": ids[::-1]})
        self.assertEqual([i["id"] for i in reordered.json()["items"]], ids[::-1])
        self.assertEqual(self.client.request(
            "DELETE", "/api/workspace", json={"ids": [ids[0]]}
        ).status_code, 200)

        decision = self.client.post("/api/decision", json={
            "track_id": self.ids["A.mp3"], "decision": True,
        })
        self.assertEqual(decision.status_code, 200)
        promoted_a = self.client.post("/api/workspace/library", json={
            "track_id": self.ids["A.mp3"],
        })
        promoted_b = self.client.post("/api/workspace/library", json={
            "track_id": self.ids["B.mp3"],
        })
        self.assertEqual(promoted_a.status_code, 200)
        self.assertEqual(promoted_b.status_code, 200)
        items = self.client.get("/api/workspace").json()["items"]
        library_items = [i for i in items if i["track_id"] is not None]
        self.assertEqual({i["track_id"] for i in library_items},
                         {self.ids["A.mp3"], self.ids["B.mp3"]})
        self.assertEqual(self.client.get("/api/counts").json()["approved"], 1)

    def test_saved_links_are_idempotent_and_keep_library_relation(self):
        item = self.client.post("/api/workspace/library", json={
            "track_id": self.ids["A.mp3"],
        }).json()["item"]
        first = self.client.post("/api/workspace/save-links", json={"ids": [item["id"]]})
        second = self.client.post("/api/workspace/save-links", json={"ids": [item["id"]]})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(second.json()["links"]), 1)
        self.assertEqual(second.json()["links"][0]["track_id"], self.ids["A.mp3"])

    def test_changed_track_id_promotion_merges_generic_and_rereads(self):
        promoted = self.client.post("/api/workspace/library", json={
            "track_id": self.ids["A.mp3"],
        })
        self.assertEqual(promoted.status_code, 200)
        new_id = "changed1234"
        conn = db.connect()
        try:
            conn.execute("UPDATE tracks SET yt_id = ? WHERE id = ?",
                         (new_id, self.ids["A.mp3"]))
            conn.commit()
        finally:
            conn.close()
        generic = self.client.post("/api/workspace/import", json={
            "text": new_id,
        }).json()["added"][0]
        updated = self.client.post("/api/workspace/library", json={
            "track_id": self.ids["A.mp3"],
        })
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["item"]["youtube_id"], new_id)
        self.assertEqual(updated.json()["item"]["id"], promoted.json()["item"]["id"])
        self.assertNotIn(generic, [i["id"] for i in self.client.get("/api/workspace").json()["items"]])

    def test_saved_link_relation_conflict_and_active_curation_conflict(self):
        a = self.client.post("/api/workspace/library", json={"track_id": self.ids["A.mp3"]}).json()["item"]
        self.client.post("/api/workspace/save-links", json={"ids": [a["id"]]})
        b = self.client.post("/api/workspace/library", json={"track_id": self.ids["B.mp3"]}).json()["item"]
        conflict = self.client.post("/api/workspace/save-links", json={"ids": [b["id"]]})
        self.assertEqual(conflict.status_code, 409)
        jobs.reserve_pipeline("workspace-test")
        with jobs._coordinator_lock:
            jobs._curation_lease = "workspace-test"
        try:
            self.assertEqual(self.client.post("/api/workspace/library", json={
                "track_id": self.ids["A.mp3"],
            }).status_code, 409)
            self.assertEqual(self.client.post("/api/workspace/save-links", json={
                "ids": [a["id"]],
            }).status_code, 409)
        finally:
            with jobs._coordinator_lock:
                jobs._curation_lease = None
            jobs.release_pipeline("workspace-test")

    def test_playlist_batches_include_50_and_tail_in_order(self):
        ids = [f"{number:011d}" for number in range(51)]
        added = self.client.post("/api/workspace/import", json={
            "text": "\n".join(ids),
        }).json()["added"]
        response = self.client.post("/api/workspace/playlists", json={
            "ids": [item["id"] for item in added],
        })
        self.assertEqual(response.status_code, 200)
        batches = response.json()["batches"]
        self.assertEqual([batch["count"] for batch in batches], [50, 1])
        self.assertEqual(batches[0]["youtube_ids"], ids[:50])
        self.assertEqual(batches[1]["youtube_ids"], ids[50:])
        self.assertTrue(batches[1]["playlist_url"].endswith(ids[50]))

    def test_selection_duplicate_youtube_reporting_and_stream_formats(self):
        a = self.client.post("/api/workspace/library", json={"track_id": self.ids["A.mp3"]}).json()["item"]
        b = self.client.post("/api/workspace/library", json={"track_id": self.ids["B.mp3"]}).json()["item"]
        selection = self.client.post("/api/workspace/selection", json={
            "ids": [b["id"], a["id"]],
        }).json()
        self.assertEqual([item["id"] for item in selection["items"]], [b["id"], a["id"]])
        self.assertEqual(selection["skipped_duplicate_item_ids"], [a["id"]])
        conn = db.connect()
        try:
            conn.execute("UPDATE workspace_items SET title = ?, channel = ? WHERE id = ?",
                         ("=SUM(1,1)", '+cmd "quoted"', b["id"]))
            conn.execute("UPDATE workspace_items SET provenance = ? WHERE id = ?",
                         ("@source", b["id"]))
            conn.commit()
        finally:
            conn.close()
        for format_name, filename in (("ids", "workspace-ids.txt"),
                                      ("urls", "workspace-urls.txt"),
                                      ("playlists", "workspace-playlists.txt"),
                                      ("csv", "workspace.csv")):
            response = self.client.post(f"/api/workspace/download/{format_name}", json={
                "ids": [b["id"], a["id"]],
            })
            self.assertEqual(response.status_code, 200)
            self.assertIn(filename, response.headers["content-disposition"])
            self.assertIn("charset=utf-8", response.headers["content-type"])
            self.assertEqual(response.headers["x-workspace-skipped-duplicate-count"], "1")
            self.assertNotIn("x-workspace-skipped-duplicate-item-ids", response.headers)
            if format_name == "csv":
                self.assertIn("'=SUM(1,1)", response.text)
                self.assertIn("'+cmd", response.text)
                self.assertIn("'@source", response.text)
                self.assertIn("workspace_item_id,youtube_id,youtube_url,title,channel,provenance,track_id", response.text)
        root_files = [os.path.join(main.REPO_ROOT, name)
                      for name in ("ids.txt", "urls.txt", "playlists.txt")]
        before = {path: (os.path.exists(path), os.path.getmtime(path) if os.path.exists(path) else None)
                  for path in root_files}
        self.client.post("/api/workspace/download/ids", json={"ids": [b["id"]]})
        after = {path: (os.path.exists(path), os.path.getmtime(path) if os.path.exists(path) else None)
                 for path in root_files}
        self.assertEqual(before, after)

    def test_selection_rejects_empty_duplicate_and_missing_ids(self):
        for ids, status in (([], 400), ([1, 1], 400), ([999999], 404)):
            response = self.client.post("/api/workspace/selection", json={"ids": ids})
            self.assertEqual(response.status_code, status)

    def test_selection_rejects_non_strict_ids_and_supports_large_order(self):
        for value in (True, 1.0, "1", 0, -1):
            response = self.client.post("/api/workspace/selection", json={"ids": [value]})
            self.assertEqual(response.status_code, 422)
        ids = [f"{number:011d}" for number in range(901)]
        added = self.client.post("/api/workspace/import", json={"text": "\n".join(ids)}).json()["added"]
        response = self.client.post("/api/workspace/selection", json={
            "ids": [item["id"] for item in added],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()["items"]],
                         [item["id"] for item in added])

    def test_download_run_snapshots_private_input_and_finalizes(self):
        item = self.client.post("/api/workspace/import", json={
            "text": "run12345678",
        }).json()["added"][0]
        old_storage = main.RUN_STORAGE
        storage = tempfile.TemporaryDirectory()
        main.RUN_STORAGE = storage.name
        captured = {}
        original_start = jobs.start
        original_result = jobs.finalization_result

        def fake_start(name, **kwargs):
            captured.update(kwargs)
            return {"name": name, "status": "running"}

        jobs.start = fake_start
        jobs.finalization_result = lambda *args: {"returncode": 0, "stopped": False}
        root_paths = [os.path.join(main.REPO_ROOT, name)
                      for name in ("ids.txt", "urls.txt", "playlists.txt")]
        before = {path: (os.path.exists(path), os.path.getmtime(path) if os.path.exists(path) else None)
                  for path in root_paths}
        try:
            response = self.client.post("/api/workspace/runs/download", json={"ids": [item["id"]]})
            self.assertEqual(response.status_code, 200)
            run_id = response.json()["id"]
            self.assertEqual(response.json()["status"], "running")
            self.assertIsNotNone(response.json()["started_at"])
            input_path = captured["env_overrides"]["YT_INPUT_FILE"]
            self.assertTrue(os.path.isfile(input_path))
            self.assertNotIn("ids.txt", input_path)
            self.assertEqual(captured["reservation_name"], "workspace_download")
            captured["finalize"]("downloader")
            history = self.client.get(f"/api/workspace/runs/{run_id}").json()
            self.assertEqual(history["status"], "done")
            self.assertNotIn("job", history)
            self.assertFalse(os.path.exists(input_path))
            after = {path: (os.path.exists(path), os.path.getmtime(path) if os.path.exists(path) else None)
                     for path in root_paths}
            self.assertEqual(before, after)
        finally:
            jobs.start = original_start
            jobs.finalization_result = original_result
            jobs.release_pipeline("workspace_download")
            main.RUN_STORAGE = old_storage
            storage.cleanup()

    def test_download_run_coordinator_conflict(self):
        item = self.client.post("/api/workspace/import", json={
            "text": "conflict123",
        }).json()["added"][0]
        jobs.reserve_pipeline("other-pipeline")
        try:
            response = self.client.post("/api/workspace/runs/download", json={"ids": [item["id"]]})
            self.assertEqual(response.status_code, 409)
        finally:
            jobs.release_pipeline("other-pipeline")

    def test_startup_cleanup_is_contained_to_run_storage(self):
        old_storage = main.RUN_STORAGE
        storage = tempfile.TemporaryDirectory()
        outside = tempfile.NamedTemporaryFile(delete=False)
        outside.close()
        main.RUN_STORAGE = storage.name
        try:
            inside = os.path.join(storage.name, "run-1-safe.ids")
            with open(inside, "w", encoding="utf-8") as stream:
                stream.write("safe\n")
            run_id = db.create_workspace_run("download", inside, "test", [])
            conn = db.connect()
            try:
                conn.execute("UPDATE workspace_runs SET input_path=?, status='running' WHERE id=?",
                             (inside, run_id))
                conn.commit()
            finally:
                conn.close()
            outside_run = db.create_workspace_run("download", outside.name, "test", [])
            conn = db.connect()
            try:
                conn.execute("UPDATE workspace_runs SET input_path=?, status='running' WHERE id=?",
                             (outside.name, outside_run))
                conn.commit()
            finally:
                conn.close()
            db.init_db()
            main._cleanup_workspace_run_files()
            self.assertFalse(os.path.exists(inside))
            self.assertTrue(os.path.exists(outside.name))
        finally:
            main.RUN_STORAGE = old_storage
            try:
                os.remove(outside.name)
            except OSError:
                pass
            storage.cleanup()


class CurationLeaseApiTest(ApiTestBase):
    def setUp(self):
        super().setUp()
        with jobs._coordinator_lock:
            jobs._curation_lease = "test-pipeline"

    def tearDown(self):
        with jobs._coordinator_lock:
            jobs._curation_lease = None
        super().tearDown()

    def test_decision_and_export_rejected_during_pipeline_lease(self):
        decision = self.client.post(
            "/api/decision",
            json={"track_id": self.ids["A.mp3"], "decision": True},
        )
        self.assertEqual(decision.status_code, 409)
        export = self.client.post("/api/export")
        self.assertEqual(export.status_code, 409)



class FolderCatalogApiTest(ApiTestBase):
    def setUp(self):
        self._settings_tmp = tempfile.TemporaryDirectory()
        self._settings_env = settings.ENV_PATH
        self._folders_env = os.environ.get("MP3_FOLDERS_JSON")
        settings.ENV_PATH = os.path.join(self._settings_tmp.name, ".env")
        super().setUp()
        self.other = tempfile.TemporaryDirectory()
        with open(os.path.join(self.other.name, "A.mp3"), "wb") as f:
            f.write(self.audio_bytes)

    def tearDown(self):
        super().tearDown()
        settings.ENV_PATH = self._settings_env
        if self._folders_env is None:
            os.environ.pop("MP3_FOLDERS_JSON", None)
        else:
            os.environ["MP3_FOLDERS_JSON"] = self._folders_env
        self.other.cleanup()
        self._settings_tmp.cleanup()

    def test_rescan_empty_and_duplicate_basename_safety(self):
        folder = main.MP3_FOLDERS[0]
        r = self.client.post("/api/settings", json={
            "MP3_FOLDERS_JSON": [folder, self.other.name],
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(main._CATALOG.by_basename[os.path.normcase("A.mp3")]), 2)
        self.assertEqual(self.client.get(f"/api/audio/{self.ids['A.mp3']}").status_code, 404)

        empty = self.client.post("/api/settings", json={"MP3_FOLDERS_JSON": []})
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(self.client.get(f"/api/audio/{self.ids['A.mp3']}").status_code, 404)
        rows = self.client.get("/api/rows?status=all").json()["rows"]
        self.assertFalse(next(r for r in rows if r["filename"] == "A.mp3")["has_local"])

    def test_folder_save_conflict_and_failure_keep_catalog(self):
        before = main._CATALOG
        jobs.reserve_pipeline("test-folder-conflict")
        try:
            blocked = self.client.post("/api/settings", json={"MP3_FOLDERS_JSON": []})
            self.assertEqual(blocked.status_code, 409)
        finally:
            jobs.release_pipeline("test-folder-conflict")
        failed = self.client.post("/api/settings", json={
            "MP3_FOLDERS_JSON": [os.path.join(self.other.name, "missing")],
        })
        self.assertEqual(failed.status_code, 400)
        self.assertIs(main._CATALOG, before)

    def test_audio_rejects_catalog_path_outside_folder(self):
        original = main._CATALOG
        record = dict(original.by_basename[os.path.normcase("A.mp3")][0])
        record["path"] = os.path.join(self.other.name, "outside.mp3")
        main._swap_file_catalog(main.FileCatalog(
            (record,), {os.path.normcase("A.mp3"): (record,)}))
        try:
            self.assertEqual(self.client.get(f"/api/audio/{self.ids['A.mp3']}").status_code, 404)
        finally:
            main._swap_file_catalog(original)

    def test_symlinks_are_skipped_and_physical_files_deduped(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            real = os.path.join(root, "real.mp3")
            with open(real, "wb") as f:
                f.write(b"audio")
            os.link(real, os.path.join(root, "hardlink.mp3"))
            outside_file = os.path.join(outside, "outside.mp3")
            with open(outside_file, "wb") as f:
                f.write(b"outside")
            try:
                os.symlink(outside_file, os.path.join(root, "inside-link.mp3"))
                os.symlink(real, os.path.join(outside, "outside-link.mp3"))
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation unavailable")
            catalog = main._build_file_catalog([root, outside])
            self.assertEqual(len(catalog.records), 2)
            self.assertNotIn("inside-link.mp3", catalog.by_basename)
            self.assertNotIn("outside-link.mp3", catalog.by_basename)

    def test_supported_audio_extensions_resolve_with_media_types(self):
        folder = main.MP3_FOLDERS[0]
        extensions = {"B.m4a": "audio/mp4", "C.opus": "audio/ogg",
                      "D.ogg": "audio/ogg", "E.flac": "audio/flac"}
        conn = db.connect()
        try:
            for name in extensions:
                with open(os.path.join(folder, name), "wb") as f:
                    f.write(b"audio")
                conn.execute("INSERT INTO tracks (filename) VALUES (?)", (name,))
            conn.commit()
            ids = {name: conn.execute(
                "SELECT id FROM tracks WHERE filename = ?", (name,)
            ).fetchone()[0] for name in extensions}
        finally:
            conn.close()
        main._swap_file_catalog(main._build_file_catalog([folder]))
        for name, media_type in extensions.items():
            response = self.client.get(f"/api/audio/{ids[name]}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["content-type"], media_type)

    def test_case_variant_basenames_use_normcase_ambiguity_index(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            for folder, name in ((first, "Song.mp3"), (second, "song.mp3")):
                with open(os.path.join(folder, name), "wb") as f:
                    f.write(b"audio")
            catalog = main._build_file_catalog([first, second])
            first_key = os.path.normcase("Song.mp3")
            second_key = os.path.normcase("song.mp3")
            if first_key == second_key:
                self.assertEqual(len(catalog.by_basename[first_key]), 2)
            else:
                self.assertEqual(len(catalog.by_basename[first_key]), 1)
                self.assertEqual(len(catalog.by_basename[second_key]), 1)


class WriterLifecycleApiTest(ApiTestBase):
    def test_invalid_post_run_csv_recovers_through_job_lifecycle(self):
        old_root = jobs.REPO_ROOT
        old_script = jobs.SCRIPTS.get("writer_test")
        jobs.REPO_ROOT = self.tmp.name
        with open(os.path.join(self.tmp.name, "writer_test.py"), "w", encoding="utf-8") as f:
            f.write("open('matches.csv', 'w', encoding='utf-8').write('not,a,valid,curation\\n')\n")
        jobs.SCRIPTS["writer_test"] = ("writer_test.py", "writer test", False)
        jobs.CURATION_WRITERS.add("writer_test")
        lease_seen = []

        def finalize(name):
            lease_seen.append(jobs.curation_active())
            main._finalize_pipeline(name)

        try:
            jobs.start(
                "writer_test", curation=True,
                prepare=main._prepare_pipeline, finalize=finalize,
            )
            job = jobs._jobs["writer_test"]
            self.assertTrue(job.done.wait(3))
            self.assertEqual(jobs.state("writer_test")["status"], "failed")
            self.assertEqual(lease_seen, [True])
            self.assertFalse(jobs.curation_active())
            restored = pd.read_csv(db.MATCHES_CSV)
            self.assertEqual(set(restored["filename"]), {"A.mp3", "B.mp3"})
        finally:
            if jobs.state("writer_test")["status"] in ("running", "stopping", "finalizing"):
                jobs.stop("writer_test")
            jobs.CURATION_WRITERS.discard("writer_test")
            jobs.SCRIPTS.pop("writer_test", None) if old_script is None else jobs.SCRIPTS.__setitem__("writer_test", old_script)
            jobs.REPO_ROOT = old_root


class DiscordPipelineGuardApiTest(ApiTestBase):
    def test_write_fetch_rejected_but_read_only_allowed(self):
        jobs.reserve_pipeline("discord_test")
        original = main.discord_service.fetch_and_extract
        main.discord_service.fetch_and_extract = lambda *args, **kwargs: {"written": []}
        try:
            blocked = self.client.post(
                "/api/discord/fetch",
                json={"channel_id": "123", "write_files": True},
            )
            self.assertEqual(blocked.status_code, 409)
            pipeline = self.client.post("/api/scripts/downloader/run")
            self.assertEqual(pipeline.status_code, 409)
            allowed = self.client.post(
                "/api/discord/fetch",
                json={"channel_id": "123", "write_files": False},
            )
            self.assertEqual(allowed.status_code, 200)
        finally:
            main.discord_service.fetch_and_extract = original
            jobs.release_pipeline("discord_test")


class AutoExportTest(ApiTestBase):
    def test_triggers_every_n(self):
        main.AUTO_EXPORT_EVERY = 2
        main._decision_count = 0
        r1 = self.client.post("/api/decision",
                             json={"track_id": self.ids["A.mp3"], "decision": True})
        self.assertNotIn("auto_exported", r1.json())     # 1st: no export yet
        r2 = self.client.post("/api/decision",
                             json={"track_id": self.ids["B.mp3"], "decision": True})
        self.assertTrue(r2.json().get("auto_exported"))   # 2nd: fired
        # and the marks were written to the csv
        out = pd.read_csv(db.MATCHES_CSV)
        self.assertEqual(int((out["check"] == 1).sum()), 2)


class AudioTest(ApiTestBase):
    def test_audio_ok_full(self):
        r = self.client.get(f"/api/audio/{self.ids['A.mp3']}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, self.audio_bytes)
        self.assertEqual(r.headers["content-type"], "audio/mpeg")

    def test_audio_range_206(self):
        # the scrubbing claim: FileResponse must honor Range
        r = self.client.get(f"/api/audio/{self.ids['A.mp3']}",
                            headers={"Range": "bytes=0-3"})
        self.assertEqual(r.status_code, 206)
        self.assertEqual(r.content, self.audio_bytes[:4])
        self.assertIn("content-range", r.headers)

    def test_audio_404_when_file_missing(self):
        # B.mp3 is in the DB but no file on disk -> 404, never a 500/leak
        r = self.client.get(f"/api/audio/{self.ids['B.mp3']}")
        self.assertEqual(r.status_code, 404)

    def test_audio_404_unknown_track(self):
        r = self.client.get("/api/audio/999999")
        self.assertEqual(r.status_code, 404)


class RowsNanApiTest(ApiTestBase):
    """End-to-end guard for the NaN-500 bug: a NaN in the DB must serialize through
    the real Starlette JSON encoder (allow_nan=False), not just db._expand_extra.
    This is the layer the unit test can't reach — it 500'd in production before."""

    def test_rows_serializes_with_nan_in_db(self):
        conn = db.connect()
        try:
            conn.execute(
                'UPDATE tracks SET score = ?, extra_json = ? WHERE filename = ?',
                (float("nan"), json.dumps({"ac_score": float("nan"), "mb_artist": "X"}), "A.mp3"),
            )
            conn.commit()
        finally:
            conn.close()
        r = self.client.get("/api/rows?status=all")
        self.assertEqual(r.status_code, 200)          # was 500 before the scrub
        row = next(x for x in r.json()["rows"] if x["filename"] == "A.mp3")
        self.assertIsNone(row["score"])
        self.assertIsNone(row["ac_score"])
        self.assertEqual(row["mb_artist"], "X")


class PlaylistApiTest(ApiTestBase):
    def test_generates_chunked_playlists_from_pasted_urls(self):
        # 51 ids -> 2 playlists (50 + 1); the tail must not be dropped
        lines = [f"https://www.youtube.com/watch?v={'a'*10}{i%10}" for i in range(51)]
        r = self.client.post("/api/playlists", json={"text": "\n".join(lines)})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["id_count"], 51)
        self.assertEqual(len(body["playlists"]), 2)
        self.assertTrue(body["playlists"][0].startswith(
            "https://www.youtube.com/watch_videos?video_ids="))

    def test_empty_input(self):
        r = self.client.post("/api/playlists", json={"text": "  \n \n"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"id_count": 0, "playlists": []})


class YtAudioTest(ApiTestBase):
    """The /api/yt_audio redirect. The resolver is stubbed so no test hits yt-dlp."""

    def test_invalid_id_rejected_without_calling_yt_dlp(self):
        r = self.client.get("/api/yt_audio/too-short")   # not 11 valid chars
        self.assertEqual(r.status_code, 400)

    def test_valid_id_redirects_to_resolved_url(self):
        orig = main._resolve_yt_audio
        main._resolve_yt_audio = lambda yid: "https://example.com/s?v=" + yid
        try:
            r = self.client.get("/api/yt_audio/dQw4w9WgXcQ", follow_redirects=False)
            self.assertIn(r.status_code, (302, 307))
            self.assertEqual(r.headers["location"], "https://example.com/s?v=dQw4w9WgXcQ")
        finally:
            main._resolve_yt_audio = orig

    def test_unresolvable_returns_502(self):
        orig = main._resolve_yt_audio
        main._resolve_yt_audio = lambda yid: None
        try:
            r = self.client.get("/api/yt_audio/dQw4w9WgXcQ")
            self.assertEqual(r.status_code, 502)
        finally:
            main._resolve_yt_audio = orig


class ResolveYtAudioTest(unittest.TestCase):
    """_resolve_yt_audio's subprocess handling (yt-dlp stubbed — no network)."""

    def _run(self, returncode, stdout):
        return types.SimpleNamespace(returncode=returncode, stdout=stdout)

    def test_returns_first_stdout_line(self):
        with mock.patch("main.subprocess.run",
                        return_value=self._run(0, "http://a/stream\nhttp://b\n")):
            self.assertEqual(main._resolve_yt_audio("dQw4w9WgXcQ"), "http://a/stream")

    def test_none_on_nonzero_returncode(self):
        with mock.patch("main.subprocess.run", return_value=self._run(1, "")):
            self.assertIsNone(main._resolve_yt_audio("dQw4w9WgXcQ"))

    def test_none_on_subprocess_error(self):
        with mock.patch("main.subprocess.run", side_effect=OSError("yt-dlp missing")):
            self.assertIsNone(main._resolve_yt_audio("dQw4w9WgXcQ"))

    def test_cmd_targets_the_requested_id(self):
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return self._run(0, "u\n")

        with mock.patch("main.subprocess.run", side_effect=fake_run):
            main._resolve_yt_audio("dQw4w9WgXcQ")
        self.assertIn("https://www.youtube.com/watch?v=dQw4w9WgXcQ", captured["cmd"])


class LibraryApiTest(ApiTestBase):
    """The Library list endpoint tags each track with a digestible state and the
    single-track endpoint hands the full row to the Review view."""

    def test_library_states(self):
        rows = self.client.get("/api/library").json()["rows"]
        by_name = {r["filename"]: r for r in rows}
        # A.mp3 has a local file + a yt link, not yet reviewed -> unreviewed
        self.assertEqual(by_name["A.mp3"]["state"], "unreviewed")
        self.assertTrue(by_name["A.mp3"]["has_local"])
        # B.mp3 has a yt link but no local file -> link_only
        self.assertEqual(by_name["B.mp3"]["state"], "link_only")
        self.assertFalse(by_name["B.mp3"]["has_local"])

    def test_state_follows_decision(self):
        self.client.post("/api/decision",
                         json={"track_id": self.ids["A.mp3"], "decision": True})
        rows = self.client.get("/api/library").json()["rows"]
        state = {r["filename"]: r["state"] for r in rows}
        self.assertEqual(state["A.mp3"], "confirmed")   # check wins over derived state

    def test_track_returns_full_row(self):
        r = self.client.get(f"/api/track/{self.ids['A.mp3']}")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["filename"], "A.mp3")
        self.assertTrue(body["has_local"])

    def test_track_404(self):
        self.assertEqual(self.client.get("/api/track/999999").status_code, 404)


class TrackStateUnitTest(unittest.TestCase):
    """main._track_state branch logic in isolation."""

    def test_all_branches(self):
        self.assertEqual(main._track_state({"check": 1, "yt_id": "x"}, True), "confirmed")
        self.assertEqual(main._track_state({"check": 0, "yt_id": "x"}, True), "rejected")
        self.assertEqual(main._track_state({"check": None, "yt_id": "x"}, True), "unreviewed")
        self.assertEqual(main._track_state({"check": None, "yt_id": ""}, True), "file_only")
        self.assertEqual(main._track_state({"check": None, "yt_id": "x"}, False), "link_only")
        self.assertEqual(main._track_state({"check": None, "yt_id": ""}, False), "new")


class ArtifactsTest(unittest.TestCase):
    """jobs.artifacts summarizes a script's output files (counts + links)."""

    def setUp(self):
        import jobs
        self.jobs = jobs
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = jobs.REPO_ROOT
        jobs.REPO_ROOT = self.tmp.name

    def tearDown(self):
        self.jobs.REPO_ROOT = self._orig
        self.tmp.cleanup()

    def _write(self, name, text):
        with open(os.path.join(self.tmp.name, name), "w", encoding="utf-8") as f:
            f.write(text)

    def test_line_and_link_counts(self):
        self._write("ids.txt", "aaa\nbbb\n\nccc\n")           # blank line ignored -> 3
        self._write("urls.txt", "u1\nu2\n")
        self._write("playlists.txt", "https://x/1\nhttps://x/2\n")
        arts = {a["file"]: a for a in self.jobs.artifacts("url_extractor")}
        self.assertEqual(arts["ids.txt"]["count"], 3)
        self.assertEqual(arts["urls.txt"]["count"], 2)
        self.assertEqual(arts["playlists.txt"]["count"], 2)
        self.assertEqual(arts["playlists.txt"]["links"], ["https://x/1", "https://x/2"])

    def test_playlist_links_are_not_silently_truncated(self):
        links = [f"https://x/{i}" for i in range(25)]
        self._write("playlists.txt", "\n".join(links) + "\n")
        art = {a["file"]: a for a in self.jobs.artifacts("playlist_generator")}["playlists.txt"]
        self.assertEqual(art["count"], 25)
        self.assertEqual(art["links"], links)

    def test_csv_count_excludes_header(self):
        self._write("matches.csv", "filename,check\nA.mp3,1\nB.mp3,0\n")
        art = self.jobs.artifacts("searcher")[0]
        self.assertEqual(art["count"], 2)          # 3 lines - header

    def test_missing_files_report_zero(self):
        arts = self.jobs.artifacts("downloader")   # nothing written
        self.assertTrue(all(a["count"] == 0 and not a["exists"] for a in arts))


class WorkspaceEnrichTest(ApiTestBase):
    """The enrich endpoint fetches YouTube metadata/health into workspace items.
    The network resolver is stubbed so no test hits yt-dlp."""

    def test_enrich_fills_metadata_and_marks_dead(self):
        self.client.post("/api/workspace/import", json={"text": "first123456\nsecond12345"})
        fake = {"first123456": {"health": "ok", "title": "Song A", "channel": "Chan A", "view_count": 5},
                "second12345": {"health": "private"}}
        orig = main._resolve_yt_metadata
        main._resolve_yt_metadata = lambda yid: fake[yid]
        try:
            resp = self.client.post("/api/workspace/enrich", json={"limit": 40}).json()
        finally:
            main._resolve_yt_metadata = orig
        items = {i["youtube_id"]: i for i in resp["items"]}
        self.assertEqual(items["first123456"]["title"], "Song A")
        self.assertEqual(json.loads(items["second12345"]["metadata_json"])["health"], "private")
        self.assertEqual(resp["remaining"], 0)

    def test_enrich_limit_reports_remaining(self):
        self.client.post("/api/workspace/import", json={"text": "first123456\nsecond12345\nthird123456"})
        orig = main._resolve_yt_metadata
        main._resolve_yt_metadata = lambda yid: {"health": "ok"}
        try:
            resp = self.client.post("/api/workspace/enrich", json={"limit": 1}).json()
        finally:
            main._resolve_yt_metadata = orig
        self.assertEqual(len(resp["checked"]), 1)
        self.assertEqual(resp["remaining"], 2)


class ClassifyYtErrorTest(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(main._classify_yt_error("ERROR: Private video. Sign in if you've been granted access"), "private")
        self.assertEqual(main._classify_yt_error("ERROR: Video unavailable"), "dead")
        self.assertEqual(main._classify_yt_error("ERROR: HTTP Error 429: Too Many Requests"), "unknown")
        self.assertEqual(main._classify_yt_error(""), "unknown")


class ResolveYtMetadataTest(unittest.TestCase):
    """_resolve_yt_metadata's yt-dlp -J handling (subprocess stubbed — no network)."""

    def _run(self, rc, stdout="", stderr=""):
        return types.SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)

    def test_ok_parses_fields(self):
        payload = json.dumps({"title": "T", "channel": "C", "view_count": 9, "duration": 100,
                              "upload_date": "20200101", "channel_is_verified": True,
                              "categories": ["Music"]})
        with mock.patch("main.subprocess.run", return_value=self._run(0, payload)):
            meta = main._resolve_yt_metadata("dQw4w9WgXcQ")
        self.assertEqual(meta["health"], "ok")
        self.assertEqual(meta["channel"], "C")
        self.assertTrue(meta["verified"])
        self.assertTrue(meta["is_music"])

    def test_failure_classifies_health(self):
        with mock.patch("main.subprocess.run", return_value=self._run(1, "", "ERROR: This video is private")):
            self.assertEqual(main._resolve_yt_metadata("dQw4w9WgXcQ")["health"], "private")

    def test_subprocess_error_is_unknown(self):
        with mock.patch("main.subprocess.run", side_effect=OSError("yt-dlp missing")):
            self.assertEqual(main._resolve_yt_metadata("dQw4w9WgXcQ")["health"], "unknown")


class PickFolderTest(unittest.TestCase):
    """Native folder dialog runs tkinter in a subprocess (stubbed here — no GUI)."""

    def _run(self, rc, stdout="", stderr=""):
        return types.SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)

    def test_returns_normalized_path(self):
        with mock.patch("main.subprocess.run", return_value=self._run(0, "E:/Music\n")):
            self.assertEqual(main._native_pick_folder(), os.path.normpath("E:/Music"))

    def test_cancelled_returns_none(self):
        with mock.patch("main.subprocess.run", return_value=self._run(0, "\n")):
            self.assertIsNone(main._native_pick_folder())

    def test_subprocess_error_returns_none(self):
        with mock.patch("main.subprocess.run", side_effect=OSError):
            self.assertIsNone(main._native_pick_folder())


class PickFolderApiTest(ApiTestBase):
    def test_pick_ok(self):
        orig = main._native_pick_folder
        main._native_pick_folder = lambda: "E:\\Music"
        try:
            self.assertEqual(self.client.post("/api/pick-folder").json()["path"], "E:\\Music")
        finally:
            main._native_pick_folder = orig

    def test_pick_cancelled_409(self):
        orig = main._native_pick_folder
        main._native_pick_folder = lambda: None
        try:
            self.assertEqual(self.client.post("/api/pick-folder").status_code, 409)
        finally:
            main._native_pick_folder = orig


if __name__ == "__main__":
    unittest.main(verbosity=2)
