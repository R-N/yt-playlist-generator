import json
import os
import tempfile
import unittest

from folder_config import resolve_mp3_folders


class FolderConfigTest(unittest.TestCase):
    def setUp(self):
        self.previous = os.environ.pop("MP3_FOLDERS_JSON", None)

    def tearDown(self):
        os.environ.pop("MP3_FOLDERS_JSON", None)
        if self.previous is not None:
            os.environ["MP3_FOLDERS_JSON"] = self.previous

    def test_absent_keeps_defaults(self):
        defaults = ["E:/Music/My Music"]
        self.assertEqual(resolve_mp3_folders(defaults), defaults)

    def test_empty_list_is_explicit(self):
        os.environ["MP3_FOLDERS_JSON"] = "[]"
        self.assertEqual(resolve_mp3_folders(["fallback"]), [])

    def test_normalizes_configured_paths(self):
        with tempfile.TemporaryDirectory() as root:
            os.environ["MP3_FOLDERS_JSON"] = json.dumps([os.path.join(root, "nested", "..")])
            self.assertEqual(resolve_mp3_folders([]), [os.path.normcase(os.path.realpath(root))])

    def test_invalid_values_fail_without_fallback(self):
        for value in ("not json", "{}", '["ok", 1]', '[""]', '["   "]', '["relative"]'):
            with self.subTest(value=value):
                os.environ["MP3_FOLDERS_JSON"] = value
                with self.assertRaisesRegex(ValueError, "MP3_FOLDERS_JSON"):
                    resolve_mp3_folders(["fallback"])

    def test_rejects_missing_files_and_overlapping_roots(self):
        with tempfile.TemporaryDirectory() as root:
            child = os.path.join(root, "child")
            os.mkdir(child)
            file_path = os.path.join(root, "file.mp3")
            open(file_path, "w").close()
            for paths in ([os.path.join(root, "missing")], [file_path], [root, child]):
                with self.subTest(paths=paths):
                    os.environ["MP3_FOLDERS_JSON"] = json.dumps(paths)
                    with self.assertRaises(ValueError):
                        resolve_mp3_folders([])

    def test_dedupes_canonical_roots(self):
        with tempfile.TemporaryDirectory() as root:
            os.environ["MP3_FOLDERS_JSON"] = json.dumps([root, os.path.join(root, ".")])
            self.assertEqual(resolve_mp3_folders([]), [os.path.normcase(os.path.realpath(root))])


if __name__ == "__main__":
    unittest.main(verbosity=2)
