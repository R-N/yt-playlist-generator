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
import tasks
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
        main._STAGED_FILES.clear()      # in-memory picked-file store; isolate per test
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
            "track_id": self.ids["A.mp3"], "decision": True, "checklist": ["youtube"]}).status_code, 200)
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
            "track_id": self.ids["A.mp3"], "decision": True, "checklist": ["youtube"]})
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

    def test_workspace_local_delete_is_ungated_by_approval(self):
        # Send an UNAPPROVED track to the Workspace, then bulk-delete its local file by item id.
        # Unlike the Library flow this must NOT require a decision (the user curates in Workspace).
        item = self.client.post("/api/workspace/library",
                                json={"track_id": self.ids["A.mp3"]}).json()["item"]
        self.assertIsNone(self.client.get(f"/api/track/{self.ids['A.mp3']}").json()["check"])
        preview = self.client.post("/api/workspace/local-delete/preview",
                                   json={"ids": [item["id"]]}).json()
        self.assertEqual(len(preview["targets"]), 1)
        r = self.client.post("/api/workspace/local-delete", json={
            "ids": [item["id"]], "token": preview["token"], "confirm": "DELETE"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(os.path.exists(os.path.join(main.MP3_FOLDERS[0], "A.mp3")))
        self.assertEqual(self.client.get("/api/library/delete/audit").json()["audit"][0]["outcome"], "deleted")
        # The track link is now unavailable, so the item's local_count -> 0 (the "local" label drops).
        after = next(it for it in self.client.get("/api/workspace").json()["items"] if it["id"] == item["id"])
        self.assertEqual(after["local_count"], 0)

    def test_workspace_local_delete_removes_file_only_item(self):
        # A file-only Workspace item (direct ref, no yt/track) whose file is deleted has no
        # remaining identity, so it is removed — no dangling "local" label left behind.
        rec = main._CATALOG.records[0]
        added = self.client.post("/api/workspace/add-files", json={"files": [
            {"folder_identity": rec["folder_identity"], "relative_path": rec["relative_path"]}]}).json()
        item_id = added["results"][0]["workspace_item_id"]
        preview = self.client.post("/api/workspace/local-delete/preview", json={"ids": [item_id]}).json()
        self.assertEqual(len(preview["targets"]), 1)
        self.client.post("/api/workspace/local-delete", json={
            "ids": [item_id], "token": preview["token"], "confirm": "DELETE"})
        ids = [it["id"] for it in self.client.get("/api/workspace").json()["items"]]
        self.assertNotIn(item_id, ids)

    def test_workspace_local_delete_typed_confirm_and_skips_linkless(self):
        item = self.client.post("/api/workspace/library",
                                json={"track_id": self.ids["A.mp3"]}).json()["item"]
        # A link-only workspace item (no local file) is reported as skipped, not deleted.
        linkless = self.client.post("/api/workspace/import", json={"text": "nolocal9999"}).json()["added"][0]
        preview = self.client.post("/api/workspace/local-delete/preview",
                                   json={"ids": [item["id"], linkless["id"]]}).json()
        self.assertEqual(len(preview["targets"]), 1)
        self.assertEqual(len(preview["skipped"]), 1)
        bad = self.client.post("/api/workspace/local-delete", json={
            "ids": [item["id"]], "token": preview["token"], "confirm": "nope"})
        self.assertEqual(bad.status_code, 400)   # typed confirm still required
        self.assertTrue(os.path.exists(os.path.join(main.MP3_FOLDERS[0], "A.mp3")))

    def test_verify_link_dead_unreviews_approved_track(self):
        tid = self.ids["A.mp3"]
        db.apply_track_yt(tid, "vid00000000", {"title": "x"})   # give it a link, unreviewed
        self.assertEqual(self.client.post("/api/decision", json={
            "track_id": tid, "decision": True, "checklist": ["youtube"]}).status_code, 200)
        self.assertEqual(self.client.get(f"/api/track/{tid}").json()["check"], 1)   # approved
        with mock.patch.object(main, "_resolve_yt_metadata", return_value={"health": "dead"}):
            r = self.client.post(f"/api/track/{tid}/verify-link")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["health"], "dead")
        # dead link on an approved track -> back to unreviewed
        self.assertIsNone(self.client.get(f"/api/track/{tid}").json()["check"])

    def test_verify_link_alive_keeps_approval(self):
        tid = self.ids["A.mp3"]
        db.apply_track_yt(tid, "vid00000000", {"title": "x"})
        self.client.post("/api/decision", json={"track_id": tid, "decision": True, "checklist": ["youtube"]})
        with mock.patch.object(main, "_resolve_yt_metadata", return_value={"health": "ok"}):
            self.client.post(f"/api/track/{tid}/verify-link")
        self.assertEqual(self.client.get(f"/api/track/{tid}").json()["check"], 1)   # stays approved

    def test_verify_local_reports_and_clears_missing(self):
        tid = self.ids["A.mp3"]
        self.assertTrue(self.client.post(f"/api/track/{tid}/verify-local").json()["present"])
        os.remove(os.path.join(main.MP3_FOLDERS[0], "A.mp3"))
        self.assertFalse(self.client.post(f"/api/track/{tid}/verify-local").json()["present"])

    def test_verify_local_clears_direct_ref_workspace_item(self):
        # A Workspace item pointing straight at a file (direct ref) must drop its own local
        # ref when the file is gone — mark_links_unavailable alone can't (it's not a track link).
        rec = main._CATALOG.records[0]
        item = self.client.post("/api/workspace/add-files", json={"files": [
            {"folder_identity": rec["folder_identity"], "relative_path": rec["relative_path"]}]}
        ).json()["results"][0]
        item_id = item["workspace_item_id"]
        os.remove(os.path.join(main.MP3_FOLDERS[0], "A.mp3"))
        self.assertFalse(self.client.post(f"/api/workspace/{item_id}/verify-local").json()["present"])
        # file-only item whose file vanished -> removed (no dangling 'local' label left)
        ids = [it["id"] for it in self.client.get("/api/workspace").json()["items"]]
        self.assertNotIn(item_id, ids)

    def test_verify_local_handles_out_of_folder_absolute_path(self):
        # An out-of-folder file (referenced by absolute path, folder_identity = its own dir)
        # verifies against that real path — present while it exists, cleared once removed.
        outside = os.path.join(tempfile.mkdtemp(), "Elsewhere.mp3")
        with open(outside, "wb") as f:
            f.write(b"\xff\xfb\x90\x00" + b"\x00" * 64)
        self.assertEqual(self.client.post("/api/files/add", json={
            "paths": [outside], "target": "workspace"}).json()["added"], 1)
        item_id = next(it["id"] for it in self.client.get("/api/workspace").json()["items"]
                       if (it.get("relative_path") or "").endswith("Elsewhere.mp3"))
        self.assertTrue(self.client.post(f"/api/workspace/{item_id}/verify-local").json()["present"])
        os.remove(outside)
        self.assertFalse(self.client.post(f"/api/workspace/{item_id}/verify-local").json()["present"])

    def test_verify_labels_bulk_reuses_local_verify(self):
        # Bulk "Verify labels" on a selection must clear a missing local file's label too, via the
        # SAME core as the per-row verify (regression: the bulk path used to only do link + catalog).
        rec = main._CATALOG.records[0]
        self.assertEqual(self.client.post("/api/workspace/add-files", json={"files": [
            {"folder_identity": rec["folder_identity"], "relative_path": rec["relative_path"]}]}
        ).json()["added"], 1)
        item_id = next(it["id"] for it in self.client.get("/api/workspace").json()["items"]
                       if (it.get("relative_path") or "").endswith("A.mp3"))
        os.remove(os.path.join(main.MP3_FOLDERS[0], "A.mp3"))
        self.assertEqual(self.client.post("/api/tasks/verify/workspace",
                                          json={"ids": [item_id]}).status_code, 200)
        ids = [it["id"] for it in self.client.get("/api/workspace").json()["items"]]
        self.assertNotIn(item_id, ids)   # file-only + file gone -> removed by the shared core

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
            "track_id": self.ids["A.mp3"], "decision": True, "checklist": ["youtube"]}).json()["check"], 1)
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
                             json={"track_id": self.ids["A.mp3"], "decision": True, "checklist": ["youtube"]})
        self.assertEqual(r.json()["check"], 1)
        self.assertEqual(self.client.get("/api/counts").json()["approved"], 1)

    def test_approve_requires_youtube_checked(self):
        # no checklist -> approve blocked
        r = self.client.post("/api/decision",
                             json={"track_id": self.ids["A.mp3"], "decision": True})
        self.assertEqual(r.status_code, 400)
        # reject needs nothing
        self.assertEqual(self.client.post("/api/decision",
            json={"track_id": self.ids["A.mp3"], "decision": False}).status_code, 200)

    def test_checklist_recorded_and_listed(self):
        self.client.post("/api/decision", json={
            "track_id": self.ids["A.mp3"], "decision": True,
            "checklist": ["youtube", "local", "lyrics"]})
        hist = self.client.get("/api/history").json()["decisions"]
        self.assertEqual(hist[0]["checklist"], ["youtube", "local", "lyrics"])

    def test_track_decision_returns_latest_checklist(self):
        tid = self.ids["A.mp3"]
        self.client.post("/api/decision", json={"track_id": tid, "decision": False})
        self.client.post("/api/decision", json={
            "track_id": tid, "decision": True, "checklist": ["youtube", "metadata"]})
        d = self.client.get(f"/api/track/{tid}/decision").json()
        self.assertEqual(d["decision"], 1)
        self.assertEqual(d["checklist"], ["youtube", "metadata"])

    def test_decision_unknown_track_404(self):
        r = self.client.post("/api/decision",
                             json={"track_id": 999999, "decision": True, "checklist": ["youtube"]})
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
            "track_id": self.ids["A.mp3"], "decision": True, "checklist": ["youtube"],
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

    def test_id_based_download_run_forces_replace_and_passes_format(self):
        old_storage = main.RUN_STORAGE
        storage = tempfile.TemporaryDirectory()
        main.RUN_STORAGE = storage.name
        captured = {}
        original_start = jobs.start
        jobs.start = lambda name, **kwargs: (captured.update(kwargs), {"name": name, "status": "running"})[1]
        try:
            response = self.client.post("/api/download/run",
                                        json={"yt_ids": ["abcdefghij0", "abcdefghij0", ""], "format": "mp3"})
            self.assertEqual(response.status_code, 200)
            env = captured["env_overrides"]
            self.assertEqual(env["AUDIO_FORMAT"], "mp3")
            self.assertEqual(env["YT_FORCE_REDOWNLOAD"], "1")   # replace defaults True
            # de-duplicated + blank dropped -> one snapshotted item
            self.assertEqual(len(response.json()["items"]), 1)
        finally:
            jobs.start = original_start
            jobs.release_pipeline("workspace_download")
            main.RUN_STORAGE = old_storage
            storage.cleanup()

    def test_download_run_rejects_unknown_format(self):
        r = self.client.post("/api/download/run", json={"yt_ids": ["abcdefghij0"], "format": "wav"})
        self.assertEqual(r.status_code, 400)

    def test_download_run_requires_ids(self):
        r = self.client.post("/api/download/run", json={"yt_ids": ["", "  "]})
        self.assertEqual(r.status_code, 400)

    def test_remove_stale_after_replace_only_when_new_file_landed(self):
        tmp = tempfile.TemporaryDirectory()
        old_path = os.path.join(tmp.name, "Song [vid11111111].opus")
        new_path = os.path.join(tmp.name, "Song [vid11111111].mp3")
        keep_path = os.path.join(tmp.name, "Other [vid22222222].opus")
        for p in (old_path, new_path, keep_path):
            with open(p, "w") as f:
                f.write("x")
        original = main._download_files_for_id
        # id vid1: old + new both present (format changed) -> old must go.
        # id vid2: only its original present (that id "failed") -> keep it.
        main._download_files_for_id = lambda yt_id: (
            [old_path, new_path] if yt_id == "vid11111111" else [keep_path])
        try:
            main._remove_stale_after_replace({"vid11111111": {old_path}, "vid22222222": {keep_path}})
            self.assertFalse(os.path.exists(old_path))   # stale old-format removed
            self.assertTrue(os.path.exists(new_path))     # freshly downloaded kept
            self.assertTrue(os.path.exists(keep_path))    # no new file for vid2 -> untouched
        finally:
            main._download_files_for_id = original
            tmp.cleanup()

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
            json={"track_id": self.ids["A.mp3"], "decision": True, "checklist": ["youtube"]},
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
                             json={"track_id": self.ids["A.mp3"], "decision": True, "checklist": ["youtube"]})
        self.assertNotIn("auto_exported", r1.json())     # 1st: no export yet
        r2 = self.client.post("/api/decision",
                             json={"track_id": self.ids["B.mp3"], "decision": True, "checklist": ["youtube"]})
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
                         json={"track_id": self.ids["A.mp3"], "decision": True, "checklist": ["youtube"]})
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


