"""Tests for the app's script-integration layer: settings (.env), the Discord
service, and the job catalog. Network and subprocess work is stubbed out."""
import os
import tempfile
import unittest

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
    def test_catalog_marks_destructive(self):
        cat = {c["name"]: c for c in jobs.catalog()}
        self.assertTrue(cat["cleanup_tracked"]["destructive"])
        self.assertTrue(cat["cleanup_downloads"]["destructive"])
        self.assertFalse(cat["downloader"]["destructive"])

    def test_state_idle_for_unstarted(self):
        st = jobs.state("searcher")
        self.assertEqual(st["status"], "idle")
        self.assertEqual(st["lines"], [])


if __name__ == "__main__":
    unittest.main()
