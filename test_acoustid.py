"""
Tests for acoustid_enrich.match_confidence (stdlib unittest).

    python -m unittest test_acoustid -v

Only the pure cross-check logic is tested; no fingerprinting or network. The
module imports `acoustid` lazily (inside lookup_acoustid), so importing it here
does not require pyacoustid to be installed.
"""
import unittest

import acoustid_enrich as ae


class MatchConfidenceTest(unittest.TestCase):
    def test_strong_with_high_score_suggests(self):
        conf, suggest = ae.match_confidence(
            "Radiohead", "Creep", "Radiohead", "Creep (Official Video)", 0.92)
        self.assertEqual(conf, "strong")
        self.assertTrue(suggest)

    def test_strong_but_low_score_no_suggest(self):
        conf, suggest = ae.match_confidence(
            "Radiohead", "Creep", "Radiohead", "Creep (Official Video)", 0.30)
        self.assertEqual(conf, "strong")
        self.assertFalse(suggest)        # AcoustID not confident -> don't auto-approve

    def test_missing_score_no_suggest(self):
        conf, suggest = ae.match_confidence(
            "Radiohead", "Creep", "Radiohead", "Creep", None)
        self.assertEqual(conf, "strong")
        self.assertFalse(suggest)

    def test_weak_title_only(self):
        conf, suggest = ae.match_confidence(
            "Utada Hikaru", "First Love", "XYZ Productions", "First Love", 0.9)
        self.assertEqual(conf, "weak")   # title agrees, artist clearly does not
        self.assertFalse(suggest)

    def test_none_when_nothing_agrees(self):
        conf, suggest = ae.match_confidence(
            "Utada Hikaru", "First Love", "XYZ", "Totally Unrelated Track", 0.9)
        self.assertEqual(conf, "none")
        self.assertFalse(suggest)

    def test_none_when_no_mb_data(self):
        conf, suggest = ae.match_confidence("", "", "Radiohead", "Creep", 0.9)
        self.assertEqual(conf, "none")
        self.assertFalse(suggest)


if __name__ == "__main__":
    unittest.main(verbosity=2)