class BackgroundVerifyTest(ApiTestBase):
    """The Verify buttons start a paced background task; the network resolver and
    the delay are stubbed so it runs instantly and hits no yt-dlp."""

    def setUp(self):
        super().setUp()
        self._delay = tasks.DELAY
        tasks.DELAY = (0, 0)
        tasks._active = None
        tasks._cancel.clear()
        tasks._threads.clear()

    def tearDown(self):
        for t in list(tasks._threads.values()):
            t.join(timeout=2)
        tasks.DELAY = self._delay
        super().tearDown()

    def _join(self, task_id):
        tasks._threads[task_id].join(timeout=3)

    def test_workspace_verify_task_runs_and_flags_dead(self):
        self.client.post("/api/workspace/import", json={"text": "first123456\nsecond12345"})
        fake = {"first123456": {"health": "ok"}, "second12345": {"health": "dead"}}
        orig = main._resolve_yt_metadata
        main._resolve_yt_metadata = lambda yid: fake[yid]
        try:
            task = self.client.post("/api/tasks/verify/workspace", json={"scope": "all"}).json()
            self._join(task["id"])
        finally:
            main._resolve_yt_metadata = orig
        done = self.client.get("/api/tasks").json()["tasks"][0]
        self.assertEqual(done["status"], "done")
        self.assertEqual((done["done"], done["found"]), (2, 1))   # 2 checked, 1 dead

    def test_second_verify_returns_409_while_running(self):
        self.client.post("/api/workspace/import", json={"text": "first123456"})
        orig = main._resolve_yt_metadata
        # Hold one task 'running' by parking the resolver on the first call.
        import threading
        entered, release = threading.Event(), threading.Event()
        def parked(yid):
            entered.set(); release.wait(2); return {"health": "ok"}
        main._resolve_yt_metadata = parked
        try:
            first = self.client.post("/api/tasks/verify/workspace", json={"scope": "all"}).json()
            entered.wait(2)
            resp = self.client.post("/api/tasks/verify/workspace", json={"scope": "all"})
            self.assertEqual(resp.status_code, 409)
            release.set()
            self._join(first["id"])
        finally:
            main._resolve_yt_metadata = orig

    def test_history_lists_decisions(self):
        rows = self.client.get("/api/rows?status=all&limit=5").json()["rows"]
        self.client.post("/api/decision", json={"track_id": rows[0]["id"], "decision": 1, "checklist": ["youtube"]})
        hist = self.client.get("/api/history").json()["decisions"]
        self.assertEqual(hist[0]["decision"], 1)
        self.assertEqual(hist[0]["track_id"], rows[0]["id"])


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


