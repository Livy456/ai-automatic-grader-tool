"""Tests for :mod:`app.grading.normalization` helpers."""

import unittest

from app.grading.normalization import slice_evidence_for_criterion


class SliceEvidenceTests(unittest.TestCase):
    def test_truncates_large_bundle(self):
        big = {"claims": [{"text": "x" * 50000}]}
        out = slice_evidence_for_criterion(big, "n/a", max_chars=200)
        self.assertIn("truncated", out["evidence"])


if __name__ == "__main__":
    unittest.main()