class FindLinkTest(ApiTestBase):
    def test_review_find_youtube_excludes_rejected_and_applies_best(self):
        import searcher
        tid = self.ids["A.mp3"]
        # Current link "rej", then reject it -> logged as rejected for this track.
        db.apply_track_yt(tid, "rej", {"title": "old"})
        self.assertEqual(self.client.post(
            "/api/decision", json={"track_id": tid, "decision": False}).status_code, 200)
        entries = [{"id": "a"}, {"id": "rej"}, {"id": "good"}]
        scores = {"a": 1, "rej": 9, "good": 5}
        with mock.patch.object(main, "_yt_search", return_value=entries), \
             mock.patch.object(searcher, "score", side_effect=lambda e, *a, **k: scores[e["id"]]):
            r = self.client.post("/api/review/find-youtube", json={"track_id": tid})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["yt_id"], "good")   # "rej" excluded despite top score
        self.assertIsNone(body["check"])          # applied as unreviewed

    def test_item_terms_reads_metadata_blob_not_only_columns(self):
        # Regression: link-only items keep title/channel in metadata_json, not the
        # columns. Reading columns only made background find "found none" (no terms).
        item = {"title": None, "channel": None, "track_artist": None, "track_title": None,
                "metadata_json": '{"title": "Song", "channel": "Band"}'}
        self.assertEqual(main._item_terms(item), ("Band", "Song"))
        self.assertTrue(main._has_terms(item))

    def test_item_terms_falls_back_to_filename_for_tagless_local(self):
        # Regression: a tagless/unreadable local file (no columns, metadata, or track)
        # was unsearchable -> "0 found". Filename stem is the last-resort search term.
        item = {"relative_path": "Cabo da Roca.m4a", "folder_identity": "/nope"}  # no such file
        self.assertEqual(main._item_terms(item), ("", "Cabo da Roca"))
        self.assertTrue(main._has_terms(item))

    def test_item_terms_reads_tags_off_out_of_folder_file(self):
        # Canaria case: an out-of-folder staged file has no catalog record, but its real
        # ID3 tags should still drive the search (read off the file's own path).
        item = {"relative_path": "Canaria.mp3", "folder_identity": "e:\\music\\downloads"}
        with mock.patch.object(main, "_read_audio_tags",
                               return_value={"tag_artist": "ReoNa", "tag_title": "Canaria"}):
            self.assertEqual(main._item_terms(item), ("ReoNa", "Canaria"))

    def test_review_find_youtube_404_when_only_current_link_returned(self):
        import searcher
        tid = self.ids["A.mp3"]                    # current yt_id "a"
        with mock.patch.object(main, "_yt_search", return_value=[{"id": "a"}]), \
             mock.patch.object(searcher, "score", side_effect=lambda e, *a, **k: 5):
            r = self.client.post("/api/review/find-youtube", json={"track_id": tid})
        self.assertEqual(r.status_code, 404)


class EmbedMetadataTest(ApiTestBase):
    def test_download_ref_classified_and_embed_writes_tags(self):
        import mutagen
        dl = os.path.join(self.tmp.name, "downloads")
        os.makedirs(dl)
        f = os.path.join(dl, "Cabo da Roca.m4a")
        with open(f, "wb") as fh:
            fh.write(b"\x00" * 16)
        settings.save({"DOWNLOAD_FOLDER": dl})
        # A direct file ref inside the download folder is a *download*, not a local file.
        item = {"relative_path": "Cabo da Roca.m4a", "folder_identity": dl}
        self.assertTrue(main._is_download_ref(item))
        self.assertFalse(main._is_download_ref(
            {"relative_path": "x.mp3", "folder_identity": os.path.join(self.tmp.name, "music")}))

        # Embed writes artist/title through mutagen easy-mode (only supported tags).
        tags = {}
        fake = mock.MagicMock()
        fake.tags = {}
        fake.__setitem__.side_effect = tags.__setitem__
        with mock.patch.object(mutagen, "File", return_value=fake):
            r = main._embed_metadata_into(f, "DAZBEE", "Cabo da Roca")
        self.assertEqual(r["ok"], True)
        self.assertEqual(tags, {"artist": "DAZBEE", "title": "Cabo da Roca"})
        fake.save.assert_called_once()

    def test_embed_refuses_when_no_metadata(self):
        f = os.path.join(self.tmp.name, "music", "A.mp3")
        with self.assertRaises(main.HTTPException) as cm:
            main._embed_metadata_into(f, "", "")
        self.assertEqual(cm.exception.status_code, 400)


class AddFilesByPathTest(ApiTestBase):
    def test_add_by_absolute_path_targets_and_staged_untracked(self):
        extra = os.path.join(self.tmp.name, "outside")   # outside configured mp3 folders
        os.makedirs(extra)
        p = os.path.join(extra, "Song.mp3")
        with open(p, "wb") as f:
            f.write(b"ID3")
        # untracked -> in-memory staged, surfaced by /api/files/staged
        r = self.client.post("/api/files/add", json={"paths": [p], "target": "untracked"}).json()
        self.assertEqual(r["added"], 1)
        staged = self.client.get("/api/files/staged").json()["files"]
        self.assertEqual([s["basename"] for s in staged], ["Song.mp3"])
        # library -> becomes a track; staged entry then drops (now tracked)
        self.assertEqual(self.client.post(
            "/api/files/add", json={"paths": [p], "target": "library"}).json()["added"], 1)
        self.assertEqual(self.client.get("/api/files/staged").json()["files"], [])
        # re-staging a now-tracked file is refused
        again = self.client.post("/api/files/add", json={"paths": [p], "target": "untracked"}).json()
        self.assertEqual(again["added"], 0)
        self.assertEqual(again["results"][0]["reason"], "already in Library")
        # a path that isn't a file is skipped
        missing = self.client.post("/api/files/add", json={
            "paths": [os.path.join(extra, "nope.mp3")], "target": "workspace"}).json()
        self.assertEqual(missing["added"], 0)

    def test_staged_out_of_folder_file_is_first_class(self):
        extra = os.path.join(self.tmp.name, "outside2")
        os.makedirs(extra)
        p = os.path.join(extra, "Tune.mp3")
        with open(p, "wb") as f:
            f.write(self.audio_bytes)
        self.client.post("/api/files/add", json={"paths": [p], "target": "untracked"})
        s = self.client.get("/api/files/staged").json()["files"][0]
        # plays despite living outside the configured folders (explicitly staged)
        audio = self.client.get("/api/local-audio", params={
            "folder_identity": s["folder_identity"], "relative_path": s["relative_path"]})
        self.assertEqual(audio.status_code, 200)
        # row-menu Add-to-Library (ref endpoint) resolves the staged file too
        r = self.client.post("/api/library/add-files", json={"files": [
            {"folder_identity": s["folder_identity"], "relative_path": s["relative_path"]}]}).json()
        self.assertEqual(r["added"], 1)
        # but an out-of-folder file that was NEVER staged stays refused (guard holds)
        bogus = os.path.join(extra, "NotStaged.mp3")
        with open(bogus, "wb") as f:
            f.write(b"ID3")
        self.assertEqual(self.client.get("/api/local-audio", params={
            "folder_identity": extra, "relative_path": "NotStaged.mp3"}).status_code, 404)

    def test_add_by_path_workspace_creates_file_only_item(self):
        p = os.path.join(self.tmp.name, "ws.mp3")
        with open(p, "wb") as f:
            f.write(b"ID3")
        self.assertEqual(self.client.post(
            "/api/files/add", json={"paths": [p], "target": "workspace"}).json()["added"], 1)
        items = self.client.get("/api/workspace").json()["items"]
        self.assertTrue(any(it.get("relative_path") == "ws.mp3" for it in items))

    def test_save_file_only_item_to_library_creates_and_links_track(self):
        # Regression: "Save to library" on a file-only item went through save-links (needs
        # a youtube_id it lacks) and did nothing. It must create a Library track + link it.
        p = os.path.join(main.MP3_FOLDERS[0], "loose.mp3")   # a configured-folder untracked file
        with open(p, "wb") as f:
            f.write(b"ID3")
        self.client.post("/api/files/add", json={"paths": [p], "target": "workspace"})
        item = next(it for it in self.client.get("/api/workspace").json()["items"]
                    if it.get("relative_path") == "loose.mp3")
        self.assertIsNone(item["track_id"])
        r = self.client.post("/api/workspace/save-to-library", json={"ids": [item["id"]]})
        self.assertEqual(r.status_code, 200)
        res = r.json()["results"][0]
        self.assertEqual(res["outcome"], "track")
        after = next(it for it in self.client.get("/api/workspace").json()["items"]
                     if it["id"] == item["id"])
        self.assertEqual(after["track_id"], res["track_id"])   # now In Library

    def test_save_untracked_file_with_link_carries_yt_onto_track(self):
        # The reported bug: an untracked file that ALSO has a youtube link went to
        # save-links (saving the link, not the file). It must create a track from the file
        # and carry the link onto it.
        p = os.path.join(main.MP3_FOLDERS[0], "withlink.mp3")
        with open(p, "wb") as f:
            f.write(b"ID3")
        self.client.post("/api/files/add", json={"paths": [p], "target": "workspace"})
        item = next(it for it in self.client.get("/api/workspace").json()["items"]
                    if it.get("relative_path") == "withlink.mp3")
        self.client.post(f"/api/workspace/{item['id']}/youtube",
                         json={"youtube_id": "dQw4w9WgXcQ", "title": "T", "channel": "C"})
        r = self.client.post("/api/workspace/save-to-library", json={"ids": [item["id"]]})
        self.assertEqual(r.status_code, 200)
        track = self.client.get(f"/api/track/{r.json()['results'][0]['track_id']}").json()
        self.assertEqual(track["yt_id"], "dQw4w9WgXcQ")   # link carried onto the new track

    def test_bulk_save_routes_files_and_links_together(self):
        # Bulk + per-row share the one endpoint: a file item becomes a track, a link-only
        # item becomes a saved link, in a single mixed call.
        p = os.path.join(main.MP3_FOLDERS[0], "mixed.mp3")
        with open(p, "wb") as f:
            f.write(b"ID3")
        self.client.post("/api/files/add", json={"paths": [p], "target": "workspace"})
        file_item = next(it for it in self.client.get("/api/workspace").json()["items"]
                         if it.get("relative_path") == "mixed.mp3")
        link_item = self.client.post("/api/workspace/import", json={"text": "zzzzzzzzzzz"}).json()["added"][0]
        r = self.client.post("/api/workspace/save-to-library",
                             json={"ids": [file_item["id"], link_item["id"]]})
        self.assertEqual(r.status_code, 200)
        outcomes = {res["id"]: res["outcome"] for res in r.json()["results"]}
        self.assertEqual(outcomes[file_item["id"]], "track")
        self.assertEqual(outcomes[link_item["id"]], "link")


class SearchPickerTest(ApiTestBase):
    def test_youtube_search_ranks_by_score_high_to_low(self):
        import searcher
        entries = [{"id": "aaaaaaaaaaa", "title": "A"}, {"id": "bbbbbbbbbbb", "title": "B"},
                   {"id": "ccccccccccc", "title": "C"}]
        scores = {"aaaaaaaaaaa": 1, "bbbbbbbbbbb": 9, "ccccccccccc": 5}
        with mock.patch.object(main, "_yt_search", return_value=entries), \
             mock.patch.object(searcher, "score", side_effect=lambda e, *a, **k: scores[e["id"]]):
            r = self.client.post("/api/search/youtube",
                                 json={"query": "x", "artist": "a", "title": "t"}).json()
        self.assertEqual([x["id"] for x in r["results"]], ["bbbbbbbbbbb", "ccccccccccc", "aaaaaaaaaaa"])
        self.assertEqual(r["results"][0]["score"], 9)

    def test_local_search_returns_ranked_catalog(self):
        r = self.client.post("/api/search/local", json={"query": "A"}).json()
        self.assertTrue(any(res["basename"] == "A.mp3" for res in r["results"]))
        self.assertIn("score", r["results"][0])

    def test_local_search_sees_file_added_after_startup(self):
        # Regression: catalog is built at startup; a file downloaded into a configured
        # folder afterward was invisible to Find-local until a manual rescan. The search
        # now refreshes the catalog first.
        new = os.path.join(main.MP3_FOLDERS[0], "Freshly Downloaded Song.mp3")
        with open(new, "wb") as f:
            f.write(b"ID3")
        r = self.client.post("/api/search/local", json={"query": "Freshly Downloaded"}).json()
        self.assertTrue(any(res["basename"] == "Freshly Downloaded Song.mp3" for res in r["results"]))

    def test_apply_chosen_youtube_and_local_to_track(self):
        tid = self.ids["A.mp3"]
        r = self.client.post(f"/api/track/{tid}/youtube",
                             json={"youtube_id": "dQw4w9WgXcQ", "title": "T"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["yt_id"], "dQw4w9WgXcQ")
        self.assertIsNone(r.json()["check"])                    # applied unreviewed
        self.assertEqual(self.client.post(f"/api/track/{tid}/youtube",
                                          json={"youtube_id": "short"}).status_code, 400)
        # A.mp3's file already belongs to track A; linking it to B is a clean 409.
        local = self.client.get("/api/local-files").json()["files"][0]
        bid = self.ids["B.mp3"]
        self.assertEqual(self.client.post(f"/api/track/{bid}/local-file", json={
            "folder_identity": local["folder_identity"],
            "relative_path": local["relative_path"]}).status_code, 409)

    def test_apply_chosen_local_to_workspace_item(self):
        item = self.client.post("/api/workspace/import", json={"text": "wsvid123456"}).json()["added"][0]
        local = self.client.get("/api/local-files").json()["files"][0]
        r = self.client.post(f"/api/workspace/{item['id']}/local-file", json={
            "folder_identity": local["folder_identity"], "relative_path": local["relative_path"]})
        self.assertEqual(r.status_code, 200)
        got = next(it for it in self.client.get("/api/workspace").json()["items"] if it["id"] == item["id"])
        self.assertEqual(got["relative_path"], local["relative_path"])


class ForceSetAndEditTest(ApiTestBase):
    def test_resolve_youtube_parses_url_scores_and_rejects_garbage(self):
        import searcher
        with mock.patch.object(main, "_resolve_yt_metadata",
                               return_value={"health": "ok", "title": "T", "channel": "C", "view_count": 5}), \
             mock.patch.object(searcher, "score", return_value=77):
            r = self.client.post("/api/resolve/youtube",
                                 json={"value": "https://youtu.be/dQw4w9WgXcQ", "artist": "a", "title": "t"}).json()
        self.assertEqual(r["id"], "dQw4w9WgXcQ")
        self.assertTrue(r["alive"])
        self.assertEqual(r["score"], 77)
        self.assertEqual(self.client.post("/api/resolve/youtube", json={"value": "garbage"}).status_code, 400)

    def test_score_local_and_set_by_absolute_path_registers_playable(self):
        extra = os.path.join(self.tmp.name, "force")
        os.makedirs(extra)
        p = os.path.join(extra, "Chosen.mp3")
        with open(p, "wb") as f:
            f.write(self.audio_bytes)
        s = self.client.post("/api/score/local", json={"path": p, "artist": "a", "title": "Chosen"}).json()
        self.assertEqual(s["basename"], "Chosen.mp3")
        self.assertIn("score", s)
        item = self.client.post("/api/workspace/import", json={"text": "forcevid123"}).json()["added"][0]
        self.assertEqual(self.client.post(f"/api/workspace/{item['id']}/local-file",
                                          json={"path": p}).status_code, 200)
        got = next(it for it in self.client.get("/api/workspace").json()["items"] if it["id"] == item["id"])
        self.assertEqual(got["relative_path"], "Chosen.mp3")
        # the outside file was registered as staged, so it stays playable
        self.assertEqual(self.client.get("/api/local-audio", params={
            "folder_identity": got["folder_identity"], "relative_path": "Chosen.mp3"}).status_code, 200)

    def test_patch_track_edits_whitelisted_only(self):
        tid = self.ids["A.mp3"]
        r = self.client.patch(f"/api/track/{tid}",
                              json={"fields": {"artist": "New Artist", "title": "New Title", "check": 9}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["artist"], "New Artist")
        self.assertIsNone(r.json()["check"])          # 'check' is not editable — ignored
        self.assertEqual(self.client.patch(f"/api/track/{tid}",
                                           json={"fields": {"yt_id": "short"}}).status_code, 400)

    def test_patch_workspace_edits_title_channel(self):
        item = self.client.post("/api/workspace/import", json={"text": "editvid1234"}).json()["added"][0]
        r = self.client.patch(f"/api/workspace/{item['id']}",
                              json={"fields": {"title": "Edited", "channel": "Chan", "position": 999}})
        self.assertEqual(r.status_code, 200)
        got = next(it for it in self.client.get("/api/workspace").json()["items"] if it["id"] == item["id"])
        self.assertEqual((got["title"], got["channel"]), ("Edited", "Chan"))


class SettingsGetterTest(unittest.TestCase):
    def test_search_top_n_and_task_delay_clamp(self):
        def envget(values):
            return lambda k, d=None: values.get(k, d)
        with mock.patch.object(settings, "get", side_effect=envget({"YT_SEARCH_TOP_N": "50"})):
            self.assertEqual(settings.search_top_n(), 10)       # clamped to ceiling
        with mock.patch.object(settings, "get",
                               side_effect=envget({"TASK_DELAY_MIN": "5", "TASK_DELAY_MAX": "2"})):
            self.assertEqual(settings.task_delay(), (5.0, 5.0))  # max forced >= min
        with mock.patch.object(settings, "get", side_effect=envget({})):
            self.assertEqual(settings.search_top_n(), 3)         # defaults
            self.assertEqual(settings.task_delay(), (1.5, 4.0))
            self.assertEqual(settings.yt_min_score(), 0)
            self.assertEqual(settings.local_min_score(), 60)

    def test_min_score_getters(self):
        def envget(values):
            return lambda k, d=None: values.get(k, d)
        with mock.patch.object(settings, "get",
                               side_effect=envget({"YT_MIN_SCORE": "-20", "LOCAL_MIN_SCORE": "999"})):
            self.assertEqual(settings.yt_min_score(), -20)       # negatives allowed
            self.assertEqual(settings.local_min_score(), 100)    # clamped to 0..100
        with mock.patch.object(settings, "get", side_effect=envget({"LOCAL_MIN_SCORE": "-5"})):
            self.assertEqual(settings.local_min_score(), 0)

    def test_search_result_limit_and_delete_ttl(self):
        def envget(values):
            return lambda k, d=None: values.get(k, d)
        with mock.patch.object(settings, "get", side_effect=envget({"SEARCH_RESULT_LIMIT": "99", "DELETE_TOKEN_TTL": "2"})):
            self.assertEqual(settings.search_result_limit(), 50)   # clamped 1..50
            self.assertEqual(settings.delete_token_ttl(), 5)       # min 5
        with mock.patch.object(settings, "get", side_effect=envget({})):
            self.assertEqual(settings.search_result_limit(), 10)
            self.assertEqual(settings.delete_token_ttl(), 60)

    def test_cleanup_extensions_parse(self):
        def envget(values):
            return lambda k, d=None: values.get(k, d)
        with mock.patch.object(settings, "get", side_effect=envget({"CLEANUP_EXTENSIONS": "MP4, webm .part"})):
            self.assertEqual(settings.cleanup_extensions(), (".mp4", ".webm", ".part"))
        with mock.patch.object(settings, "get", side_effect=envget({})):
            self.assertIn(".webm", settings.cleanup_extensions())   # falls back to defaults

    def test_mb_min_score_clamped(self):
        def envget(values):
            return lambda k, d=None: values.get(k, d)
        with mock.patch.object(settings, "get", side_effect=envget({"MB_MIN_SCORE": "150"})):
            self.assertEqual(settings.mb_min_score(), 100)
        with mock.patch.object(settings, "get", side_effect=envget({})):
            self.assertEqual(settings.mb_min_score(), 90)

    def test_mb_search_limit_clamped(self):
        def envget(values):
            return lambda k, d=None: values.get(k, d)
        with mock.patch.object(settings, "get", side_effect=envget({"MB_SEARCH_LIMIT": "99"})):
            self.assertEqual(settings.mb_search_limit(), 25)
        with mock.patch.object(settings, "get", side_effect=envget({})):
            self.assertEqual(settings.mb_search_limit(), 5)


class LyricsMetadataApiTest(ApiTestBase):
    """Lyrics + metadata finding. The online providers (lyrics_fetch / MusicBrainz) are
    stubbed so no test hits the network; storage + apply are exercised for real."""

    def _import_item(self):
        added = self.client.post("/api/workspace/import", json={"text": "songid12345"}).json()["added"]
        return added[0]["id"]

    def test_find_lyrics_stores_and_get_serves_cached(self):
        item_id = self._import_item()
        synced = "[00:01.00]la la\n[00:03.00]la"
        orig = main._fetch_lyrics
        main._fetch_lyrics = lambda a, t: synced
        try:
            found = self.client.post(f"/api/workspace/{item_id}/lyrics").json()
        finally:
            main._fetch_lyrics = orig
        self.assertTrue(found["found"])
        self.assertTrue(found["synced"])
        # Stored on the item, so a plain GET returns it without re-fetching (stub restored).
        got = self.client.get(f"/api/workspace/{item_id}/lyrics").json()
        self.assertEqual(got["lyrics"], synced)
        item = next(i for i in self.client.get("/api/workspace").json()["items"] if i["id"] == item_id)
        self.assertEqual(json.loads(item["metadata_json"])["lyrics"], synced)

    def test_find_lyrics_reports_none(self):
        item_id = self._import_item()
        orig = main._fetch_lyrics
        main._fetch_lyrics = lambda a, t: ""
        try:
            found = self.client.post(f"/api/workspace/{item_id}/lyrics").json()
        finally:
            main._fetch_lyrics = orig
        self.assertFalse(found["found"])

    def test_lyrics_read_from_sidecar_without_network(self):
        # A workspace file item pointing at A.mp3; its .lrc sidecar is served as-is.
        folder = settings.configured_mp3_folders()[0]
        self.client.post("/api/workspace/add-files",
                         json={"files": [{"folder_identity": folder, "relative_path": "A.mp3"}]})
        item = next(i for i in self.client.get("/api/workspace").json()["items"] if i.get("relative_path") == "A.mp3")
        sidecar = os.path.splitext(os.path.join(item["folder_identity"], item["relative_path"]))[0] + ".lrc"
        with open(sidecar, "w", encoding="utf-8") as f:
            f.write("[00:00.50]hello")
        orig = main._fetch_lyrics
        main._fetch_lyrics = lambda a, t: (_ for _ in ()).throw(AssertionError("network hit"))
        try:
            got = self.client.get(f"/api/workspace/{item['id']}/lyrics").json()
        finally:
            main._fetch_lyrics = orig
        self.assertTrue(got["found"] and got["synced"])
        self.assertIn("hello", got["lyrics"])

    def test_find_metadata_applies_best_above_floor(self):
        item_id = self._import_item()
        orig = main._mb_best
        main._mb_best = lambda a, t: {"artist": "MB Artist", "title": "MB Title", "score": 95}
        try:
            with mock.patch.object(settings, "mb_min_score", return_value=90):
                updated = self.client.post(f"/api/workspace/{item_id}/find-metadata").json()
            self.assertEqual(updated["title"], "MB Title")
            self.assertEqual(updated["channel"], "MB Artist")
            # Below the floor -> 404, nothing applied.
            main._mb_best = lambda a, t: {"artist": "X", "title": "Y", "score": 50}
            with mock.patch.object(settings, "mb_min_score", return_value=90):
                resp = self.client.post(f"/api/workspace/{item_id}/find-metadata")
            self.assertEqual(resp.status_code, 404)
        finally:
            main._mb_best = orig

    def test_save_lyrics_persists_on_workspace_item(self):
        item_id = self._import_item()
        self.client.post(f"/api/workspace/{item_id}/lyrics/save", json={"lyrics": "[00:02.00]edited"})
        got = self.client.get(f"/api/workspace/{item_id}/lyrics").json()
        self.assertEqual(got["lyrics"], "[00:02.00]edited")
        self.assertTrue(got["synced"])

    def test_find_metadata_generic_applies_to_track_columns(self):
        # Same generic endpoint, kind='track' -> writes the artist/title columns.
        track_id = self.ids["A.mp3"]
        orig = main._mb_best
        main._mb_best = lambda a, t: {"artist": "Track Artist", "title": "Track Title", "score": 99}
        try:
            with mock.patch.object(settings, "mb_min_score", return_value=90):
                updated = self.client.post(f"/api/track/{track_id}/find-metadata").json()
        finally:
            main._mb_best = orig
        self.assertEqual(updated["artist"], "Track Artist")
        self.assertEqual(updated["title"], "Track Title")


class RomanizeTest(ApiTestBase):
    def test_romanize_text_endpoint(self):
        r = self.client.post("/api/romanize", json={
            "texts": ["残酷な天使", "hello", "[00:12.34]夜に駆ける"]}).json()
        self.assertEqual(r["texts"][0], "zankoku na tenshi")
        self.assertEqual(r["texts"][1], "hello")               # ascii untouched
        self.assertTrue(r["texts"][2].startswith("[00:12.34]"))  # LRC prefix preserved

    def test_romanize_filename_renames_and_repoints(self):
        folder = main.MP3_FOLDERS[0]
        p = os.path.join(folder, "残酷な天使.mp3")
        with open(p, "wb") as f:
            f.write(self.audio_bytes)
        main._install_catalog(main._build_file_catalog(main.MP3_FOLDERS))
        self.assertEqual(self.client.post(
            "/api/files/add", json={"paths": [p], "target": "library"}).json()["added"], 1)
        tid = next(row["id"] for row in self.client.get("/api/rows?status=all").json()["rows"]
                   if row["filename"] == "残酷な天使.mp3")

        r = self.client.post("/api/romanize/filename", json={"track_id": tid}).json()
        self.assertTrue(r["renamed"])
        self.assertEqual(r["name"], "zankoku na tenshi.mp3")
        self.assertFalse(os.path.exists(p))
        self.assertTrue(os.path.exists(os.path.join(folder, "zankoku na tenshi.mp3")))
        # link repointed: resolving the same track now finds an already-romanized name
        again = self.client.post("/api/romanize/filename", json={"track_id": tid}).json()
        self.assertFalse(again["renamed"])
        self.assertEqual(again["name"], "zankoku na tenshi.mp3")

    def test_romanize_filename_ascii_is_noop(self):
        r = self.client.post("/api/romanize/filename", json={
            "track_id": self.ids["A.mp3"]}).json()
        self.assertFalse(r["renamed"])
        self.assertTrue(os.path.exists(os.path.join(main.MP3_FOLDERS[0], "A.mp3")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
